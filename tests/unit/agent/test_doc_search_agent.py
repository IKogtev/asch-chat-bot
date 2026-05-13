import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_doc_search_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "agents" / "doc_search_agent.py"

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = [str(repo_root / "agent")]
    agents_pkg = types.ModuleType("agent.agents")
    agents_pkg.__path__ = [str(repo_root / "agent" / "agents")]
    tools_pkg = types.ModuleType("agent.tools")
    tools_pkg.__path__ = [str(repo_root / "agent" / "tools")]

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.setup_logger = lambda *args, **kwargs: types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )

    config_stub = types.ModuleType("agent.config")
    config_stub.KBSEARCH_MCP_URL = ""
    config_stub.MCP_TOKEN = ""
    config_stub.MCP_TIMEOUT_SEC = 30.0

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.load_prompt = lambda *args, **kwargs: "prompt"

    prompt_loader_stub = types.ModuleType("agent.prompt_loader")
    prompt_loader_stub.start_prompt_watcher = lambda *args, **kwargs: None

    class _FakeLlmAgent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    adk_agents_stub = types.ModuleType("google.adk.agents")
    adk_agents_stub.LlmAgent = _FakeLlmAgent

    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = type("LiteLlm", (), {})

    mcp_tool_stub = types.ModuleType("google.adk.tools.mcp_tool")
    mcp_tool_stub.McpToolset = type("McpToolset", (), {})

    mcp_session_stub = types.ModuleType("google.adk.tools.mcp_tool.mcp_session_manager")
    class _FakeStreamableHTTPConnectionParams:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    mcp_session_stub.StreamableHTTPConnectionParams = _FakeStreamableHTTPConnectionParams

    refresh_stub = types.ModuleType("agent.tools.refreshing_mcp_toolset")

    class _FakeRefreshingMcpToolset:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    refresh_stub.RefreshingMcpToolset = _FakeRefreshingMcpToolset

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["agent.tools"] = tools_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.config"] = config_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.prompt_loader"] = prompt_loader_stub
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.models.lite_llm"] = lite_llm_stub
    sys.modules["google.adk.tools.mcp_tool"] = mcp_tool_stub
    sys.modules["google.adk.tools.mcp_tool.mcp_session_manager"] = mcp_session_stub
    sys.modules["agent.tools.refreshing_mcp_toolset"] = refresh_stub

    spec = importlib.util.spec_from_file_location("agent.agents.doc_search_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.agents.doc_search_agent"] = module
    spec.loader.exec_module(module)
    return module


doc_search_module = _load_doc_search_module()
validate_doc_search_result = doc_search_module.validate_doc_search_result
VALIDATION_CONTEXT = {}


@pytest.mark.unit
def test_validate_doc_search_result_accepts_document_list() -> None:
    result = validate_doc_search_result(
        {
            "status": "ok",
            "mode": "document_list",
            "message": "",
            "results": [
                {
                    "document_id": "doc-1",
                    "source_name": "file.pdf",
                    "source_path": "/x/file.pdf",
                    "snippet": "fragment",
                }
            ],
        },
        VALIDATION_CONTEXT,
    )

    assert result["mode"] == "document_list"
    assert result["results"][0]["document_id"] == "doc-1"


@pytest.mark.unit
def test_validate_doc_search_result_accepts_no_data_without_results() -> None:
    result = validate_doc_search_result(
        {
            "status": "ok",
            "mode": "no_data",
            "message": "Ничего не найдено",
        },
        VALIDATION_CONTEXT,
    )

    assert result["results"] == []


@pytest.mark.unit
def test_validate_doc_search_result_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result({"status": "ok", "mode": "bad", "message": "x"}, VALIDATION_CONTEXT)

    assert "doc_search_agent" in str(exc.value)
    assert "invalid mode" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_requires_message_for_non_document_list() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result({"status": "ok", "mode": "info", "message": ""}, VALIDATION_CONTEXT)

    assert "requires non-empty message" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_rejects_results_for_non_document_list() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result(
            {
                "status": "ok",
                "mode": "info",
                "message": "служебно",
                "results": [{"document_id": "1", "source_name": "x"}],
            },
            VALIDATION_CONTEXT,
        )

    assert "must not contain results" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_rejects_empty_document_list() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result(
            {"status": "ok", "mode": "document_list", "message": "", "results": []},
            VALIDATION_CONTEXT,
        )

    assert "requires non-empty results array" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_reports_invalid_items_after_normalization() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result(
            {
                "status": "ok",
                "mode": "document_list",
                "message": "",
                "results": [{"source_name": "x"}],
            },
            VALIDATION_CONTEXT,
        )

    assert "returned no valid items after normalization" in str(exc.value)
    assert "missing document_id" in str(exc.value)


@pytest.mark.unit
def test_create_doc_search_agent_uses_refreshing_mcp_toolset() -> None:
    doc_search_module.KBSEARCH_MCP_URL = "http://kbsearch/mcp"
    doc_search_module.MCP_TOKEN = "token"
    doc_search_module.MCP_TIMEOUT_SEC = 12.0

    agent = doc_search_module.create_doc_search_agent(model=object())

    assert len(agent.tools) == 1
    toolset = agent.tools[0]
    assert toolset.tool_filter == ["kb_search"]
    assert toolset.connection_params.url == "http://kbsearch/mcp"
    assert toolset.connection_params.headers == {"Authorization": "Bearer token"}
    assert toolset.connection_params.timeout == 12.0
