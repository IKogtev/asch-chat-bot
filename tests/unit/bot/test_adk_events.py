import pytest

from bot.services.adk_events import (
    extract_bot_action,
    extract_selected_product,
    extract_timing,
)


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


@pytest.mark.unit
def test_extract_selected_product_from_dialog_context() -> None:
    events = [
        {
            "author": "root_agent",
            "actions": {
                "stateDelta": {
                    "last_product": "Старый продукт (код 1111)",
                    "_product_dialog_context": {
                        "selected_product": {
                            "code": "2832",
                            "name": "Fort Knox",
                            "folder_kit": "Fort Knox (2832)",
                        }
                    },
                }
            },
        }
    ]

    assert extract_selected_product(events) == {
        "code": "2832",
        "name": "Fort Knox",
    }


@pytest.mark.unit
def test_extract_selected_product_ignores_stale_last_product() -> None:
    events = [
        {
            "author": "root_agent",
            "actions": {
                "stateDelta": {
                    "last_product": "Unit Linked Стратегия роста (код 7698)"
                }
            },
        }
    ]

    assert extract_selected_product(events) is None


@pytest.mark.unit
def test_extract_selected_product_none_when_nothing_selected() -> None:
    events = [
        {
            "author": "root_agent",
            "actions": {
                "stateDelta": {
                    "last_product": "Fort Knox 1 год (код 8914)",
                    "_product_dialog_context": {
                        "last_mode": "product_filter",
                        "products": [{"code": "8914"}, {"code": "8942"}],
                        "selected_product": None,
                    },
                }
            },
        }
    ]

    assert extract_selected_product(events) is None


@pytest.mark.unit
def test_extract_timing_from_state_delta() -> None:
    events = [
        {"author": "owasp_agent", "actions": {"stateDelta": {}}},
        {
            "author": "root_agent",
            "actions": {
                "stateDelta": {
                    "_timing": {
                        "owasp_ms": 100,
                        "owasp_ttft_ms": 30,
                        "owasp_input_tokens": 11,
                        "owasp_output_tokens": 4,
                        "route": "kb_answer",
                        "intent": "faq",
                    }
                }
            },
        },
    ]

    assert extract_timing(events) == {
        "owasp_ms": 100,
        "owasp_ttft_ms": 30,
        "owasp_input_tokens": 11,
        "owasp_output_tokens": 4,
        "route": "kb_answer",
        "intent": "faq",
    }


@pytest.mark.unit
def test_extract_timing_returns_none_without_timing() -> None:
    assert extract_timing([{"actions": {"stateDelta": {"_bot_action": {"type": "x"}}}}]) is None
