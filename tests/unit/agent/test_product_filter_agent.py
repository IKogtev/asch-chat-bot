import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "agents" / "product_filter_agent.py"

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
    config_stub.PRODUCT_FILTER_TEMPERATURE = 0.0
    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.load_prompt = lambda *args, **kwargs: "prompt"
    watcher_stub = types.ModuleType("agent.prompt_loader")
    watcher_stub.start_prompt_watcher = lambda *args, **kwargs: None

    class FakeAgent:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    adk_agents_stub = types.ModuleType("google.adk.agents")
    adk_agents_stub.LlmAgent = FakeAgent
    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = object
    genai_types_stub = types.ModuleType("google.genai.types")
    genai_types_stub.GenerateContentConfig = lambda **kwargs: kwargs
    session_stub = types.ModuleType("google.adk.tools.mcp_tool.mcp_session_manager")
    session_stub.StreamableHTTPConnectionParams = lambda **kwargs: kwargs

    class RefreshingToolset:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    toolset_stub = types.ModuleType("agent.tools.refreshing_mcp_toolset")
    toolset_stub.RefreshingMcpToolset = RefreshingToolset

    for name, module in {
        "agent": agent_pkg,
        "agent.agents": agents_pkg,
        "utils.logger": logger_stub,
        "agent.config": config_stub,
        "agent.helpers": helpers_stub,
        "agent.prompt_loader": watcher_stub,
        "agent.tools.refreshing_mcp_toolset": toolset_stub,
        "google.adk.agents": adk_agents_stub,
        "google.adk.models.lite_llm": lite_llm_stub,
        "google.genai.types": genai_types_stub,
        "google.adk.tools.mcp_tool.mcp_session_manager": session_stub,
    }.items():
        sys.modules[name] = module

    spec = importlib.util.spec_from_file_location("agent.agents.product_filter_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


product_filter = _load_module()
SQL_CONTEXT = {"_adk_tool_calls": ["execute_sql"]}


@pytest.mark.unit
def test_product_filter_contract_normalizes_products() -> None:
    result = product_filter.validate_product_filter_result(
        {
            "status": "ok",
            "mode": "product_filter",
            "message": "Найдены продукты",
            "products": [{"code": 2867, "name": " Fort Knox ", "is_active": "Действующий"}],
        },
        SQL_CONTEXT,
    )

    assert result["products"] == [{"code": "2867", "name": "Fort Knox", "is_active": "Действующий"}]


@pytest.mark.unit
def test_product_filter_contract_requires_attribute_values() -> None:
    with pytest.raises(ValueError, match="requires attribute_values"):
        product_filter.validate_product_filter_result(
            {"status": "ok", "mode": "product_attribute_values", "message": "Значения"},
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_filter_contract_rejects_info_mode() -> None:
    with pytest.raises(ValueError, match="invalid mode"):
        product_filter.validate_product_filter_result(
            {"status": "ok", "mode": "product_card", "message": "x"},
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_filter_factory_uses_response_schema() -> None:
    agent = product_filter.create_product_filter_agent(model="model")

    assert agent.name == "product_filter_agent"
    assert agent.output_key == "product_filter_result_json"
    assert agent.output_schema is product_filter.ProductFilterResponseSchema


@pytest.mark.unit
def test_product_filter_response_schema_restricts_mode() -> None:
    with pytest.raises(Exception):
        product_filter.ProductFilterResponseSchema(status="ok", mode="product_kit", message="x")
