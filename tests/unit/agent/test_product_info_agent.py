import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_module():
    repo_root = Path(__file__).resolve().parents[3]
    agents_path = repo_root / "agent" / "agents"

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

    def load(name: str):
        module_path = agents_path / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"agent.agents.{name}", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    contract = load("product_info_contract")
    content = load("product_info_content_agent")
    formatter = load("product_info_format_agent")
    return types.SimpleNamespace(
        ProductInfoResponseSchema=contract.ProductInfoResponseSchema,
        validate_product_info_result=contract.validate_product_info_result,
        create_product_info_content_agent=content.create_product_info_content_agent,
        create_product_info_format_agent=formatter.create_product_info_format_agent,
    )


product_info = _load_module()
SQL_CONTEXT = {"_adk_tool_calls": ["execute_sql"]}


@pytest.mark.unit
def test_product_info_contract_accepts_card() -> None:
    result = product_info.validate_product_info_result(
        {
            "mode": "product_card",
            "message": "Карточка продукта",
            "resolved_product": {"code": 2832, "name": "Fort Knox"},
        },
        SQL_CONTEXT,
    )

    assert "status" not in result
    assert set(result) == {
        "mode",
        "message",
        "resolved_product",
        "clarification_options",
    }
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
def test_product_info_format_prompt_requires_product_kit_message() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    prompt = (
        repo_root
        / "kb_storage"
        / "prompts"
        / "product_info_format"
        / "product_info_format_agent_prompt.md"
    ).read_text(encoding="utf-8")

    assert "`message` должен быть непустым" in prompt
    assert "Комплект для продукта «<name>»." in prompt


@pytest.mark.unit
def test_product_info_response_schema_restricts_mode() -> None:
    with pytest.raises(Exception):
        product_info.ProductInfoResponseSchema(mode="product_filter", message="x")


@pytest.mark.unit
def test_product_info_response_schema_contains_only_used_fields() -> None:
    schema = product_info.ProductInfoResponseSchema.model_json_schema()
    response = product_info.ProductInfoResponseSchema(
        mode="product_card",
        message="x",
    )

    expected_fields = {
        "mode",
        "message",
        "resolved_product",
        "clarification_options",
    }
    assert set(schema["properties"]) == expected_fields
    assert set(response.model_dump()) == expected_fields
    assert schema["properties"]["message"]["minLength"] == 1


@pytest.mark.unit
def test_product_info_response_schema_rejects_whitespace_message() -> None:
    with pytest.raises(Exception, match="message must be non-empty"):
        product_info.ProductInfoResponseSchema(mode="no_data", message="   ")


@pytest.mark.unit
def test_product_info_response_schema_rejects_unsupported_product_fields() -> None:
    with pytest.raises(Exception, match="unsupported fields"):
        product_info.ProductInfoResponseSchema(
            mode="product_card",
            message="Карточка продукта",
            resolved_product={"code": "8914", "name": "Продукт", "term": "1 год"},
        )


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
