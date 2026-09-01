from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from agent.advisor_profile import AdvisorClientProfile, AdvisorProfileField
from agent.advisor_profile_matcher import (
    AdvisorClientTypeDefinition,
    AdvisorClientTypeEvidence,
    AdvisorSelectedClientType,
    validate_selected_client_type,
)
from tests.unit.agent._advisor_workbook import (
    client_type_workbook_rows,
    load_client_type_definitions,
)
from utils.client_types import CLIENT_TYPE_CODE_COLUMN, CLIENT_TYPES_DESCRIPTION_ROW_LABEL


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
    selected = AdvisorSelectedClientType(
        definition=client_type,
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
    return profile, selected


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
    profile, selected = selected_payload()

    result = validate_selected_client_type(
        selected,
        client_profile=profile,
        minimum_confidence=0.75,
    )

    assert result.definition.profile_name == "Консервативный"


@pytest.mark.unit
def test_selection_rejects_description_row() -> None:
    profile, selected = selected_payload()
    invalid_definition = selected.definition.model_copy(
        update={"profile_name": CLIENT_TYPES_DESCRIPTION_ROW_LABEL}
    )

    with pytest.raises(ValueError, match="description row"):
        validate_selected_client_type(
            selected.model_copy(update={"definition": invalid_definition}),
            client_profile=profile,
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_selection_rejects_incomplete_selected_definition() -> None:
    profile, selected = selected_payload()
    attributes = dict(selected.definition.attributes)
    del attributes["notes"]
    invalid_definition = selected.definition.model_copy(
        update={"attributes": attributes}
    )

    with pytest.raises(ValueError, match="incomplete attributes"):
        validate_selected_client_type(
            selected.model_copy(update={"definition": invalid_definition}),
            client_profile=profile,
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_selection_rejects_unsupported_evidence() -> None:
    profile, selected = selected_payload()
    invalid_evidence = selected.evidence[0].model_copy(
        update={"client_value": "Максимальный рост"}
    )
    invalid_selected = selected.model_copy(update={"evidence": (invalid_evidence,)})

    with pytest.raises(ValueError, match="does not match client field"):
        validate_selected_client_type(
            invalid_selected,
            client_profile=profile,
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_selection_rejects_evidence_with_an_unmapped_table_field() -> None:
    profile, selected = selected_payload()
    client_type = next(
        row for row in definitions() if row.profile_name == "Консервативный"
    )
    invalid_evidence = selected.evidence[0].model_copy(
        update={
            "table_field": "currency",
            "table_value": client_type.attributes["currency"],
        }
    )
    invalid_selected = selected.model_copy(update={"evidence": (invalid_evidence,)})

    with pytest.raises(ValueError, match="cannot support"):
        validate_selected_client_type(
            invalid_selected,
            client_profile=profile,
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_low_confidence_selection_requires_clarification() -> None:
    profile, selected = selected_payload(confidence=0.5)

    with pytest.raises(ValueError, match="requires clarification"):
        validate_selected_client_type(
            selected,
            client_profile=profile,
            minimum_confidence=0.75,
        )


@pytest.mark.unit
def test_selection_rejects_secondary_type() -> None:
    _, selected = selected_payload()

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        AdvisorSelectedClientType(
            **selected.model_dump(),
            secondary_type=selected,
        )


@pytest.mark.unit
def test_selection_rejects_out_of_range_confidence() -> None:
    _, selected = selected_payload()

    with pytest.raises(ValidationError):
        AdvisorSelectedClientType(
            definition=selected.definition,
            confidence=1.1,
        )


@pytest.mark.unit
def test_new_approved_workbook_row_requires_no_production_code_change() -> None:
    row = workbook_rows()[0].copy()
    row[CLIENT_TYPE_CODE_COLUMN] = "CT-004"
    row["profile_name"] = "Новый утвержденный профиль"

    definition = AdvisorClientTypeDefinition.from_mapping(row)

    assert definition.client_type_code == "CT-004"
    assert definition.profile_name == "Новый утвержденный профиль"
