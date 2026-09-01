from __future__ import annotations

import importlib.util
import json
import logging
import sys
import types
from pathlib import Path

import pytest


class _CapturingLogger:
    def __init__(self) -> None:
        self.records: list[str] = []
        self.debug_enabled = True

    def isEnabledFor(self, level: int) -> bool:
        return self.debug_enabled and level == logging.DEBUG

    def debug(self, message: str, *args: object) -> None:
        self.records.append(message % args)


def _load_debug_trace_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "debug_trace.py"
    module_name = "test_debug_trace_module"
    capture = _CapturingLogger()

    original_logger_module = sys.modules.get("utils.logger")
    logger_stub = types.ModuleType("utils.logger")
    logger_stub.setup_logger = lambda *args, **kwargs: capture
    sys.modules["utils.logger"] = logger_stub

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        if original_logger_module is None:
            sys.modules.pop("utils.logger", None)
        else:
            sys.modules["utils.logger"] = original_logger_module
    return module, capture


debug_trace, capture = _load_debug_trace_module()


@pytest.fixture(autouse=True)
def _reset_trace(monkeypatch):
    capture.records.clear()
    capture.debug_enabled = True
    monkeypatch.delenv("LLM_TRACE_ENABLED", raising=False)
    monkeypatch.delenv("LLM_TRACE_AGENTS", raising=False)
    monkeypatch.delenv("LLM_TRACE_INCLUDE_CONTENT", raising=False)


@pytest.mark.unit
def test_trace_is_disabled_by_default_and_requires_debug_level(monkeypatch) -> None:
    debug_trace.trace_debug("test", {"value": 1}, agent_name="advisor_content_agent")
    assert capture.records == []

    monkeypatch.setenv("LLM_TRACE_ENABLED", "true")
    capture.debug_enabled = False
    debug_trace.trace_debug("test", {"value": 1}, agent_name="advisor_content_agent")
    assert capture.records == []


@pytest.mark.unit
def test_provider_request_trace_is_structured_and_hides_content(monkeypatch) -> None:
    monkeypatch.setenv("LLM_TRACE_ENABLED", "true")
    summary = debug_trace.summarize_provider_request(
        "provider-model",
        [{"role": "user", "content": "sensitive client request"}],
        {
            "tools": [
                {
                    "type": "function",
                    "function": {"name": "execute_sql", "parameters": {"type": "object"}},
                }
            ],
            "extra_body": {"api_key": "secret", "thinking": {"type": "enabled"}},
        },
    )
    debug_trace.trace_debug(
        "litellm_pre_api_call",
        summary,
        agent_name="advisor_content_agent",
    )

    record = json.loads(capture.records[0].removeprefix("llm_trace "))
    assert record["model"] == "provider-model"
    assert record["tools"][0]["name"] == "execute_sql"
    assert record["messages"][0]["content"]["length"] == 24
    assert "preview" not in record["messages"][0]["content"]
    assert record["extra_body"]["api_key"] == "[REDACTED]"
    assert "sensitive client request" not in capture.records[0]


@pytest.mark.unit
def test_tool_summary_records_sql_shape_without_sql_text() -> None:
    sql = "SELECT code FROM products WHERE is_active = 'Действующий'"
    summary = debug_trace.summarize_tool_args({"sql": sql, "limit": 10})

    assert summary["keys"] == ["limit", "sql"]
    assert summary["sql"]["tables"] == ["products"]
    assert summary["sql"]["length"] == len(sql)
    assert sql not in json.dumps(summary, ensure_ascii=False)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "classification"),
    [
        ("ordinary JSON", "none"),
        ("<tool_call></tool_call>", "empty_tool_tag"),
        ("<tool_call>{bad json}</tool_call>", "invalid_tool_json"),
        (
            '<tool_call>{"name":"execute_sql","arguments":{}}</tool_call>',
            "raw_tool_call_not_converted",
        ),
    ],
)
def test_classify_tool_call_text(text: str, classification: str) -> None:
    assert debug_trace.classify_tool_call_text(text)["classification"] == classification
