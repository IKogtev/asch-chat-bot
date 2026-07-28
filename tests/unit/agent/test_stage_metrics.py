import types

import pytest

from agent.stage_metrics import (
    STAGE_METRICS_STATE_KEY,
    build_timing_payload,
    event_has_model_output,
    extract_usage_tokens,
    flatten_stage_metrics,
    record_stage_metrics,
    stage_name_from_log_label,
)


@pytest.mark.unit
def test_stage_name_from_log_label() -> None:
    assert stage_name_from_log_label("owasp_result_json") == "owasp"
    assert stage_name_from_log_label("dispatcher_result_json") == "dispatcher"
    assert stage_name_from_log_label("doc_search_result_json") == "doc_search"
    assert stage_name_from_log_label("kb_answer_result_json") == "kb_answer"
    assert stage_name_from_log_label("product_info_result_json") == "product_info"
    assert stage_name_from_log_label("product_filter_result_json") == "product_filter"
    assert stage_name_from_log_label("unknown") is None


@pytest.mark.unit
def test_extract_usage_tokens_from_object_and_dict() -> None:
    usage = types.SimpleNamespace(prompt_token_count=12, candidates_token_count=7)
    assert extract_usage_tokens(usage) == (12, 7)
    assert extract_usage_tokens({"prompt_token_count": 3, "candidates_token_count": 2}) == (3, 2)
    assert extract_usage_tokens(None) == (0, 0)


@pytest.mark.unit
def test_event_has_model_output_ignores_thoughts() -> None:
    thought = types.SimpleNamespace(text="скрыто", thought=True)
    visible = types.SimpleNamespace(text="ok")
    assert event_has_model_output(
        types.SimpleNamespace(content=types.SimpleNamespace(parts=[thought]))
    ) is False
    assert event_has_model_output(
        types.SimpleNamespace(content=types.SimpleNamespace(parts=[thought, visible]))
    ) is True
    assert event_has_model_output(
        types.SimpleNamespace(
            content=types.SimpleNamespace(
                parts=[types.SimpleNamespace(function_call={"name": "kb_search"})]
            )
        )
    ) is True


@pytest.mark.unit
def test_record_stage_metrics_accumulates_retries() -> None:
    state: dict = {}
    record_stage_metrics(
        state,
        "doc_search",
        ms=100,
        ttft_ms=40,
        input_tokens=10,
        output_tokens=5,
        tool_calls=2,
        model_turns=1,
    )
    record_stage_metrics(
        state,
        "doc_search",
        ms=50,
        ttft_ms=20,
        input_tokens=4,
        output_tokens=2,
        tool_calls=1,
        model_turns=1,
    )

    assert state[STAGE_METRICS_STATE_KEY]["doc_search"] == {
        "ms": 150,
        "ttft_ms": 40,
        "input_tokens": 14,
        "output_tokens": 7,
        "tool_calls": 3,
        "model_turns": 2,
    }


@pytest.mark.unit
def test_flatten_and_build_timing_payload() -> None:
    flat = flatten_stage_metrics(
        {
            "owasp": {
                "ms": 10,
                "ttft_ms": 3,
                "input_tokens": 1,
                "output_tokens": 2,
                "tool_calls": 0,
                "model_turns": 1,
            },
        },
        route="kb_answer",
        intent="faq",
    )
    assert flat == {
        "owasp_ms": 10,
        "owasp_ttft_ms": 3,
        "owasp_input_tokens": 1,
        "owasp_output_tokens": 2,
        "owasp_tool_calls": 0,
        "owasp_model_turns": 1,
        "route": "kb_answer",
        "intent": "faq",
    }

    payload = build_timing_payload(
        {
            STAGE_METRICS_STATE_KEY: {
                "dispatcher": {
                    "ms": 55,
                    "ttft_ms": 12,
                    "input_tokens": 8,
                    "output_tokens": 3,
                    "tool_calls": 0,
                    "model_turns": 1,
                }
            },
            "_dispatcher_result_parsed": {"route": "doc_search", "intent": "doc_search"},
        }
    )
    assert payload["dispatcher_ms"] == 55
    assert payload["dispatcher_tool_calls"] == 0
    assert payload["dispatcher_model_turns"] == 1
    assert payload["route"] == "doc_search"
    assert payload["intent"] == "doc_search"
