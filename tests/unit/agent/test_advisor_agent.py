from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from agent.advisor_profile import AdvisorClientProfile, AdvisorProfileField
from agent.advisor_profile_matcher import (
    AdvisorClientTypeEvidence,
    AdvisorSelectedClientType,
)
from agent.advisor_ranking_service import (
    AdvisorProductFacts,
    AdvisorRankedProduct,
    AdvisorRankingResult,
    AdvisorScoreComponent,
)
from agent.agents import advisor_content_agent, advisor_format_agent
from agent.agents.advisor_content_agent import ADVISOR_TOOL_FILTER
from agent.agents.advisor_contract import (
    AdvisorContentResult,
    validate_advisor_content_result,
    validate_advisor_final_result,
)
from tests.unit.agent._advisor_workbook import load_client_type_definitions


REPO_ROOT = Path(__file__).resolve().parents[3]
CLIENT_TYPES_PATH = (
    REPO_ROOT
    / "kb_storage"
    / "manager"
    / "tables"
    / "typical_client_profiles_active.xlsx"
)
NOW = datetime(2026, 8, 31, tzinfo=timezone.utc)


def _definitions():
    """Загружает реальные Client Types тем же Phase 1 fixture-путем."""
    return load_client_type_definitions(CLIENT_TYPES_PATH)


def _profile() -> AdvisorClientProfile:
    """Создает минимальный явный профиль для доказательства выбора типа."""
    return AdvisorClientProfile(
        goal=AdvisorProfileField(
            value="Сохранение капитала",
            source_turn="turn-1",
            updated_at=NOW,
            origin="explicit",
        )
    )


def _selected(*, profile_name: str = "Консервативный", confidence: float = 0.9):
    """Создает единственный выбранный тип из реальной строки workbook."""
    row = next(item for item in _definitions() if item.profile_name == "Консервативный")
    definition = row.model_copy(update={"profile_name": profile_name})
    return AdvisorSelectedClientType(
        definition=definition,
        confidence=confidence,
        evidence=(
            AdvisorClientTypeEvidence(
                client_field="goal",
                client_value="Сохранение капитала",
                table_field="client_goal",
                table_value=row.attributes["client_goal"],
                source_turn="turn-1",
            ),
        ),
    )
def _context() -> dict[str, object]:
    """Возвращает validation context с SQL только текущего запуска."""
    return {
        "advisor_client_profile": AdvisorClientProfile(),
        "advisor_minimum_client_type_confidence": 0.75,
        "advisor_source_turn": "turn-1",
        "advisor_updated_at": NOW.isoformat(),
        "_advisor_executed_sql": [
            "SELECT * FROM typical_client_profiles",
            (
                "SELECT code, name, is_active, capital_loss_risk, "
                "product_risk_level, income, currency, product_type, term, "
                "liquidity, contribution_type, payout_type FROM products "
                "WHERE is_active = 'Действующий'"
            ),
        ],
    }


def _candidate_payload() -> dict[str, object]:
    """Создает полный candidates payload на реальных Client Types строках."""
    definitions = _definitions()
    selected = next(row for row in definitions if row.profile_name == "Консервативный")
    required_columns = {
        rule.product_column
        for rules in (
            selected.required_properties,
            selected.preferred_properties,
            selected.acceptable_compromises,
            selected.contraindications,
        )
        for rule in rules
        if rule.product_column != "is_active"
    }
    return {
        "mode": "candidates",
        "profile_patch": _profile().model_dump(mode="json"),
        "selected_client_type": _selected().model_dump(mode="json"),
        "missing_fields": [],
        "clarification_question": None,
        "products": [
            {
                "code": "P-1",
                "name": "Тестовый продукт",
                "is_active": "Действующий",
                "attributes": {column: "Проверенное значение" for column in required_columns},
                "family": "Тестовое семейство",
                "tie_break_priority": 10,
            }
        ],
        "no_data_reason": None,
    }


def _ranking_result(*, is_active: str = "Действующий") -> AdvisorRankingResult:
    """Создает детерминированный TOP для проверки format contract."""
    ranked = []
    for index, code in enumerate(("P-1", "P-2"), start=1):
        ranked.append(
            AdvisorRankedProduct(
                product=AdvisorProductFacts(
                    code=code,
                    name=f"Продукт {index}",
                    is_active=is_active,
                ),
                score=Decimal(str(100 - index)),
                score_components=(
                    AdvisorScoreComponent(
                        criterion="preferred:1:test",
                        points=Decimal(str(100 - index)),
                        matched=True,
                    ),
                ),
            )
        )
    return AdvisorRankingResult(
        primary_client_type="Консервативный",
        accepted_candidates=tuple(ranked),
        excluded_candidates=(),
        top_products=tuple(ranked),
        scoring_policy_version="test-pilot-v1",
    )


@pytest.mark.unit
def test_advisor_content_contract_accepts_grounded_candidates() -> None:
    result = validate_advisor_content_result(_candidate_payload(), _context())

    assert result["mode"] == "candidates"
    assert result["selected_client_type"]["definition"]["profile_name"] == "Консервативный"
    assert result["products"][0]["code"] == "P-1"


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["unsupported", "recommendation", "product_filter"])
def test_advisor_content_contract_rejects_unsupported_modes(mode: str) -> None:
    payload = _candidate_payload()
    payload["mode"] = mode

    with pytest.raises(ValueError, match="Input should be"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_rejects_description_row() -> None:
    payload = _candidate_payload()
    payload["selected_client_type"] = _selected(
        profile_name="Тип профиля"
    ).model_dump(mode="json")

    with pytest.raises(ValueError, match="description row"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_rejects_unsupported_evidence() -> None:
    payload = _candidate_payload()
    selected = _selected()
    evidence = selected.evidence[0].model_copy(
        update={"client_value": "Несуществующий факт"}
    )
    selected = selected.model_copy(update={"evidence": (evidence,)})
    payload["selected_client_type"] = selected.model_dump(mode="json")

    with pytest.raises(ValueError, match="does not match client field"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_rejects_invalid_confidence() -> None:
    payload = _candidate_payload()
    payload["selected_client_type"]["confidence"] = 1.1

    with pytest.raises(ValueError, match="less than or equal to 1"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
@pytest.mark.parametrize("field", ["source_turn", "updated_at"])
def test_advisor_content_contract_rejects_fabricated_provenance(field: str) -> None:
    payload = _candidate_payload()
    payload["profile_patch"]["goal"][field] = (
        "another-turn" if field == "source_turn" else "2026-08-30T00:00:00Z"
    )

    with pytest.raises(ValueError, match="current advisor"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_rejects_logged_invalid_rule_and_provenance_shape() -> None:
    payload = _candidate_payload()
    payload["profile_patch"]["goal"]["source_turn"] = None
    payload["profile_patch"]["goal"]["updated_at"] = None
    payload["selected_client_type"]["definition"]["required_properties"] = [
        "Статус: Действующий"
    ]
    payload["selected_client_type"]["evidence"][0]["source_turn"] = None

    with pytest.raises(ValueError, match="validation errors"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_rejects_age_with_units() -> None:
    payload = _candidate_payload()
    payload["profile_patch"] = {
        "client_age": {
            "value": "45 лет",
            "source_turn": "turn-1",
            "updated_at": NOW.isoformat(),
            "origin": "explicit",
        }
    }

    with pytest.raises(ValueError, match="valid integer"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_requires_one_valid_clarification() -> None:
    payload = _candidate_payload()
    payload.update(
        mode="needs_clarification",
        selected_client_type=None,
        missing_fields=["term_months"],
        clarification_question="На какой срок планируется вложение? Когда?",
        products=[],
    )

    with pytest.raises(ValueError, match="exactly one question"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_uses_client_profile_names_for_missing_fields() -> None:
    payload = _candidate_payload()
    payload.update(
        mode="needs_clarification",
        profile_patch={},
        selected_client_type=None,
        missing_fields=["goal"],
        clarification_question="Какова финансовая цель клиента?",
        products=[],
    )

    result = validate_advisor_content_result(payload, _context())

    assert result["missing_fields"] == ["goal"]

    payload["missing_fields"] = ["client_goal"]
    with pytest.raises(ValueError, match="Unknown missing client field: 'client_goal'"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_requires_current_run_sql_for_both_sources() -> None:
    with pytest.raises(ValueError, match="typical_client_profiles"):
        validate_advisor_content_result(
            _candidate_payload(),
            {
                **_context(),
                "_advisor_executed_sql": [
                    "SELECT code, name, is_active FROM products "
                    "WHERE is_active = 'Действующий'"
                ],
            },
        )

    with pytest.raises(ValueError, match="products"):
        validate_advisor_content_result(
            _candidate_payload(),
            {
                **_context(),
                "_advisor_executed_sql": ["SELECT * FROM typical_client_profiles"],
            },
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("sql", "error"),
    [
        (
            "SELECT * FROM products WHERE is_active = 'Действующий'",
            "wildcard projection",
        ),
        (
            "SELECT code, name, is_active FROM products",
            "active status",
        ),
    ],
)
def test_advisor_content_contract_requires_narrow_active_products_sql(
    sql: str,
    error: str,
) -> None:
    context = {
        **_context(),
        "_advisor_executed_sql": [
            "SELECT * FROM typical_client_profiles",
            sql,
        ],
    }

    with pytest.raises(ValueError, match=error):
        validate_advisor_content_result(_candidate_payload(), context)


@pytest.mark.unit
def test_advisor_content_contract_rejects_inactive_candidates() -> None:
    payload = _candidate_payload()
    payload["products"][0]["is_active"] = "Архивный"

    with pytest.raises(ValueError, match="only active products"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_rejects_incomplete_product_identity() -> None:
    payload = _candidate_payload()
    del payload["products"][0]["name"]

    with pytest.raises(ValueError, match="Field required"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_rejects_removed_active_property() -> None:
    payload = _candidate_payload()
    payload["products"][0]["active"] = True

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_content_contract_rejects_unknown_keys() -> None:
    payload = _candidate_payload()
    payload["invented_score"] = 100

    with pytest.raises(ValueError, match="Extra inputs are not permitted"):
        validate_advisor_content_result(payload, _context())


@pytest.mark.unit
def test_advisor_final_contract_preserves_ranked_products() -> None:
    ranking = _ranking_result()
    payload = {
        "mode": "recommendation",
        "message": "Варианты для проверки менеджером.",
        "primary_client_type": "Консервативный",
        "products": [
            {
                "code": item.product.code,
                "name": item.product.name,
                "is_active": item.product.is_active,
            }
            for item in ranking.top_products
        ],
    }

    result = validate_advisor_final_result(
        payload,
        {"advisor_ranking_result": ranking},
    )

    assert [product["code"] for product in result["products"]] == ["P-1", "P-2"]


@pytest.mark.unit
def test_advisor_final_contract_rejects_inactive_ranked_product() -> None:
    ranking = _ranking_result(is_active="Архивный")

    with pytest.raises(ValueError, match="only active products"):
        validate_advisor_final_result(
            {
                "mode": "recommendation",
                "message": "Рекомендация.",
                "primary_client_type": "Консервативный",
                "products": [
                    {
                        "code": item.product.code,
                        "name": item.product.name,
                        "is_active": item.product.is_active,
                    }
                    for item in ranking.top_products
                ],
            },
            {"advisor_ranking_result": ranking},
        )


@pytest.mark.unit
@pytest.mark.parametrize("mutation", ["invent", "reorder"])
def test_advisor_final_contract_rejects_invented_or_reordered_products(
    mutation: str,
) -> None:
    ranking = _ranking_result()
    products = [
        {
            "code": item.product.code,
            "name": item.product.name,
            "is_active": item.product.is_active,
        }
        for item in ranking.top_products
    ]
    if mutation == "invent":
        products[0] = {
            "code": "P-X",
            "name": "Придуманный продукт",
            "is_active": "Действующий",
        }
    else:
        products.reverse()

    with pytest.raises(ValueError, match="exact ranked TOP"):
        validate_advisor_final_result(
            {
                "mode": "recommendation",
                "message": "Рекомендация.",
                "primary_client_type": "Консервативный",
                "products": products,
            },
            {"advisor_ranking_result": ranking},
        )


@pytest.mark.unit
def test_advisor_agent_prompts_and_tool_allowlist_match_phase_2() -> None:
    content_prompt = (
        REPO_ROOT
        / "kb_storage"
        / "prompts"
        / "advisor_content"
        / "advisor_content_agent_prompt.md"
    ).read_text(encoding="utf-8")
    format_prompt = (
        REPO_ROOT
        / "kb_storage"
        / "prompts"
        / "advisor_format"
        / "advisor_format_agent_prompt.md"
    ).read_text(encoding="utf-8")

    assert set(ADVISOR_TOOL_FILTER) == {
        "search_table",
        "search_column",
        "search_analytic",
        "search_objects",
        "execute_sql",
    }
    assert "typical_client_profiles" in content_prompt
    assert "ровно один короткий вопрос" in content_prompt
    assert "Не фильтруй, не оценивай" in content_prompt
    assert "точное значение `Действующий`" in content_prompt
    assert "не используй `SELECT *`" in content_prompt
    assert "WHERE is_active = 'Действующий'" in content_prompt
    assert "dc_entities" in content_prompt
    assert "dc_columns" in content_prompt
    assert "dc_analytics" in content_prompt
    assert '"product_column"' in content_prompt
    assert '"expected_values"' in content_prompt
    assert "JSON-число от 0 до 120" in content_prompt
    assert "{advisor_profile_field_names_json}" in content_prompt
    assert "{advisor_profile_field_names_json}" in (
        advisor_content_agent.ADVISOR_CONTENT_FALLBACK_PROMPT
    )
    assert "Client Types table column name in missing_fields" in (
        advisor_content_agent.ADVISOR_CONTENT_FALLBACK_PROMPT
    )
    assert "имена колонок таблицы Client Types" in content_prompt
    assert "client_types_source" not in content_prompt
    assert "products_source" not in content_prompt
    assert "source_row_identity" not in content_prompt
    assert '"secondary_type":' not in content_prompt
    assert '"secondary_client_type":' not in format_prompt
    assert '"selected_client_type"' in content_prompt
    assert '"definition"' in content_prompt
    assert '"client_type_selection"' not in content_prompt
    assert "исходный порядок `top_products`" in format_prompt
    assert "Запрещено добавлять, удалять, заменять или переставлять продукты" in format_prompt


@pytest.mark.unit
def test_advisor_prompt_examples_match_content_schema() -> None:
    prompt = (
        REPO_ROOT
        / "kb_storage"
        / "prompts"
        / "advisor_content"
        / "advisor_content_agent_prompt.md"
    ).read_text(encoding="utf-8")
    examples = re.findall(
        r"`(?:candidates|needs_clarification|no_data)`:\s*```json\s*(\{.*?\})\s*```",
        prompt,
        flags=re.DOTALL,
    )

    assert len(examples) == 3
    rendered_examples = [
        item.replace("{advisor_source_turn}", "turn-1").replace(
            "{advisor_updated_at}", NOW.isoformat()
        )
        for item in examples
    ]
    assert [
        AdvisorContentResult.model_validate(json.loads(item)).mode
        for item in rendered_examples
    ] == [
        "candidates",
        "needs_clarification",
        "no_data",
    ]


@pytest.mark.unit
def test_advisor_agent_factories_follow_product_agent_conventions(monkeypatch) -> None:
    """Проверяет output keys, tool split, watcher и temperature format agent."""
    watched: list[tuple[str, str]] = []
    monkeypatch.setattr(
        advisor_content_agent,
        "start_prompt_watcher",
        lambda prompt, agent, logger: watched.append((prompt, agent.name)),
    )
    monkeypatch.setattr(
        advisor_format_agent,
        "start_prompt_watcher",
        lambda prompt, agent, logger: watched.append((prompt, agent.name)),
    )

    content = advisor_content_agent.create_advisor_content_agent(model="content-model")
    formatter = advisor_format_agent.create_advisor_format_agent(model="format-model")

    assert content.name == "advisor_content_agent"
    assert content.include_contents == "none"
    assert content.output_key == "advisor_content_result_json"
    assert len(content.tools) == 1
    assert content.tools[0]._mcp_tool_filter == ADVISOR_TOOL_FILTER
    assert content.tools[0]._trace_agent_name == "advisor_content_agent"
    assert content.before_model_callback is advisor_content_agent.before_model_debug_trace
    assert content.after_model_callback is advisor_content_agent.after_model_debug_trace
    assert content.on_model_error_callback is advisor_content_agent.on_model_error_debug_trace
    assert content.before_tool_callback is advisor_content_agent.before_tool_debug_trace
    assert content.after_tool_callback is advisor_content_agent.after_tool_debug_trace
    assert content.on_tool_error_callback is advisor_content_agent.on_tool_error_debug_trace
    assert getattr(content, "output_schema", None) is None
    assert (
        content.generate_content_config.max_output_tokens
        == advisor_content_agent.ADVISOR_MAX_OUTPUT_TOKENS
    )
    assert (
        content.generate_content_config.temperature
        == advisor_content_agent.ADVISOR_TEMPERATURE
    )
    assert formatter.name == "advisor_format_agent"
    assert formatter.include_contents == "none"
    assert formatter.output_key == "advisor_result_json"
    assert formatter.tools == []
    assert formatter.generate_content_config.temperature == 0.0
    assert (
        formatter.generate_content_config.max_output_tokens
        == advisor_format_agent.ADVISOR_MAX_OUTPUT_TOKENS
    )
    assert getattr(formatter, "output_schema", None) is None
    assert watched == [
        ("advisor_content_agent_prompt.md", "advisor_content_agent"),
        ("advisor_format_agent_prompt.md", "advisor_format_agent"),
    ]


@pytest.mark.unit
def test_compose_exposes_advisor_runtime_settings() -> None:
    """Проверяет Advisor settings в обоих Compose runtime-сервисах."""
    compose = (REPO_ROOT / "docker-compose.yaml").read_text(encoding="utf-8")

    assert compose.count("LLM_TRACE_ENABLED=${LLM_TRACE_ENABLED:-true}") == 2
    assert compose.count("LLM_TRACE_AGENTS=${LLM_TRACE_AGENTS:-advisor_content_agent}") == 2

    assert compose.count("ADVISOR_TEMPERATURE=0.5") == 2
    assert (
        compose.count(
            "ADVISOR_MAX_OUTPUT_TOKENS=${ADVISOR_MAX_OUTPUT_TOKENS:-16000}"
        )
        == 1
    )
    assert (
        compose.count(
            "ADVISOR_MAX_OUTPUT_TOKENS=${ADVISOR_MAX_OUTPUT_TOKENS:-6000}"
        )
        == 1
    )
