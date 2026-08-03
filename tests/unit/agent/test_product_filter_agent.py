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

    def load(name: str):
        module_path = agents_path / f"{name}.py"
        spec = importlib.util.spec_from_file_location(f"agent.agents.{name}", module_path)
        module = importlib.util.module_from_spec(spec)
        assert spec and spec.loader
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    contract = load("product_filter_contract")
    content = load("product_filter_content_agent")
    formatter = load("product_filter_format_agent")
    return types.SimpleNamespace(
        ProductFilterResponseSchema=contract.ProductFilterResponseSchema,
        validate_product_filter_result=contract.validate_product_filter_result,
        create_product_filter_content_agent=content.create_product_filter_content_agent,
        create_product_filter_format_agent=formatter.create_product_filter_format_agent,
    )


product_filter = _load_module()
SQL_CONTEXT = {"_adk_tool_calls": ["execute_sql"]}


@pytest.mark.unit
def test_product_filter_contract_normalizes_products() -> None:
    result = product_filter.validate_product_filter_result(
        {
            "mode": "product_filter",
            "message": "Найдены продукты",
            "products": [{"code": 2867, "name": " Fort Knox ", "is_active": "Действующий"}],
        },
        SQL_CONTEXT,
    )

    assert result["products"] == [{"code": "2867", "name": "Fort Knox", "is_active": "Действующий"}]


@pytest.mark.unit
def test_product_filter_contract_normalizes_nullable_fields() -> None:
    payload = product_filter.ProductFilterResponseSchema(
        mode="needs_clarification",
        message="Уточните продукт",
        clarification_options=[
            {"code": "8914", "name": "Фиксированный доход 1 год"},
            {
                "code": "8959",
                "name": "Фиксированный доход 1 год + Альфа-Вклад Актив",
            },
        ],
        products=[
            {
                "code": "8914",
                "name": "Фиксированный доход 1 год",
                "term": None,
                "currency": None,
                "folder_kit": None,
            }
        ],
        attribute_name=None,
        attribute_column=None,
    ).model_dump()

    result = product_filter.validate_product_filter_result(payload, {})

    assert "status" not in result
    assert result["clarification_options"] == [
        {"code": "8914", "name": "Фиксированный доход 1 год"},
        {
            "code": "8959",
            "name": "Фиксированный доход 1 год + Альфа-Вклад Актив",
        },
    ]
    assert result["products"] == [
        {"code": "8914", "name": "Фиксированный доход 1 год"}
    ]
    assert result["attribute_name"] == ""
    assert result["attribute_column"] == ""


@pytest.mark.unit
def test_product_filter_response_schema_rejects_noncanonical_clarification_options() -> None:
    with pytest.raises(Exception):
        product_filter.ProductFilterResponseSchema(
            mode="needs_clarification",
            message="Уточните продукт",
            clarification_options=[
                {
                    "product_code": "8914",
                    "canonical_name": "Фиксированный доход 1 год",
                }
            ],
        )


@pytest.mark.unit
def test_product_filter_response_schema_keeps_clarification_options_inline() -> None:
    schema = product_filter.ProductFilterResponseSchema.model_json_schema()
    clarification_schema = schema["properties"]["clarification_options"]

    assert "$defs" not in schema
    assert "$ref" not in str(clarification_schema)
    assert clarification_schema["items"]["type"] == "object"


@pytest.mark.unit
@pytest.mark.parametrize("resolved_product", ["None", " none ", "null"])
def test_product_filter_response_schema_normalizes_absent_resolved_product(
    resolved_product: str,
) -> None:
    response = product_filter.ProductFilterResponseSchema(
        mode="product_filter",
        message="Найдены продукты",
        resolved_product=resolved_product,
    )

    assert response.resolved_product is None


@pytest.mark.unit
def test_product_filter_response_schema_rejects_other_resolved_product_strings() -> None:
    with pytest.raises(Exception):
        product_filter.ProductFilterResponseSchema(
            mode="product_filter",
            message="Найдены продукты",
            resolved_product="not-a-product",
        )


@pytest.mark.unit
def test_product_filter_response_schema_normalizes_null_list_fields() -> None:
    response = product_filter.ProductFilterResponseSchema(
        mode="product_compare",
        message="Сравнение продуктов",
        clarification_options=None,
        attribute_values=None,
    )

    assert response.clarification_options == []
    assert response.attribute_values == []


@pytest.mark.unit
@pytest.mark.parametrize("field_name", ["clarification_options", "attribute_values"])
def test_product_filter_response_schema_rejects_non_list_field_values(
    field_name: str,
) -> None:
    payload = {
        "mode": "product_compare",
        "message": "Сравнение продуктов",
        field_name: "None",
    }

    with pytest.raises(Exception):
        product_filter.ProductFilterResponseSchema(**payload)


@pytest.mark.unit
def test_product_filter_contract_requires_attribute_values() -> None:
    with pytest.raises(ValueError, match="requires attribute_name"):
        product_filter.validate_product_filter_result(
            {"mode": "product_attribute_values", "message": "Значения"},
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_filter_contract_requires_products() -> None:
    with pytest.raises(ValueError, match="requires products"):
        product_filter.validate_product_filter_result(
            {"mode": "product_filter", "message": "Найдены продукты"},
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_filter_contract_rejects_info_mode() -> None:
    with pytest.raises(ValueError, match="invalid mode"):
        product_filter.validate_product_filter_result(
            {"mode": "product_card", "message": "x"},
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_filter_factories_split_tools_and_response_schema() -> None:
    content_agent = product_filter.create_product_filter_content_agent(
        model="content-model"
    )
    format_agent = product_filter.create_product_filter_format_agent(
        model="format-model"
    )

    assert content_agent.name == "product_filter_content_agent"
    assert content_agent.output_key == "product_filter_content_result_json"
    assert len(content_agent.tools) == 1
    assert getattr(content_agent, "output_schema", None) is None

    assert format_agent.name == "product_filter_format_agent"
    assert format_agent.output_key == "product_filter_result_json"
    assert format_agent.tools == []
    assert format_agent.output_schema is product_filter.ProductFilterResponseSchema
    assert format_agent.generate_content_config["temperature"] == 0.0


@pytest.mark.unit
def test_product_filter_response_schema_restricts_mode() -> None:
    with pytest.raises(Exception):
        product_filter.ProductFilterResponseSchema(mode="product_kit", message="x")


@pytest.mark.unit
def test_product_filter_response_schema_contains_only_used_fields() -> None:
    schema = product_filter.ProductFilterResponseSchema.model_json_schema()
    response = product_filter.ProductFilterResponseSchema(
        mode="product_filter",
        message="x",
    )

    expected_fields = {
        "mode",
        "message",
        "resolved_product",
        "clarification_options",
        "products",
        "attribute_name",
        "attribute_column",
        "attribute_values",
    }
    assert set(schema["properties"]) == expected_fields
    assert set(response.model_dump()) == expected_fields
    assert schema["properties"]["message"]["minLength"] == 1


@pytest.mark.unit
def test_product_filter_response_schema_rejects_unsupported_product_fields() -> None:
    with pytest.raises(Exception, match="unsupported fields"):
        product_filter.ProductFilterResponseSchema(
            mode="product_filter",
            message="Найдены продукты",
            products=[{"code": "8914", "name": "Продукт", "unknown": "x"}],
        )
