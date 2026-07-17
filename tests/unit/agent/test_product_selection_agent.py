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
    config_stub.PRODUCT_SELECTION_TEMPERATURE = 0.0
    config_stub.build_generate_content_config = lambda temperature: None

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.load_prompt = lambda *args, **kwargs: "prompt"

    prompt_loader_stub = types.ModuleType("agent.prompt_loader")
    prompt_loader_stub.start_prompt_watcher = lambda *args, **kwargs: None

    adk_agents_stub = types.ModuleType("google.adk.agents")

    class _FakeLlmAgent:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.name = kwargs.get("name")
            self.model = kwargs.get("model")
            self.instruction = kwargs.get("instruction")
            self.tools = kwargs.get("tools")
            self.output_key = kwargs.get("output_key")
            self.output_schema = kwargs.get("output_schema")
            self.generate_content_config = kwargs.get("generate_content_config")

    adk_agents_stub.LlmAgent = _FakeLlmAgent

    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = type("LiteLlm", (), {})

    mcp_session_manager_stub = types.ModuleType(
        "google.adk.tools.mcp_tool.mcp_session_manager"
    )
    mcp_toolset_stub = types.ModuleType("google.adk.tools.mcp_tool.mcp_toolset")

    class _FakeConnectionParams:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.url = kwargs.get("url")
            self.headers = kwargs.get("headers")
            self.timeout = kwargs.get("timeout")

    mcp_toolset_stub.McpToolset = type("McpToolset", (), {})
    mcp_session_manager_stub.StreamableHTTPConnectionParams = type(
        "StreamableHTTPConnectionParams", (), {"__init__": _FakeConnectionParams.__init__}
    )

    refreshing_toolset_stub = types.ModuleType("agent.tools.refreshing_mcp_toolset")

    class _FakeRefreshingMcpToolset:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.connection_params = kwargs.get("connection_params")
            self.tool_filter = kwargs.get("tool_filter")

    refreshing_toolset_stub.RefreshingMcpToolset = _FakeRefreshingMcpToolset

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.config"] = config_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.prompt_loader"] = prompt_loader_stub
    sys.modules["agent.tools.refreshing_mcp_toolset"] = refreshing_toolset_stub
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.models.lite_llm"] = lite_llm_stub
    sys.modules[
        "google.adk.tools.mcp_tool.mcp_session_manager"
    ] = mcp_session_manager_stub
    sys.modules["google.adk.tools.mcp_tool.mcp_toolset"] = mcp_toolset_stub

    spec = importlib.util.spec_from_file_location("agent.agents.product_selection_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.agents.product_selection_agent"] = module
    spec.loader.exec_module(module)
    return module


product_selection_module = _load_product_selection_module()
validate_product_selection_result = product_selection_module.validate_product_selection_result
SQL_CONTEXT = {"_adk_tool_calls": ["execute_sql"]}
PRODUCT_COMPARE_COLUMNS = [
    "code",
    "name",
    "is_active",
    "insurance_type",
    "product_type",
    "term",
    "capital_loss_risk",
    "product_risk_level",
    "income",
    "contribution_type",
    "payout_type",
    "liquidity",
    "currency",
    "fx_protection",
    "segment",
    "client_goal",
    "taxes",
    "tax_benefits",
    "in_focus",
    "in_focus_condition",
    "input_date",
    "commission",
]


@pytest.mark.unit
def test_product_compare_prompt_requires_all_product_columns() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    prompt_path = (
        repo_root
        / "kb_storage"
        / "prompts"
        / "product_selection"
        / "product_selection_agent_compare_prompt.md"
    )
    prompt = prompt_path.read_text(encoding="utf-8")

    for column in PRODUCT_COMPARE_COLUMNS:
        assert f"`{column}`" in prompt

    assert "не короткий `display_columns`" in prompt
    assert "product_resolutions" in prompt
    assert "execute_sql" in prompt
    assert "code IN" in prompt


@pytest.mark.unit
def test_validate_product_selection_result_accepts_resolver_style_clarification_options() -> None:
    result = validate_product_selection_result(
        {
            "status": "needs_clarification",
            "mode": "product_compare",
            "message": "Уточните продукт Альфа Kids",
            "used_tables": "products",
            "resolved_product_code": "7725",
            "resolved_product_name": "Альфа Kids+ 5 лет",
            "clarification_options_json": (
                '[{"product_code": "3821", "canonical_name": "Альфа Kids 5 лет"},'
                ' {"product_code": "7725", "canonical_name": "Альфа Kids+ 5 лет"}]'
            ),
            "products_json": "[]",
        },
        {},
    )

    assert result["status"] == "ok"
    assert result["mode"] == "needs_clarification"
    assert result["clarification_options"] == [
        {"code": "3821", "name": "Альфа Kids 5 лет"},
        {"code": "7725", "name": "Альфа Kids+ 5 лет"},
    ]
    assert result["resolved_product"] == {
        "code": "7725",
        "name": "Альфа Kids+ 5 лет",
    }


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
        SQL_CONTEXT,
    )

    assert result == {
        "status": "ok",
        "mode": "product_filter",
        "message": "Products found",
        "used_tables": ["products"],
        "resolved_product": None,
        "clarification_options": [],
        "products": [],
        "attribute_name": "",
        "attribute_column": "",
        "attribute_values": [],
    }


@pytest.mark.unit
def test_validate_product_selection_result_accepts_products_for_filter_answer() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_filter",
            "message": "Products found",
            "used_tables": ["products"],
            "products": [
                {
                    "code": 2867,
                    "name": " Bundle Fort Knox 3+36 месяцев ",
                    "folder_kit": "Fort Knox (2867)",
                    "ignored": "x",
                }
            ],
            "clarification_options": [],
        },
        SQL_CONTEXT,
    )

    assert result["products"] == [
        {
            "code": "2867",
            "name": "Bundle Fort Knox 3+36 месяцев",
            "folder_kit": "Fort Knox (2867)",
        }
    ]


@pytest.mark.unit
def test_validate_product_selection_result_accepts_attribute_values_answer() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_attribute_values",
            "message": "Values found",
            "used_tables": ["products"],
            "resolved_product": None,
            "clarification_options": [],
            "products": [],
            "attribute_name": "currency",
            "attribute_column": "currency",
            "attribute_values": ["RUB", "USD", "CNY"],
        },
        SQL_CONTEXT,
    )

    assert result["mode"] == "product_attribute_values"
    assert result["products"] == []
    assert result["attribute_name"] == "currency"
    assert result["attribute_column"] == "currency"
    assert result["attribute_values"] == ["RUB", "USD", "CNY"]


@pytest.mark.unit
def test_validate_product_selection_result_accepts_product_card_with_resolved_product() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_card",
            "message": "Product card",
            "used_tables": ["products"],
            "resolved_product": {
                "code": 2832,
                "name": " Fort Knox 6 месяцев ",
                "ignored": "x",
            },
        },
        SQL_CONTEXT,
    )

    assert result["resolved_product"] == {
        "code": "2832",
        "name": "Fort Knox 6 месяцев",
    }
    assert result["clarification_options"] == []


@pytest.mark.unit
def test_validate_product_selection_result_accepts_product_kit_with_product_code() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_kit",
            "message": "Product kit",
            "used_tables": ["products"],
            "resolved_product": {
                "code": "2832",
                "name": "Fort Knox 6 месяцев",
                "folder_kit": "Fort Knox (2832)",
            },
        },
        SQL_CONTEXT,
    )

    assert result["mode"] == "product_kit"
    assert result["resolved_product"]["code"] == "2832"
    assert result["resolved_product"]["folder_kit"] == "Fort Knox (2832)"


@pytest.mark.unit
def test_validate_product_selection_result_unwraps_double_encoded_set_model_response() -> None:
    """ADK set_model_response иногда отдаёт строки как JSON-литералы: '\"8957\"', '\"[]\"'."""
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "product_kit",
            "message": "Комплект документов для продукта 'Защищенный капитал $ 3 года' найден.",
            "used_tables": "products",
            "resolved_product_code": '"8957"',
            "resolved_product_name": '"Защищенный капитал $ 3 года"',
            "resolved_product_folder_kit": '"НСЖ/ЗК $ 3 года 5,1% (8957) 22.05.26"',
            "clarification_options_json": '"[]"',
            "products_json": '"[]"',
            "attribute_name": '""',
            "attribute_column": '""',
            "attribute_values": '""',
        },
        SQL_CONTEXT,
    )

    assert result["mode"] == "product_kit"
    assert result["resolved_product"] == {
        "code": "8957",
        "name": "Защищенный капитал $ 3 года",
        "folder_kit": "НСЖ/ЗК $ 3 года 5,1% (8957) 22.05.26",
    }
    assert result["clarification_options"] == []
    assert result["products"] == []
    assert result["attribute_name"] == ""
    assert result["attribute_column"] == ""
    assert result["attribute_values"] == []


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
                    "code": 2832,
                    "name": "Защищенный капитал 5 лет",
                    "term": "5 лет",
                    "currency": "рубли",
                    "extra": "x",
                }
            ],
        },
        SQL_CONTEXT,
    )

    assert result["clarification_options"] == [
        {
            "code": "2832",
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
        SQL_CONTEXT,
    )

    assert result["mode"] == "no_data"
    assert result["used_tables"] == []
    assert result["resolved_product"] is None
    assert result["clarification_options"] == []
    assert result["products"] == []


@pytest.mark.unit
def test_validate_product_selection_result_accepts_no_data_without_execute_sql() -> None:
    result = validate_product_selection_result(
        {
            "status": "ok",
            "mode": "no_data",
            "message": "No data",
            "used_tables": [],
        },
        {"_adk_tool_calls": ["search_table", "search_column"]},
    )

    assert result["mode"] == "no_data"
    assert result["used_tables"] == []


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
def test_validate_product_selection_result_requires_execute_sql_tool_call() -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": "product_filter",
                "message": "Products found",
                "used_tables": ["products"],
            },
            {"_adk_tool_calls": ["search_table", "search_column"]},
        )

    message = str(exc.value)
    assert "tool_usage" in message
    assert "execute_sql" in message


@pytest.mark.unit
def test_validate_product_selection_result_logs_debug_context() -> None:
    debug_messages = []
    original_logger = product_selection_module.logger
    product_selection_module.logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: debug_messages.append(a[0] if a else ""),
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )

    try:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": "product_filter",
                "message": "Products found",
                "used_tables": ["products"],
            },
            {
                "_adk_tool_calls": ["execute_sql"],
                "_adk_tool_event_summaries": [
                    {"type": "call", "name": "execute_sql"}
                ],
            },
        )
    finally:
        product_selection_module.logger = original_logger

    assert any(
        "product_selection validation context" in message
        for message in debug_messages
    )


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
def test_validate_product_selection_result_requires_code_for_product_kit() -> None:
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

    assert "requires resolved_product.code" in str(exc.value)


@pytest.mark.unit
def test_validate_product_selection_result_requires_values_for_attribute_values() -> None:
    with pytest.raises(ValueError) as exc:
        validate_product_selection_result(
            {
                "status": "ok",
                "mode": "product_attribute_values",
                "message": "Values found",
                "used_tables": ["products"],
                "attribute_values": [],
            },
            SQL_CONTEXT,
        )

    assert "requires attribute_values" in str(exc.value)


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


@pytest.mark.unit
def test_create_product_selection_agents_use_refreshing_toolset_and_schemas() -> None:
    original_url = product_selection_module.DBHUB_MCP_URL
    original_token = product_selection_module.DBHUB_MCP_TOKEN
    original_timeout = product_selection_module.DBHUB_MCP_TIMEOUT_SEC

    product_selection_module.DBHUB_MCP_URL = "http://dbhub:8080/mcp"
    product_selection_module.DBHUB_MCP_TOKEN = "token"
    product_selection_module.DBHUB_MCP_TIMEOUT_SEC = 45.0

    try:
        agents = product_selection_module.create_product_selection_agents(model="model")
    finally:
        product_selection_module.DBHUB_MCP_URL = original_url
        product_selection_module.DBHUB_MCP_TOKEN = original_token
        product_selection_module.DBHUB_MCP_TIMEOUT_SEC = original_timeout

    assert agents.card_kit.name == "product_selection_card_kit_agent"
    assert agents.filter.name == "product_selection_filter_agent"
    assert agents.compare.name == "product_selection_compare_agent"

    assert agents.card_kit.output_schema is product_selection_module.ProductSelectionCardKitResponseSchema
    assert agents.filter.output_schema is product_selection_module.ProductSelectionFilterResponseSchema
    assert agents.compare.output_schema is product_selection_module.ProductSelectionCompareResponseSchema

    assert agents.card_kit.tools[0].tool_filter == product_selection_module.CARD_KIT_TOOL_FILTER
    assert agents.filter.tools[0].tool_filter == product_selection_module.FILTER_TOOL_FILTER
    assert agents.compare.tools[0].tool_filter == product_selection_module.COMPARE_TOOL_FILTER

    toolset = agents.filter.tools[0]
    assert type(toolset).__name__ == "_FakeRefreshingMcpToolset"
    assert toolset.connection_params.url == "http://dbhub:8080/mcp"
    assert toolset.connection_params.headers == {"Authorization": "Bearer token"}
    assert toolset.connection_params.timeout == 45.0

    assert (
        product_selection_module.select_product_selection_agent("product_card", agents)
        is agents.card_kit
    )
    assert (
        product_selection_module.select_product_selection_agent("product_kit", agents)
        is agents.card_kit
    )
    assert (
        product_selection_module.select_product_selection_agent("product_filter", agents)
        is agents.filter
    )
    assert (
        product_selection_module.select_product_selection_agent(
            "product_attribute_values", agents
        )
        is agents.filter
    )
    assert (
        product_selection_module.select_product_selection_agent("product_compare", agents)
        is agents.compare
    )
