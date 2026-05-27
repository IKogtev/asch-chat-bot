import pytest

from bot.services.adk_events import extract_bot_action


@pytest.mark.unit
def test_extract_bot_action_from_state_delta() -> None:
    events = [
        {"author": "root_agent", "actions": {"stateDelta": {"x": 1}}},
        {
            "author": "root_agent",
            "actions": {
                "state_delta": {
                    "_bot_action": {
                        "type": "send_product_kit",
                        "product_code": "2832",
                        "product_name": "Fort Knox",
                    }
                }
            },
        },
    ]

    result = extract_bot_action(events)

    assert result == {
        "type": "send_product_kit",
        "product_code": "2832",
        "product_name": "Fort Knox",
    }


@pytest.mark.unit
def test_extract_bot_action_returns_none_without_action() -> None:
    assert extract_bot_action([{"actions": {"stateDelta": {"x": 1}}}]) is None
