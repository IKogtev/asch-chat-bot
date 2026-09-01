from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest
from pydantic import ValidationError

from agent.advisor_profile import (
    AdvisorClientProfile,
    AdvisorProfileField,
    merge_advisor_profile,
    reset_advisor_profile,
)


NOW = datetime(2026, 8, 28, tzinfo=timezone.utc)


def fact(value, *, turn: str = "turn-1", origin: str = "explicit"):
    return AdvisorProfileField(
        value=value,
        source_turn=turn,
        updated_at=NOW,
        origin=origin,
    )


@pytest.mark.unit
def test_profile_validates_typed_values_and_provenance() -> None:
    profile = AdvisorClientProfile(
        client_age=fact(42),
        term_months=fact(84),
        contribution_amount=fact(Decimal("500000")),
        currency=fact("Рубли"),
    )

    assert profile.client_age.value == 42
    assert profile.currency.explicit is True
    assert profile.explicit_values()["term_months"] == 84


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("client_age", 121),
        ("term_months", 0),
        ("contribution_amount", Decimal("-1")),
        ("currency", " "),
    ],
)
def test_profile_rejects_invalid_values(field_name: str, value) -> None:
    with pytest.raises(ValidationError):
        AdvisorClientProfile(**{field_name: fact(value)})


@pytest.mark.unit
def test_merge_adds_new_facts_without_losing_existing_values() -> None:
    current = AdvisorClientProfile(goal=fact("Накопление"))
    patch = AdvisorClientProfile(term_months=fact(60, turn="turn-2"))

    result = merge_advisor_profile(current, patch)

    assert result.profile.goal.value == "Накопление"
    assert result.profile.term_months.value == 60
    assert result.changed_fields == ["term_months"]


@pytest.mark.unit
def test_explicit_fact_replaces_an_inferred_value() -> None:
    current = AdvisorClientProfile(
        liquidity_need=fact("Средняя", origin="inferred")
    )
    patch = AdvisorClientProfile(
        liquidity_need=fact("Высокая", turn="turn-2")
    )

    result = merge_advisor_profile(current, patch)

    assert result.profile.liquidity_need.value == "Высокая"
    assert not result.conflicts


@pytest.mark.unit
def test_conflicting_explicit_values_require_clarification() -> None:
    current = AdvisorClientProfile(term_months=fact(60))
    patch = AdvisorClientProfile(term_months=fact(84, turn="turn-2"))

    result = merge_advisor_profile(current, patch)

    assert result.requires_clarification is True
    assert result.profile.term_months.value == 60
    assert result.conflicts[0].field_name == "term_months"


@pytest.mark.unit
def test_explicit_correction_replaces_the_old_value() -> None:
    current = AdvisorClientProfile(term_months=fact(60))
    patch = AdvisorClientProfile(term_months=fact(84, turn="turn-2"))

    result = merge_advisor_profile(
        current,
        patch,
        correction_fields={"term_months"},
    )

    assert result.profile.term_months.value == 84
    assert not result.conflicts


@pytest.mark.unit
def test_reset_for_new_client_drops_the_previous_profile() -> None:
    initial = AdvisorClientProfile(goal=fact("Защита", turn="turn-9"))

    reset = reset_advisor_profile(initial)

    assert reset.goal.value == "Защита"
    assert reset.term_months is None
