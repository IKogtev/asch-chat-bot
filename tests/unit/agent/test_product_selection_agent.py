import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_product_selection_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "agents" / "product_selection_agent.py"

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
    config_stub.DBHUB_MCP_TIMEOUT_SEC = 30.0
    config_stub.DBHUB_MCP_TOKEN = ""
    config_stub.DBHUB_MCP_URL = ""

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

    spec = importlib.util.spec_from_file_location("agent.agents.product_selection_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.agents.product_selection_agent"] = module
    spec.loader.exec_module(module)
    return module


product_selection_module = _load_product_selection_module()
validate_product_selection_result = product_selection_module.validate_product_selection_result


@pytest.mark.unit
def test_validate_product_selection_result_accepts_dbhub_answer() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_filter",
            "message": "Products found",
            "source": "dbhub",
            "used_tables": "products",
        },
        {},
    )

    assert result == {
        "status": "ok",
        "mode": "product_filter",
        "message": "Products found",
        "source": "dbhub",
        "used_tables": ["products"],
    }


@pytest.mark.unit
def test_validate_product_selection_result_accepts_no_data() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "no_data",
            "message": "No data",
            "source": "none",
            "used_tables": [],
        },
        {},
    )

    assert result["mode"] == "no_data"
    assert result["source"] == "none"
    assert result["used_tables"] == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "parts"),
    [
        (
            {"status": "bad", "mode": "product_filter", "message": "x", "source": "dbhub"},
            ("product_selection_agent", "basic_fields", "invalid status"),
        ),
        (
            {"status": "ok", "mode": "bad", "message": "x", "source": "dbhub"},
            ("product_selection_agent", "basic_fields", "invalid mode"),
        ),
        (
            {"status": "ok", "mode": "product_filter", "message": "x", "source": "bad"},
            ("product_selection_agent", "basic_fields", "source must be"),
        ),
    ],
)
def test_validate_product_selection_result_rejects_invalid_basic_fields(payload, parts) -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(payload, {})

    message = str(exc.value)
    for part in parts:
        assert part in message


@pytest.mark.unit
def test_validate_product_selection_result_requires_message() -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": "product_filter",
                "message": "   ",
                "source": "dbhub",
                "used_tables": ["products"],
            },
            {},
        )

    assert "message is required" in str(exc.value)


@pytest.mark.unit
def test_validate_product_selection_result_requires_none_source_for_no_data() -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": "no_data",
                "message": "No data",
                "source": "dbhub",
                "used_tables": ["products"],
            },
            {},
        )

    assert "mode='no_data' requires source='none'" in str(exc.value)


@pytest.mark.unit
def test_validate_product_selection_result_rejects_none_source_for_data_answer() -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": "product_compare",
                "message": "Comparison",
                "source": "none",
                "used_tables": [],
            },
            {},
        )

    assert "product result with data must use source='dbhub'" in str(exc.value)
