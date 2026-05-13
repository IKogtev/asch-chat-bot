import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_kb_answer_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "agents" / "kb_answer_agent.py"

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
    config_stub.FAQSEARCH_MCP_TIMEOUT_SEC = 30.0
    config_stub.FAQSEARCH_MCP_TOKEN = ""
    config_stub.FAQSEARCH_MCP_URL = ""
    config_stub.KBSEARCH_MCP_URL = ""
    config_stub.MCP_TIMEOUT_SEC = 30.0
    config_stub.MCP_TOKEN = ""

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

    spec = importlib.util.spec_from_file_location("agent.agents.kb_answer_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.agents.kb_answer_agent"] = module
    spec.loader.exec_module(module)
    return module


kb_answer_module = _load_kb_answer_module()
validate_kb_answer_result = kb_answer_module.validate_kb_answer_result
DEFAULT_CONTEXT = {"intent": "kb_answer"}


@pytest.mark.unit
def test_validate_kb_answer_result_accepts_text_answer() -> None:
    result = validate_kb_answer_result(
        {
            "status": "ok",
            "mode": "text_answer",
            "message": "Готовый ответ",
            "source": "faq_search",
        },
        DEFAULT_CONTEXT,
    )

    assert result == {
        "status": "ok",
        "mode": "text_answer",
        "message": "Готовый ответ",
        "source": "faq_search",
    }


@pytest.mark.unit
def test_validate_kb_answer_result_accepts_no_data_result() -> None:
    result = validate_kb_answer_result(
        {
            "status": "ok",
            "mode": "no_data",
            "message": "Точный ответ не найден",
            "source": "none",
        },
        DEFAULT_CONTEXT,
    )

    assert result["mode"] == "no_data"
    assert result["source"] == "none"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "parts"),
    [
        (
            {"status": "bad", "mode": "text_answer", "message": "x", "source": "faq_search"},
            ("kb_answer_agent", "basic_fields", "invalid status"),
        ),
        (
            {"status": "ok", "mode": "bad", "message": "x", "source": "faq_search"},
            ("kb_answer_agent", "basic_fields", "invalid mode"),
        ),
        (
            {"status": "ok", "mode": "text_answer", "message": "x", "source": "bad"},
            ("kb_answer_agent", "basic_fields", "invalid source"),
        ),
    ],
)
def test_validate_kb_answer_result_rejects_invalid_basic_fields(payload, parts) -> None:
    with pytest.raises(ValueError) as exc:
        validate_kb_answer_result(payload, DEFAULT_CONTEXT)

    message = str(exc.value)
    for part in parts:
        assert part in message


@pytest.mark.unit
def test_validate_kb_answer_result_requires_non_empty_message() -> None:
    with pytest.raises(ValueError) as exc:
        validate_kb_answer_result(
            {
                "status": "ok",
                "mode": "text_answer",
                "message": "   ",
                "source": "faq_search",
            },
            DEFAULT_CONTEXT,
        )

    assert "message is required" in str(exc.value)


@pytest.mark.unit
def test_validate_kb_answer_result_requires_none_source_for_no_data() -> None:
    with pytest.raises(ValueError) as exc:
        validate_kb_answer_result(
            {
                "status": "ok",
                "mode": "no_data",
                "message": "Ничего не найдено",
                "source": "faq_search",
            },
            DEFAULT_CONTEXT,
        )

    assert "mode='no_data' requires source='none'" in str(exc.value)


@pytest.mark.unit
def test_validate_kb_answer_result_rejects_none_source_for_text_answer() -> None:
    with pytest.raises(ValueError) as exc:
        validate_kb_answer_result(
            {
                "status": "ok",
                "mode": "text_answer",
                "message": "Ответ",
                "source": "none",
            },
            DEFAULT_CONTEXT,
        )

    assert "mode='text_answer' must not use source='none' outside smalltalk" in str(exc.value)


@pytest.mark.unit
def test_validate_kb_answer_result_allows_none_source_for_smalltalk() -> None:
    result = validate_kb_answer_result(
        {
            "status": "ok",
            "mode": "text_answer",
            "message": kb_answer_module.ASSISTANT_CAPABILITIES_ANSWER,
            "source": "none",
        },
        {"intent": "smalltalk"},
    )

    assert result["mode"] == "text_answer"
    assert result["source"] == "none"
    assert result["message"] == kb_answer_module.ASSISTANT_CAPABILITIES_ANSWER


@pytest.mark.unit
def test_assistant_capabilities_answer_constant_matches_expected_phrase() -> None:
    assert (
        kb_answer_module.ASSISTANT_CAPABILITIES_ANSWER
        == "Я умею искать документы и помогать продавать продукты АСЖ."
    )


@pytest.mark.unit
def test_create_kb_answer_agent_uses_refreshing_mcp_toolsets() -> None:
    kb_answer_module.KBSEARCH_MCP_URL = "http://kbsearch/mcp"
    kb_answer_module.MCP_TOKEN = "kb-token"
    kb_answer_module.MCP_TIMEOUT_SEC = 12.0
    kb_answer_module.FAQSEARCH_MCP_URL = "http://faq/mcp"
    kb_answer_module.FAQSEARCH_MCP_TOKEN = "faq-token"
    kb_answer_module.FAQSEARCH_MCP_TIMEOUT_SEC = 7.0

    agent = kb_answer_module.create_kb_answer_agent(model=object())

    assert len(agent.tools) == 2
    kb_toolset, faq_toolset = agent.tools
    assert kb_toolset.tool_filter == ["kb_search"]
    assert kb_toolset.connection_params.url == "http://kbsearch/mcp"
    assert kb_toolset.connection_params.headers == {"Authorization": "Bearer kb-token"}
    assert kb_toolset.connection_params.timeout == 12.0
    assert faq_toolset.tool_filter == ["faq_search"]
    assert faq_toolset.connection_params.url == "http://faq/mcp"
    assert faq_toolset.connection_params.headers == {"Authorization": "Bearer faq-token"}
    assert faq_toolset.connection_params.timeout == 7.0
