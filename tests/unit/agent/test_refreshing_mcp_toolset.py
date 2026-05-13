import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest


def _load_refreshing_mcp_toolset_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "tools" / "refreshing_mcp_toolset.py"
    module_name = "test_refreshing_mcp_toolset_module"

    logger_stub = type(sys)("utils.logger")
    logger_stub.setup_logger = lambda *args, **kwargs: type(
        "Logger",
        (),
        {
            "warning": lambda *a, **k: None,
            "info": lambda *a, **k: None,
            "debug": lambda *a, **k: None,
            "error": lambda *a, **k: None,
        },
    )()

    base_toolset_stub = type(sys)("google.adk.tools.base_toolset")

    class _FakeBaseToolset:
        def __init__(self, *, tool_filter=None, tool_name_prefix=None):
            self.tool_filter = tool_filter
            self.tool_name_prefix = tool_name_prefix

    base_toolset_stub.BaseToolset = _FakeBaseToolset

    mcp_tool_stub = type(sys)("google.adk.tools.mcp_tool")
    mcp_tool_stub.McpToolset = type("McpToolset", (), {})

    sys.modules["utils.logger"] = logger_stub
    sys.modules["google.adk.tools.base_toolset"] = base_toolset_stub
    sys.modules["google.adk.tools.mcp_tool"] = mcp_tool_stub

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


refreshing_module = _load_refreshing_mcp_toolset_module()
RefreshingMcpToolset = refreshing_module.RefreshingMcpToolset
is_mcp_session_error = refreshing_module.is_mcp_session_error


class _FakeMcpToolset:
    def __init__(self, *, outcome, close_errors=None, **kwargs):
        self.outcome = outcome
        self.close_errors = close_errors if close_errors is not None else []
        self.kwargs = kwargs
        self.closed = False
        self.get_tools_calls = 0

    async def get_tools(self, readonly_context=None):
        self.get_tools_calls += 1
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def close(self):
        self.closed = True
        if self.close_errors:
            raise self.close_errors.pop(0)

    def get_auth_config(self):
        return "auth"

    async def process_llm_request(self, **kwargs):
        self.process_llm_request_kwargs = kwargs


def _make_toolset(outcomes):
    created = []

    def factory(**kwargs):
        outcome = outcomes.pop(0)
        toolset = _FakeMcpToolset(outcome=outcome, **kwargs)
        created.append(toolset)
        return toolset

    wrapper = RefreshingMcpToolset(
        connection_params="connection",
        tool_filter=["kb_search"],
        mcp_toolset_factory=factory,
    )
    return wrapper, created


@pytest.mark.unit
def test_is_mcp_session_error_detects_nested_error() -> None:
    exc = ExceptionGroup(
        "wrapper",
        [RuntimeError("other"), RuntimeError("Connection closed")],
    )

    assert is_mcp_session_error(exc)


@pytest.mark.unit
def test_get_tools_refreshes_once_on_mcp_session_error() -> None:
    wrapper, created = _make_toolset(
        [
            RuntimeError("Failed to get tools from MCP server: Connection closed"),
            ["tool"],
        ]
    )

    result = asyncio.run(wrapper.get_tools(readonly_context="ctx"))

    assert result == ["tool"]
    assert len(created) == 2
    assert created[0].closed is True
    assert created[0].get_tools_calls == 1
    assert created[1].get_tools_calls == 1
    assert created[1].kwargs["connection_params"] == "connection"
    assert created[1].kwargs["tool_filter"] == ["kb_search"]


@pytest.mark.unit
def test_get_tools_does_not_refresh_non_mcp_error() -> None:
    wrapper, created = _make_toolset([RuntimeError("schema error")])

    with pytest.raises(RuntimeError, match="schema error"):
        asyncio.run(wrapper.get_tools())

    assert len(created) == 1
    assert created[0].closed is False


@pytest.mark.unit
def test_get_tools_raises_when_retry_fails() -> None:
    wrapper, created = _make_toolset(
        [
            RuntimeError("Connection closed"),
            RuntimeError("Connection closed again"),
        ]
    )

    with pytest.raises(RuntimeError, match="Connection closed again"):
        asyncio.run(wrapper.get_tools())

    assert len(created) == 2
    assert created[0].closed is True


@pytest.mark.unit
def test_close_closes_current_toolset() -> None:
    wrapper, created = _make_toolset([["tool"]])

    asyncio.run(wrapper.close())

    assert created[0].closed is True


@pytest.mark.unit
def test_delegates_auth_config() -> None:
    wrapper, _ = _make_toolset([["tool"]])

    assert wrapper.get_auth_config() == "auth"
