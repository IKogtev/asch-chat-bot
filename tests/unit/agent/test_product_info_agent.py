import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "agents" / "product_info_agent.py"

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
    config_stub.DBHUB_MCP_URL = "http://dbhub.test/mcp"
    config_stub.PRODUCT_INFO_TEMPERATURE = 0.0
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

    spec = importlib.util.spec_from_file_location("agent.agents.product_info_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


product_info = _load_module()
SQL_CONTEXT = {"_adk_tool_calls": ["execute_sql"]}


@pytest.mark.unit
def test_product_info_contract_accepts_card() -> None:
    result = product_info.validate_product_info_result(
        {
            "mode": "product_card",
            "message": "Карточка продукта",
            "used_tables": "products",
            "resolved_product": {"code": 2832, "name": "Fort Knox"},
        },
        SQL_CONTEXT,
    )

    assert "status" not in result
    assert result["used_tables"] == ["products"]
    assert result["resolved_product"] == {"code": "2832", "name": "Fort Knox"}


@pytest.mark.unit
def test_product_info_contract_keeps_folder_kit_exception() -> None:
    result = product_info.validate_product_info_result(
        {
            "mode": "product_kit",
            "message": "Комплект готов",
            "resolved_product": {"code": "2832", "name": "Fort Knox", "folder_kit": "Fort Knox (2832)"},
        },
        {"_adk_tool_calls": []},
    )

    assert result["resolved_product"]["folder_kit"] == "Fort Knox (2832)"


@pytest.mark.unit
def test_product_info_rejects_filter_mode() -> None:
    with pytest.raises(ValueError, match="invalid mode"):
        product_info.validate_product_info_result(
            {"mode": "product_filter", "message": "x"},
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_info_factories_split_tools_and_response_schema() -> None:
    content_agent = product_info.create_product_info_content_agent(model="content-model")
    format_agent = product_info.create_product_info_format_agent(model="format-model")

    assert content_agent.name == "product_info_content_agent"
    assert content_agent.output_key == "product_info_content_result_json"
    assert len(content_agent.tools) == 1
    assert getattr(content_agent, "output_schema", None) is None

    assert format_agent.name == "product_info_format_agent"
    assert format_agent.output_key == "product_info_result_json"
    assert format_agent.tools == []
    assert format_agent.output_schema is product_info.ProductInfoResponseSchema
    assert format_agent.generate_content_config["temperature"] == 0.0


@pytest.mark.unit
def test_product_info_response_schema_restricts_mode() -> None:
    with pytest.raises(Exception):
        product_info.ProductInfoResponseSchema(mode="product_filter", message="x")


@pytest.mark.unit
def test_product_info_response_schema_omits_status() -> None:
    schema = product_info.ProductInfoResponseSchema.model_json_schema()
    response = product_info.ProductInfoResponseSchema(
        mode="product_card",
        message="x",
    )

    assert "status" not in schema["properties"]
    assert "status" not in response.model_dump()


@pytest.mark.unit
def test_product_info_response_schema_parses_json_string_resolved_product() -> None:
    response = product_info.ProductInfoResponseSchema(
        mode="product_card",
        message="Карточка продукта",
        resolved_product='{"code": "8914", "name": "Фиксированный доход 1 год"}',
    )

    assert response.resolved_product == {
        "code": "8914",
        "name": "Фиксированный доход 1 год",
    }


@pytest.mark.unit
@pytest.mark.parametrize("resolved_product", ["not-json", '["8914"]'])
def test_product_info_response_schema_rejects_non_object_resolved_product(
    resolved_product: str,
) -> None:
    with pytest.raises(Exception, match="resolved_product must be a JSON object"):
        product_info.ProductInfoResponseSchema(
            mode="product_card",
            message="Карточка продукта",
            resolved_product=resolved_product,
        )
