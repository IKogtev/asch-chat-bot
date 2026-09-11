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
        client_goal=fact("Накопление"),
        investment_horizon=fact("7 лет"),
        dependents=fact("Есть"),
        min_amount=fact(Decimal("500000")),
        age=fact(45),
    )

    assert profile.client_goal.value == "Накопление"
    assert profile.dependents.explicit is True
    assert profile.explicit_values()["investment_horizon"] == "7 лет"
    assert profile.age.value == 45


@pytest.mark.unit
@pytest.mark.parametrize(
    ("field_name", "value"),
    [
        ("client_goal", " "),
        ("investment_horizon", " "),
        ("dependents", " "),
        ("expected_return_percent", " "),
        ("min_amount", Decimal("-1")),
        ("age", -1),
        ("age", 121),
        ("age", "45"),
        ("age", 45.5),
        ("age", True),
    ],
)
def test_profile_rejects_invalid_values(field_name: str, value) -> None:
    with pytest.raises(ValidationError):
        AdvisorClientProfile(**{field_name: fact(value)})


@pytest.mark.unit
def test_merge_adds_new_facts_without_losing_existing_values() -> None:
    current = AdvisorClientProfile(client_goal=fact("Накопление"))
    patch = AdvisorClientProfile(investment_horizon=fact("5 лет", turn="turn-2"))

    result = merge_advisor_profile(current, patch)

    assert result.profile.client_goal.value == "Накопление"
    assert result.profile.investment_horizon.value == "5 лет"
    assert result.changed_fields == ["investment_horizon"]


@pytest.mark.unit
def test_explicit_fact_replaces_an_inferred_value() -> None:
    current = AdvisorClientProfile(
        dependents=fact("Нет", origin="inferred")
    )
    patch = AdvisorClientProfile(
        dependents=fact("Есть", turn="turn-2")
    )

    result = merge_advisor_profile(current, patch)

    assert result.profile.dependents.value == "Есть"
    assert not result.conflicts


@pytest.mark.unit
def test_inferred_fact_does_not_replace_an_explicit_value() -> None:
    current = AdvisorClientProfile(
        dependents=fact("Есть")
    )
    patch = AdvisorClientProfile(
        dependents=fact("Нет", turn="turn-2", origin="inferred")
    )

    result = merge_advisor_profile(current, patch)

    assert result.profile.dependents.value == "Есть"
    assert result.changed_fields == []
    assert not result.conflicts


@pytest.mark.unit
def test_conflicting_explicit_values_require_clarification() -> None:
    current = AdvisorClientProfile(investment_horizon=fact("5 лет"))
    patch = AdvisorClientProfile(investment_horizon=fact("7 лет", turn="turn-2"))

    result = merge_advisor_profile(current, patch)

    assert result.requires_clarification is True
    assert result.profile.investment_horizon.value == "5 лет"
    assert result.conflicts[0].field_name == "investment_horizon"


@pytest.mark.unit
def test_explicit_correction_replaces_the_old_value() -> None:
    current = AdvisorClientProfile(investment_horizon=fact("5 лет"))
    patch = AdvisorClientProfile(investment_horizon=fact("7 лет", turn="turn-2"))

    result = merge_advisor_profile(
        current,
        patch,
        correction_fields={"investment_horizon"},
    )

    assert result.profile.investment_horizon.value == "7 лет"
    assert not result.conflicts


@pytest.mark.unit
def test_reset_for_new_client_drops_the_previous_profile() -> None:
    initial = AdvisorClientProfile(client_goal=fact("Защита", turn="turn-9"))

    reset = reset_advisor_profile(initial)

    assert reset.client_goal.value == "Защита"
    assert reset.investment_horizon is None
