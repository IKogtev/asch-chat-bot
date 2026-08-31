from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.advisor_profile import AdvisorClientProfile, AdvisorProfileField
from agent.advisor_profile_matcher import (
    AdvisorClientTypeDefinition,
    AdvisorClientTypeEvidence,
    AdvisorClientTypeMatch,
    AdvisorClientTypeSelection,
    validate_client_type_selection,
)
from tests.unit.agent._advisor_workbook import (
    client_type_workbook_rows,
    load_client_type_definitions,
)
from utils.client_types import CLIENT_TYPE_CODE_COLUMN


TABLE_PATH = (
    Path(__file__).resolve().parents[3]
    / "kb_storage"
    / "manager"
    / "tables"
    / "typical_client_profiles_active.xlsx"
)
NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def workbook_rows() -> list[dict[str, object]]:
    return client_type_workbook_rows(TABLE_PATH)


def definitions() -> list[AdvisorClientTypeDefinition]:
    return load_client_type_definitions(TABLE_PATH)


def selected_payload(*, confidence: float = 0.9, name: str = "Консервативный"):
    profile = AdvisorClientProfile(
        goal=AdvisorProfileField(
            value="Сохранение капитала",
            source_turn="turn-1",
            updated_at=NOW,
            origin="explicit",
        )
    )
    client_type = next(row for row in definitions() if row.profile_name == name)
    match = AdvisorClientTypeMatch(
        profile_name=name,
        confidence=confidence,
        evidence=(
            AdvisorClientTypeEvidence(
                client_field="goal",
                client_value="Сохранение капитала",
                table_field="client_goal",
                table_value=client_type.attributes["client_goal"],
                source_turn="turn-1",
            ),
        ),
    )
    return profile, match


@pytest.mark.unit
def test_real_workbook_rows_validate_and_parse_all_four_rule_columns() -> None:
    client_types = definitions()

    assert [row.client_type_code for row in client_types] == [
        "CT-001",
        "CT-002",
        "CT-003",
    ]
    assert {row.profile_name for row in client_types} == {
        "Консервативный",
        "Умеренный",
        "Агрессивный",
    }
    assert all(row.required_properties for row in client_types)
    assert all(row.preferred_properties for row in client_types)
    assert all(row.acceptable_compromises for row in client_types)
    assert all(row.contraindications for row in client_types)


@pytest.mark.unit
def test_valid_selection_references_supplied_fact_and_loaded_table_field() -> None:
    profile, match = selected_payload()
    selection = AdvisorClientTypeSelection(mode="selected", primary_type=match)

    result = validate_client_type_selection(
        selection,
        client_profile=profile,
        client_types=definitions(),
        minimum_confidence=0.75,
    )

    assert result.primary_type.profile_name == "Консервативный"


@pytest.mark.unit
def test_selection_rejects_unknown_client_type() -> None:
    profile, match = selected_payload(name="Консервативный")
    unknown = match.model_copy(update={"profile_name": "Неизвестный"})

    with pytest.raises(ValueError, match="Unknown selected Client Type"):
        validate_client_type_selection(
            AdvisorClientTypeSelection(mode="selected", primary_type=unknown),
            client_profile=profile,
            client_types=definitions(),
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_selection_rejects_unsupported_evidence() -> None:
    profile, match = selected_payload()
    invalid_evidence = match.evidence[0].model_copy(
        update={"client_value": "Максимальный рост"}
    )
    invalid_match = match.model_copy(update={"evidence": (invalid_evidence,)})

    with pytest.raises(ValueError, match="does not match client field"):
        validate_client_type_selection(
            AdvisorClientTypeSelection(mode="selected", primary_type=invalid_match),
            client_profile=profile,
            client_types=definitions(),
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_selection_rejects_evidence_with_an_unmapped_table_field() -> None:
    profile, match = selected_payload()
    client_type = next(
        row for row in definitions() if row.profile_name == "Консервативный"
    )
    invalid_evidence = match.evidence[0].model_copy(
        update={
            "table_field": "currency",
            "table_value": client_type.attributes["currency"],
        }
    )
    invalid_match = match.model_copy(update={"evidence": (invalid_evidence,)})

    with pytest.raises(ValueError, match="cannot support"):
        validate_client_type_selection(
            AdvisorClientTypeSelection(mode="selected", primary_type=invalid_match),
            client_profile=profile,
            client_types=definitions(),
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_low_confidence_selection_requires_one_clarification_question() -> None:
    profile, match = selected_payload(confidence=0.5)

    with pytest.raises(ValueError, match="requires clarification"):
        validate_client_type_selection(
            AdvisorClientTypeSelection(mode="selected", primary_type=match),
            client_profile=profile,
            client_types=definitions(),
            minimum_confidence=0.75,
        )

    clarification = AdvisorClientTypeSelection(
        mode="needs_clarification",
        primary_type=match,
        missing_fields=["term_months"],
        clarification_question="На какой срок клиент планирует вложение?",
    )
    assert validate_client_type_selection(
        clarification,
        client_profile=profile,
        client_types=definitions(),
        minimum_confidence=0.75,
    ) is clarification


@pytest.mark.unit
def test_selection_accepts_distinct_secondary_type_with_its_own_evidence() -> None:
    profile, primary = selected_payload()
    moderate = next(row for row in definitions() if row.profile_name == "Умеренный")
    secondary = AdvisorClientTypeMatch(
        profile_name="Умеренный",
        confidence=0.8,
        evidence=(
            AdvisorClientTypeEvidence(
                client_field="goal",
                client_value="Сохранение капитала",
                table_field="client_goal",
                table_value=moderate.attributes["client_goal"],
                source_turn="turn-1",
            ),
        ),
    )
    selection = AdvisorClientTypeSelection(
        mode="selected",
        primary_type=primary,
        secondary_type=secondary,
    )

    result = validate_client_type_selection(
        selection,
        client_profile=profile,
        client_types=definitions(),
        minimum_confidence=0.75,
    )

    assert result.secondary_type.profile_name == "Умеренный"


@pytest.mark.unit
def test_selection_rejects_out_of_range_confidence_and_multiple_questions() -> None:
    with pytest.raises(ValidationError):
        AdvisorClientTypeMatch(
            profile_name="Умеренный",
            confidence=1.1,
        )

    profile, match = selected_payload(confidence=0.5)
    selection = AdvisorClientTypeSelection(
        mode="needs_clarification",
        primary_type=match,
        missing_fields=["term_months"],
        clarification_question="Какой срок? Какая сумма?",
    )
    with pytest.raises(ValueError, match="exactly one question"):
        validate_client_type_selection(
            selection,
            client_profile=profile,
            client_types=definitions(),
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_new_approved_workbook_row_requires_no_production_code_change() -> None:
    row = workbook_rows()[0].copy()
    row[CLIENT_TYPE_CODE_COLUMN] = "CT-004"
    row["profile_name"] = "Новый утвержденный профиль"

    definition = AdvisorClientTypeDefinition.from_mapping(row)

    assert definition.client_type_code == "CT-004"
    assert definition.profile_name == "Новый утвержденный профиль"
