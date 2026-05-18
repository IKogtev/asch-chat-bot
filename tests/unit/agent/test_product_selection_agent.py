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
def test_validate_product_selection_result_accepts_filter_answer() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_filter",
            "message": "Products found",
            "used_tables": "products",
            "clarification_options": [],
        },
        {},
    )

    assert result == {
        "status": "ok",
        "mode": "product_filter",
        "message": "Products found",
        "used_tables": ["products"],
        "resolved_product": None,
        "clarification_options": [],
    }


@pytest.mark.unit
def test_validate_product_selection_result_accepts_product_card_with_resolved_product() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_card",
            "message": "Product card",
            "used_tables": ["products"],
            "resolved_product": {
                "id": 2832,
                "name": " Fort Knox 6 месяцев ",
                "ignored": "x",
            },
        },
        {},
    )

    assert result["resolved_product"] == {
        "id": "2832",
        "name": "Fort Knox 6 месяцев",
    }
    assert result["clarification_options"] == []


@pytest.mark.unit
def test_validate_product_selection_result_accepts_product_kit_with_product_id() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_kit",
            "message": "Product kit",
            "used_tables": ["products"],
            "resolved_product": {
                "id": "2832",
                "name": "Fort Knox 6 месяцев",
            },
        },
        {},
    )

    assert result["mode"] == "product_kit"
    assert result["resolved_product"]["id"] == "2832"


@pytest.mark.unit
def test_validate_product_selection_result_accepts_needs_clarification() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "needs_clarification",
            "message": "Уточните срок или валюту.",
            "used_tables": ["products"],
            "clarification_options": [
                {
                    "id": 2832,
                    "name": "Защищенный капитал 5 лет",
                    "term": "5 лет",
                    "currency": "рубли",
                    "extra": "x",
                }
            ],
        },
        {},
    )

    assert result["clarification_options"] == [
        {
            "id": "2832",
            "name": "Защищенный капитал 5 лет",
            "term": "5 лет",
            "currency": "рубли",
        }
    ]


@pytest.mark.unit
def test_validate_product_selection_result_accepts_no_data() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "no_data",
            "message": "No data",
            "used_tables": [],
        },
        {},
    )

    assert result["mode"] == "no_data"
    assert result["used_tables"] == []
    assert result["resolved_product"] is None
    assert result["clarification_options"] == []


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "parts"),
    [
        (
            {"status": "bad", "mode": "product_filter", "message": "x"},
            ("product_selection_agent", "basic_fields", "invalid status"),
        ),
        (
            {"status": "ok", "mode": "bad", "message": "x"},
            ("product_selection_agent", "basic_fields", "invalid mode"),
        ),
        (
            {
                "status": "ok",
                "mode": "product_filter",
                "message": "x",
                "resolved_product": "bad",
            },
            ("product_selection_agent", "basic_fields", "expected dict"),
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
                "used_tables": ["products"],
            },
            {},
        )

    assert "message is required" in str(exc.value)


@pytest.mark.unit
def test_validate_product_selection_result_rejects_removed_modes() -> None:
    for mode in ["product_recommendation", "product_explanation", "product_alternatives"]:
        with pytest.raises(ValueError) as exc:
            validate_product_selection_result(
                {
                    "status": "ok",
                    "mode": mode,
                    "message": "Removed mode",
                    "used_tables": ["products"],
                },
                {},
            )

        assert "invalid mode" in str(exc.value)


@pytest.mark.unit
@pytest.mark.parametrize("mode", ["product_card", "product_kit"])
def test_validate_product_selection_result_requires_resolved_product(mode: str) -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": mode,
                "message": "Product answer",
                "used_tables": ["products"],
            },
            {},
        )

    assert "requires resolved_product" in str(exc.value)


@pytest.mark.unit
def test_validate_product_selection_result_requires_id_for_product_kit() -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": "product_kit",
                "message": "Product kit",
                "used_tables": ["products"],
                "resolved_product": {"name": "Fort Knox"},
            },
            {},
        )

    assert "requires resolved_product.id" in str(exc.value)


@pytest.mark.unit
def test_validate_product_selection_result_requires_options_for_needs_clarification() -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": "needs_clarification",
                "message": "Уточните срок или валюту.",
                "used_tables": ["products"],
                "clarification_options": [],
            },
            {},
        )

    assert "requires clarification_options" in str(exc.value)
