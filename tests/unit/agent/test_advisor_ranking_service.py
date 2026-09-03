from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from agent.advisor_ranking_service import (
    ACTIVE_PRODUCT_STATUS,
    BELOW_MINIMUM_SCORE,
    CONTRAINDICATED_PROPERTY,
    INACTIVE_PRODUCT,
    REQUIRED_PROPERTY_MISMATCH,
    AdvisorProductFacts,
    AdvisorRankingService,
    AdvisorScoringPolicy,
)
from agent.advisor_profile_matcher import (
    AdvisorClientTypeDefinition,
    AdvisorSelectedClientType,
)
from tests.unit.agent._advisor_workbook import (
    load_client_type_definitions,
    read_first_xlsx_sheet,
)


PRODUCTS_PATH = (
    Path(__file__).resolve().parents[3]
    / "kb_storage"
    / "manager"
    / "tables"
    / "products_active.xlsx"
)
CLIENT_TYPES_PATH = PRODUCTS_PATH.with_name("typical_client_profiles_active.xlsx")


def definitions() -> list[AdvisorClientTypeDefinition]:
    return load_client_type_definitions(CLIENT_TYPES_PATH)


def selected(definition: AdvisorClientTypeDefinition) -> AdvisorSelectedClientType:
    return AdvisorSelectedClientType(definition=definition, confidence=0.9)


def policy(**overrides) -> AdvisorScoringPolicy:
    values = {
        "version": "test-pilot-v1",
        "preferred_weight": Decimal("100"),
        "compromise_penalty": Decimal("20"),
        "minimum_score": Decimal("0"),
        "diversity_max_score_gap": Decimal("25"),
        "diversity_max_per_family": 1,
    }
    values.update(overrides)
    return AdvisorScoringPolicy(**values)


def product(
    code: str,
    *,
    family: str | None = None,
    priority: int = 100,
    **attributes,
) -> AdvisorProductFacts:
    is_active = attributes.pop("is_active", ACTIVE_PRODUCT_STATUS)
    return AdvisorProductFacts(
        code=code,
        name=f"Product {code}",
        is_active=is_active,
        attributes=attributes,
        family=family,
        tie_break_priority=priority,
    )


def moderate_product_attributes() -> dict[str, object]:
    return {
        "capital_loss_risk": "Есть риск",
        "product_risk_level": "Средний",
        "income": "Не гарантирован",
        "currency": "Рубли",
        "product_type": "Unit Linked",
        "term": "Условно-бессрочный",
        "liquidity": "Высокая",
        "contribution_type": "Единоразово + возможны пополнения",
        "payout_type": "Ежеквартальные выплаты",
    }


@pytest.mark.unit
def test_real_workbook_client_type_filters_real_product_rows() -> None:
    conservative = next(
        row for row in definitions() if row.profile_name == "Консервативный"
    )
    source_rows = [
        row
        for row in read_first_xlsx_sheet(PRODUCTS_PATH)
        if row.get("code") and row.get("code") != "#"
    ]
    products = [
        AdvisorProductFacts(
            code=str(row["code"]),
            name=str(row["name"]),
            is_active=str(row["is_active"]),
            attributes={
                key: value
                for key, value in row.items()
                if key not in {"code", "name", "is_active"}
            },
        )
        for row in source_rows
    ]

    result = AdvisorRankingService(policy()).rank(
        products=products,
        selected_client_type=selected(conservative),
    )

    assert result.primary_client_type == "Консервативный"
    assert result.accepted_candidates
    assert len(result.top_products) <= 3
    assert all(
        sum(
            (component.points for component in ranked.score_components),
            Decimal("0"),
        )
        == ranked.score
        for ranked in result.accepted_candidates
    )
    accepted_identities = {
        (
            ranked.product.code,
            ranked.product.name,
            ranked.product.is_active,
        )
        for ranked in result.accepted_candidates
    }
    assert accepted_identities.isdisjoint(
        (
            exclusion.product_code,
            exclusion.product_name,
            exclusion.product_status,
        )
        for exclusion in result.excluded_candidates
    )


@pytest.mark.unit
def test_required_and_contraindicated_rules_return_stable_exclusion_codes() -> None:
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    attributes = moderate_product_attributes()
    attributes.update(
        product_risk_level="Высокий",
        liquidity="Низкая",
    )

    result = AdvisorRankingService(policy()).rank(
        products=[product("X1", **attributes)],
        selected_client_type=selected(moderate),
    )

    codes = [item.code for item in result.excluded_candidates]
    assert REQUIRED_PROPERTY_MISMATCH in codes
    assert CONTRAINDICATED_PROPERTY in codes
    assert not result.top_products


@pytest.mark.unit
@pytest.mark.parametrize("inactive_status", ["Архивный", "действующий"])
def test_inactive_status_product_is_excluded_before_ranking(
    inactive_status: str,
) -> None:
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    attributes = moderate_product_attributes()

    result = AdvisorRankingService(policy()).rank(
        products=[
            product("ACTIVE", is_active=ACTIVE_PRODUCT_STATUS, **attributes),
            product("INACTIVE", is_active=inactive_status, **attributes),
        ],
        selected_client_type=selected(moderate),
    )

    assert [item.product.code for item in result.top_products] == ["ACTIVE"]
    assert any(
        item.product_code == "INACTIVE" and item.code == INACTIVE_PRODUCT
        for item in result.excluded_candidates
    )


@pytest.mark.unit
def test_preferred_properties_and_compromises_have_bounded_soft_effects() -> None:
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    full_fit = moderate_product_attributes()
    compromise = moderate_product_attributes()
    compromise["payout_type"] = "Без выплат"

    result = AdvisorRankingService(policy()).rank(
        products=[product("P1", **full_fit), product("P2", **compromise)],
        selected_client_type=selected(moderate),
    )

    ranked = {item.product.code: item for item in result.accepted_candidates}
    assert ranked["P1"].score == Decimal("100.00")
    assert ranked["P2"].score < ranked["P1"].score
    assert ranked["P2"].compromises


@pytest.mark.unit
def test_ties_use_priority_then_product_code() -> None:
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    attributes = moderate_product_attributes()

    result = AdvisorRankingService(
        policy(diversity_max_per_family=3)
    ).rank(
        products=[
            product("B", priority=20, **attributes),
            product("C", priority=10, **attributes),
            product("A", priority=10, **attributes),
        ],
        selected_client_type=selected(moderate),
    )

    assert [item.product.code for item in result.top_products] == ["A", "C", "B"]


@pytest.mark.unit
def test_diversity_replaces_duplicate_family_only_within_score_gap() -> None:
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    full = moderate_product_attributes()
    duplicate = {**full, "term": "Среднесрочный"}
    family_b = {**full, "payout_type": "Без выплат"}
    family_c = {
        **full,
        "product_type": "Квазидепозит",
        "term": "Среднесрочный",
    }

    result = AdvisorRankingService(policy()).rank(
        products=[
            product("P1", family="A", **full),
            product("P2", family="A", **duplicate),
            product("P3", family="B", **family_b),
            product("P4", family="C", **family_c),
        ],
        selected_client_type=selected(moderate),
    )

    assert [item.product.code for item in result.top_products] == ["P1", "P3", "P4"]
    assert result.diversity_replacements
    assert result.diversity_replacements[0].deferred_product_code == "P2"


@pytest.mark.unit
def test_focus_and_kv_attributes_do_not_affect_ranking() -> None:
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    first = {**moderate_product_attributes(), "focus": "Да", "kv": 999999}
    second = {**moderate_product_attributes(), "focus": "Нет", "kv": 0}

    result = AdvisorRankingService(
        policy(diversity_max_per_family=2)
    ).rank(
        products=[product("P1", **first), product("P2", **second)],
        selected_client_type=selected(moderate),
    )

    assert result.accepted_candidates[0].score == result.accepted_candidates[1].score
    assert [item.product.code for item in result.top_products] == ["P1", "P2"]


@pytest.mark.unit
def test_minimum_score_excludes_weak_candidates_without_padding_top_three() -> None:
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    full = moderate_product_attributes()
    weak = {
        **full,
        "product_type": "Квазидепозит",
        "term": "Среднесрочный",
        "contribution_type": "Единоразовый",
    }

    result = AdvisorRankingService(
        policy(minimum_score=Decimal("70"), diversity_max_per_family=3)
    ).rank(
        products=[product("P1", **full), product("P2", **weak)],
        selected_client_type=selected(moderate),
    )

    assert [item.product.code for item in result.top_products] == ["P1"]
    assert any(
        item.product_code == "P2" and item.code == BELOW_MINIMUM_SCORE
        for item in result.excluded_candidates
    )


@pytest.mark.unit
def test_repeated_ranking_is_byte_for_byte_deterministic() -> None:
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    service = AdvisorRankingService(policy(diversity_max_per_family=3))
    products = [
        product("P2", **moderate_product_attributes()),
        product("P1", **moderate_product_attributes()),
    ]

    first = service.rank(products=products, selected_client_type=selected(moderate))
    second = service.rank(products=products, selected_client_type=selected(moderate))

    assert first.model_dump_json() == second.model_dump_json()
