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

    adk_agents_stub = types.ModuleType("google.adk.agents")
    adk_agents_stub.LlmAgent = type("LlmAgent", (), {})

    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = type("LiteLlm", (), {})

    mcp_tool_stub = types.ModuleType("google.adk.tools.mcp_tool")
    mcp_tool_stub.McpToolset = type("McpToolset", (), {})

    mcp_session_stub = types.ModuleType("google.adk.tools.mcp_tool.mcp_session_manager")
    mcp_session_stub.StreamableHTTPConnectionParams = type(
        "StreamableHTTPConnectionParams", (), {}
    )

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.config"] = config_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.prompt_loader"] = prompt_loader_stub
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.models.lite_llm"] = lite_llm_stub
    sys.modules["google.adk.tools.mcp_tool"] = mcp_tool_stub
    sys.modules["google.adk.tools.mcp_tool.mcp_session_manager"] = mcp_session_stub

    spec = importlib.util.spec_from_file_location("agent.agents.kb_answer_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.agents.kb_answer_agent"] = module
    spec.loader.exec_module(module)
    return module


kb_answer_module = _load_kb_answer_module()
validate_kb_answer_result = kb_answer_module.validate_kb_answer_result


@pytest.mark.unit
def test_validate_kb_answer_result_accepts_text_answer() -> None:
    result = validate_kb_answer_result(
        {
            "status": "ok",
            "mode": "text_answer",
            "message": "Готовый ответ",
            "source": "faq_search",
        }
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
        }
    )

    assert result["mode"] == "no_data"
    assert result["source"] == "none"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (
            {"status": "bad", "mode": "text_answer", "message": "x", "source": "faq_search"},
            "Invalid status",
        ),
        (
            {"status": "ok", "mode": "bad", "message": "x", "source": "faq_search"},
            "Invalid mode",
        ),
        (
            {"status": "ok", "mode": "text_answer", "message": "x", "source": "bad"},
            "Invalid source",
        ),
    ],
)
def test_validate_kb_answer_result_rejects_invalid_basic_fields(payload, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        validate_kb_answer_result(payload)


@pytest.mark.unit
def test_validate_kb_answer_result_requires_non_empty_message() -> None:
    with pytest.raises(ValueError, match="message is required"):
        validate_kb_answer_result(
            {
                "status": "ok",
                "mode": "text_answer",
                "message": "   ",
                "source": "faq_search",
            }
        )
