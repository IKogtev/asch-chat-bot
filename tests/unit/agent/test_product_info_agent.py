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
    config_stub.PRODUCT_CONTENT_MAX_OUTPUT_TOKENS = 6000
    config_stub.PRODUCT_FORMATTER_MAX_OUTPUT_TOKENS = 6000
    config_stub.LLM_PRESENCE_PENALTY = 1.5
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
def test_product_info_contract_supplies_clarification_message_when_formatter_omits_it() -> None:
    result = product_info.validate_product_info_result(
        {
            "mode": "needs_clarification",
            "message": "",
            "clarification_options": [
                {"code": "8914", "name": "Фиксированный доход 1 год"},
                {
                    "code": "8959",
                    "name": "Фиксированный доход 1 год + Альфа-Вклад Актив",
                },
            ],
        },
        SQL_CONTEXT,
    )

    assert result["message"] == "Нашла несколько подходящих продуктов. Уточни, какой нужен:"
    assert len(result["clarification_options"]) == 2


@pytest.mark.unit
def test_product_info_rejects_filter_mode() -> None:
    with pytest.raises(ValueError, match="invalid mode"):
        product_info.validate_product_info_result(
            {"mode": "product_filter", "message": "x"},
            SQL_CONTEXT,
        )


@pytest.mark.unit
def test_product_info_factories_split_tools_without_response_schema() -> None:
    content_agent = product_info.create_product_info_content_agent(model="content-model")
    format_agent = product_info.create_product_info_format_agent(model="format-model")

    assert content_agent.name == "product_info_content_agent"
    assert content_agent.include_contents == "none"
    assert content_agent.output_key == "product_info_content_result_json"
    assert len(content_agent.tools) == 1
    assert getattr(content_agent, "output_schema", None) is None
    assert content_agent.generate_content_config["presence_penalty"] == 1.5
    assert content_agent.generate_content_config["max_output_tokens"] == 6000

    assert format_agent.name == "product_info_format_agent"
    assert format_agent.include_contents == "none"
    assert format_agent.output_key == "product_info_result_json"
    assert format_agent.tools == []
    assert getattr(format_agent, "output_schema", None) is None
    assert format_agent.generate_content_config["temperature"] == 0.0
    assert format_agent.generate_content_config["presence_penalty"] == 1.5
    assert format_agent.generate_content_config["max_output_tokens"] == 6000


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
def test_product_info_content_prompt_preserves_resolver_status_and_identity() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    prompt = (
        repo_root
        / "kb_storage"
        / "prompts"
        / "product_info_content"
        / "product_info_content_agent_prompt.md"
    ).read_text(encoding="utf-8")

    assert "при полном отсутствии совпадений — по\nархивным" in prompt
    assert "Не меняй выбранный им\nстатус" in prompt
    assert "по `code`, `name` и\n  `is_active`" in prompt


@pytest.mark.unit
def test_product_info_format_prompt_requires_multiline_product_card() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    prompt = (
        repo_root
        / "kb_storage"
        / "prompts"
        / "product_info_format"
        / "product_info_format_agent_prompt.md"
    ).read_text(encoding="utf-8")

    assert "Каждое поле карточки выводи с новой строки" in prompt
    assert "используй `\\n` перед каждым следующим полем" in prompt
    assert "не объединяй поля в одну строку" in prompt
    assert "Не оборачивай JSON в Markdown-блоки" in prompt
    assert "Объект должен содержать ровно четыре ключа" in prompt
    assert "Запрещено:" in prompt
    assert "Перед ответом молча проверь" in prompt


@pytest.mark.unit
def test_product_info_format_prompt_requires_dd_mm_yyyy_date() -> None:
    repo_root = Path(__file__).resolve().parents[3]
    prompt = (
        repo_root
        / "kb_storage"
        / "prompts"
        / "product_info_format"
        / "product_info_format_agent_prompt.md"
    ).read_text(encoding="utf-8")

    assert "Значение `Дата ввода` форматируй как `DD-MM-YYYY`" in prompt
    assert "`2026-05-20T00:00:00.000Z` преобразуй в `20-05-2026`" in prompt
