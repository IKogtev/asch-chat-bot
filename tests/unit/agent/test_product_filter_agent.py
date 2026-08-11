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
        validate_product_filter_result=contract.validate_product_filter_result,
        create_product_filter_content_agent=content.create_product_filter_content_agent,
        create_product_filter_format_agent=formatter.create_product_filter_format_agent,
    )


product_filter = _load_module()
SQL_CONTEXT = {"_adk_tool_calls": ["execute_sql"]}


@pytest.mark.unit
def test_product_filter_contract_normalizes_products_without_reformatting_message() -> None:
    message = "Найдено продуктов: 1.\n2867 - Fort Knox"
    result = product_filter.validate_product_filter_result(
        {
            "mode": "product_filter",
            "message": message,
            "products": [{"code": 2867, "name": " Fort Knox ", "is_active": "Действующий"}],
        },
        SQL_CONTEXT,
    )

    assert result["products"] == [{"code": "2867", "name": "Fort Knox", "is_active": "Действующий"}]
    assert result["message"] == message


@pytest.mark.unit
def test_product_filter_contract_accepts_multiple_products() -> None:
    result = product_filter.validate_product_filter_result(
        {
            "mode": "product_filter",
            "message": (
                "Найдено продуктов: 2.\n"
                "2851 - АльфаЗдоровье 5 лет (НСЖ Здоровье)\n"
                "8992 - Чистый процент 1 год (ПСЖ)"
            ),
            "products": [
                {"code": "2851", "name": "АльфаЗдоровье 5 лет"},
                {"code": "8992", "name": "Чистый процент 1 год"},
            ],
        },
        SQL_CONTEXT,
    )

    assert result["message"] == (
        "Найдено продуктов: 2.\n"
        "2851 - АльфаЗдоровье 5 лет (НСЖ Здоровье)\n"
        "8992 - Чистый процент 1 год (ПСЖ)"
    )
    assert [product["code"] for product in result["products"]] == ["2851", "8992"]


@pytest.mark.unit
def test_product_filter_contract_allows_same_code_with_different_names() -> None:
    result = product_filter.validate_product_filter_result(
        {
            "mode": "product_filter",
            "message": (
                "Найдено продуктов: 2.\n"
                "8914 - Фиксированный доход 1 год\n"
                "8914 - Фиксированный доход 2 года"
            ),
            "products": [
                {"code": "8914", "name": "Фиксированный доход 1 год"},
                {"code": "8914", "name": "Фиксированный доход 2 года"},
            ],
        },
        SQL_CONTEXT,
    )

    assert result["message"] == (
        "Найдено продуктов: 2.\n"
        "8914 - Фиксированный доход 1 год\n"
        "8914 - Фиксированный доход 2 года"
    )
    assert [product["name"] for product in result["products"]] == [
        "Фиксированный доход 1 год",
        "Фиксированный доход 2 года",
    ]


@pytest.mark.unit
def test_product_filter_contract_normalizes_nullable_fields() -> None:
    payload = {
        "mode": "needs_clarification",
        "message": "Уточните продукт",
        "clarification_options": [
            {"code": "8914", "name": "Фиксированный доход 1 год"},
            {
                "code": "8959",
                "name": "Фиксированный доход 1 год + Альфа-Вклад Актив",
            },
        ],
        "products": [
            {
                "code": "8914",
                "name": "Фиксированный доход 1 год",
                "term": None,
                "currency": None,
                "folder_kit": None,
            }
        ],
        "attribute_name": None,
        "attribute_column": None,
    }

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
@pytest.mark.parametrize(
    "products",
    [
        [],
        [{"code": "2851", "name": "АльфаЗдоровье 5 лет"}],
        [
            {"code": "8914", "name": "Фиксированный доход 1 год"},
            {"code": "8914", "name": "Фиксированный доход 1 год"},
        ],
    ],
)
def test_product_filter_contract_requires_two_distinct_comparison_products(
    products: list[dict[str, str]],
) -> None:
    with pytest.raises(ValueError, match="exactly two distinct product identities"):
        product_filter.validate_product_filter_result(
            {
                "mode": "product_compare",
                "message": "Сравнение продуктов",
                "products": products,
            },
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_filter_contract_accepts_two_distinct_comparison_products() -> None:
    result = product_filter.validate_product_filter_result(
        {
            "mode": "product_compare",
            "message": "Сравнение продуктов",
            "products": [
                {"code": "8914", "name": "Фиксированный доход 1 год"},
                {"code": "8959", "name": "Фиксированный доход и Альфа-Вклад"},
            ],
        },
        SQL_CONTEXT,
    )

    assert [product["code"] for product in result["products"]] == [
        "8914",
        "8959",
    ]


@pytest.mark.unit
def test_product_filter_contract_accepts_same_code_comparison_products() -> None:
    result = product_filter.validate_product_filter_result(
        {
            "mode": "product_compare",
            "message": "Сравнение продуктов",
            "products": [
                {
                    "code": "7695",
                    "name": "Юнит Линк Активные облигации",
                    "is_active": "Действующий",
                },
                {
                    "code": "7695",
                    "name": "Юнит Линк Стратегия роста",
                    "is_active": "Действующий",
                },
            ],
        },
        SQL_CONTEXT,
    )

    assert [product["name"] for product in result["products"]] == [
        "Юнит Линк Активные облигации",
        "Юнит Линк Стратегия роста",
    ]


@pytest.mark.unit
def test_product_filter_contract_rejects_info_mode() -> None:
    with pytest.raises(ValueError, match="invalid mode"):
        product_filter.validate_product_filter_result(
            {"mode": "product_card", "message": "x"},
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_filter_factories_split_tools_without_response_schema() -> None:
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
    assert getattr(format_agent, "output_schema", None) is None
    assert format_agent.generate_content_config["temperature"] == 0.0


@pytest.mark.unit
def test_product_filter_format_prompt_requires_multiline_product_output() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    prompt = (
        repo_root
        / "kb_storage"
        / "prompts"
        / "product_filter_format"
        / "product_filter_format_agent_prompt.md"
    ).read_text(encoding="utf-8")

    assert "Переносы строк для `product_filter` и `product_compare`" in prompt
    assert "кодируй каждый перенос строки как `\\n`" in prompt
    assert "используй `\\n\\n` между заголовком" in prompt
    assert "Никогда не объединяй их в одну строку" in prompt
    assert "Сортируй строки продуктов по `code` по возрастанию" in prompt
    assert "Каждое свойство должно принадлежать ровно одной группе" in prompt
    assert "### Пример отображения `product_compare`" in prompt
    assert "`Активный продукт` нельзя выводить под каждым продуктом" in prompt
    assert "Не оборачивай JSON в Markdown-блоки" in prompt
    assert "Объект должен содержать ровно эти ключи" in prompt
    assert "Запрещено:" in prompt
    assert "Перед ответом молча проверь" in prompt


@pytest.mark.unit
def test_product_filter_content_prompt_rejects_ambiguous_comparison() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    prompt = (
        repo_root
        / "kb_storage"
        / "prompts"
        / "product_filter_content"
        / "product_filter_content_agent_prompt.md"
    ).read_text(encoding="utf-8")

    assert "Не выбирай первый, наиболее" in prompt
    assert "Для `status=\"ok\"` верни ровно две SQL-строки" in prompt
    assert "`clarification_options` пустым" in prompt
    assert "содержит дополнительную строку" in prompt
