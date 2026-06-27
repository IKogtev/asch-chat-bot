"""Scenario JSON loading and plan 004 checks."""
from pathlib import Path

from tester.dialogues.run_dialogues import load_scenario

REPO_ROOT = Path(__file__).resolve().parents[3]
SCENARIOS_DIR = REPO_ROOT / "tester" / "dialogues" / "scenarios"


def test_load_plan004_name_budget_scenario() -> None:
    path = SCENARIOS_DIR / "12_name_budget.json"
    scenario = load_scenario(path)
    assert scenario.id == "real-12"
    assert scenario.max_name_in_replies == 1
    assert scenario.turns[1].expect_not_contains == ("Дмитрий",)
    assert "plan004" in scenario.tags


def test_load_plan004_route_intent_expectations() -> None:
    path = SCENARIOS_DIR / "14_product_kit_followup.json"
    scenario = load_scenario(path)
    assert scenario.turns[0].expect_route == "product_selection"
    assert scenario.turns[0].expect_intent == "product_filter"
    assert scenario.turns[2].expect_intent == "product_kit"


def test_all_plan004_scenarios_exist() -> None:
    ids = []
    for path in sorted(SCENARIOS_DIR.glob("1[2-8]_*.json")):
        scenario = load_scenario(path)
        ids.append(scenario.id)
        assert "plan004" in scenario.tags
    assert ids == [
        "real-12",
        "real-13",
        "real-14",
        "real-15",
        "real-16",
        "real-17",
        "real-18",
    ]
