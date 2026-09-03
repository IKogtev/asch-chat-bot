import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_dispatcher_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "agents" / "dispatcher_agent.py"

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

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.load_prompt = lambda *args, **kwargs: "prompt"

    prompt_loader_stub = types.ModuleType("agent.prompt_loader")
    prompt_loader_stub.start_prompt_watcher = lambda *args, **kwargs: None

    config_stub = types.ModuleType("agent.config")
    config_stub.DISPATCHER_TEMPERATURE = 0.2
    config_stub.DISPATCHER_MAX_OUTPUT_TOKENS = 4000
    config_stub.LLM_MAX_OUTPUT_TOKENS = 4096
    config_stub.LLM_PRESENCE_PENALTY = 1.5

    adk_agents_stub = types.ModuleType("google.adk.agents")
    adk_agents_stub.LlmAgent = type("LlmAgent", (), {})

    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = type("LiteLlm", (), {})

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.prompt_loader"] = prompt_loader_stub
    sys.modules["agent.config"] = config_stub
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.models.lite_llm"] = lite_llm_stub

    spec = importlib.util.spec_from_file_location("agent.agents.dispatcher_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.agents.dispatcher_agent"] = module
    spec.loader.exec_module(module)
    return module


dispatcher_module = _load_dispatcher_module()
validate_dispatcher_result = dispatcher_module.validate_dispatcher_result
DispatcherResponseSchema = dispatcher_module.DispatcherResponseSchema
VALIDATION_CONTEXT = {}
TARGET_PRODUCT_FILTER_QUERY = "Какие активные продукты без риска и с гарантированным доходом?"
TARGET_PRODUCT_FILTER_SEARCH_QUERY = "активные продукты без риска и с гарантированным доходом"
TARGET_FOCUS_FILTER_QUERIES = ("Что сейчас в фокусе?", "что в фокусе")
TARGET_FOCUS_FILTER_SEARCH_QUERY = "покажи продукты в фокусе"


def _read_dispatcher_prompt() -> str:
    repo_root = Path(__file__).resolve().parents[3]
    prompt_path = repo_root / "kb_storage" / "prompts" / "dispatcher" / "dispatcher_agent_prompt.md"
    return prompt_path.read_text(encoding="utf-8").lower()


@pytest.mark.unit
def test_validate_dispatcher_result_accepts_doc_search_with_query() -> None:
    result = validate_dispatcher_result(
        {
            "status": "ok",
            "route": "doc_search",
            "intent": "doc_search",
            "reason": "user asks to find docs",
            "search_query": "отпуск",
        },
        VALIDATION_CONTEXT,
    )

    assert result["route"] == "doc_search"
    assert result["intent"] == "doc_search"
    assert result["search_query"] == "отпуск"


@pytest.mark.unit
def test_validate_dispatcher_result_accepts_follow_up_without_query() -> None:
    result = validate_dispatcher_result(
        {
            "status": "ok",
            "route": "doc_search",
            "intent": "show_more",
            "reason": "pagination",
            "search_query": "",
        },
        VALIDATION_CONTEXT,
    )

    assert result["intent"] == "show_more"
    assert result["search_query"] == ""


@pytest.mark.unit
def test_validate_dispatcher_result_accepts_smalltalk_without_query() -> None:
    result = validate_dispatcher_result(
        {
            "status": "ok",
            "route": "smalltalk",
            "intent": "smalltalk",
            "reason": "casual talk",
            "search_query": "",
        },
        VALIDATION_CONTEXT,
    )

    assert result["route"] == "smalltalk"
    assert result["intent"] == "smalltalk"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("route", "intent"),
    [
        ("product_info", "product_card"),
        ("product_info", "product_kit"),
        ("product_filter", "product_filter"),
        ("product_filter", "product_compare"),
        ("product_filter", "product_attribute_values"),
    ],
)
def test_validate_dispatcher_result_accepts_product_routes_with_query(route: str, intent: str) -> None:
    result = validate_dispatcher_result(
        {
            "status": "ok",
            "route": route,
            "intent": intent,
            "reason": "product comparison",
            "search_query": "Fort Knox и Защищенный капитал",
        },
        VALIDATION_CONTEXT,
    )

    assert result["route"] == route
    assert result["intent"] == intent
    assert result["search_query"] == "Fort Knox и Защищенный капитал"


@pytest.mark.unit
def test_validate_dispatcher_result_accepts_advisor_recommendation() -> None:
    result = validate_dispatcher_result(
        {
            "status": "ok",
            "route": "advisor",
            "intent": "advisor_recommendation",
            "reason": "advisor_recommendation",
            "search_query": "осторожный клиент с горизонтом семь лет",
        },
        VALIDATION_CONTEXT,
    )

    assert result["route"] == "advisor"
    assert result["intent"] == "advisor_recommendation"


@pytest.mark.unit
def test_dispatcher_schema_heals_advisor_route_from_intent() -> None:
    result = DispatcherResponseSchema(
        status="ok",
        route="kb_answer",
        intent="advisor_recommendation",
        reason="advisor_recommendation",
        search_query="что предложить этому клиенту",
    )

    assert result.route == "advisor"


@pytest.mark.unit
def test_dispatcher_prompt_routes_active_no_risk_guaranteed_income_to_product_filter() -> None:
    prompt = _read_dispatcher_prompt()

    assert "покажи активные продукты" in prompt
    assert "`product_filter`" in prompt


@pytest.mark.unit
def test_dispatcher_prompt_routes_focus_questions_to_product_filter() -> None:
    prompt = _read_dispatcher_prompt()

    assert "что сейчас в фокусе" in prompt
    assert "`product_filter`" in prompt


@pytest.mark.unit
def test_dispatcher_prompt_contains_advisor_route_matrix() -> None:
    prompt = _read_dispatcher_prompt()

    # Примеры покрывают новый запрос, уточнение профиля и объяснение рекомендации.
    assert "клиенту 30 лет, имеет опыт инвестирования" in prompt
    assert "клиент хочет спасти свои большие накопления от инфляции" in prompt
    assert "клиент 50 лет, хочет заниматься своим здоровьем" in prompt
    assert "что предложить осторожному клиенту с горизонтом 7 лет" in prompt
    assert "клиент всё-таки не готов к потере капитала" in prompt
    assert "почему вариант 3 хуже 1" in prompt
    assert 'route="advisor"' in prompt
    assert 'intent="advisor_recommendation"' in prompt


@pytest.mark.unit
def test_dispatcher_prompt_preserves_advisor_route_boundaries() -> None:
    prompt = _read_dispatcher_prompt()

    assert "список только по параметрам каталога остаётся в `product_filter`" in prompt
    assert "сравнение названных продуктов остаётся в `product_filter`" in prompt
    assert "карточка и комплект продукта остаются в `product_info`" in prompt
    assert "общий вопрос о правилах продукта остаётся в `kb_answer`" in prompt


@pytest.mark.unit
def test_assistant_capabilities_smalltalk_examples_include_conversational_variants() -> None:
    examples = dispatcher_module.ASSISTANT_CAPABILITIES_SMALLTALK_EXAMPLES

    assert "что ты умеешь" in examples
    assert "что умеешь" in examples
    assert "чем можешь помочь" in examples
    assert "на что способен" in examples


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "parts"),
    [
        (
            {
                "status": "bad",
                "route": "doc_search",
                "intent": "doc_search",
                "reason": "x",
                "search_query": "запрос",
            },
            ("dispatcher_agent", "basic_fields", "invalid status"),
        ),
        (
            {
                "status": "ok",
                "route": "other",
                "intent": "doc_search",
                "reason": "x",
                "search_query": "запрос",
            },
            ("dispatcher_agent", "basic_fields", "invalid route"),
        ),
        (
            {
                "status": "ok",
                "route": "doc_search",
                "intent": "other",
                "reason": "x",
                "search_query": "запрос",
            },
            ("dispatcher_agent", "basic_fields", "invalid intent"),
        ),
    ],
)
def test_validate_dispatcher_result_rejects_invalid_basic_fields(payload, parts) -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(payload, VALIDATION_CONTEXT)

    message = str(exc.value)
    for part in parts:
        assert part in message


@pytest.mark.unit
def test_validate_dispatcher_result_rejects_doc_route_intent_with_wrong_route() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "kb_answer",
                "intent": "show_all",
                "reason": "wrong route",
                "search_query": "",
            },
            VALIDATION_CONTEXT,
        )

    assert "doc_search intents must use route='doc_search'" in str(exc.value)


@pytest.mark.unit
def test_validate_dispatcher_result_rejects_smalltalk_with_query() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "smalltalk",
                "intent": "smalltalk",
                "reason": "casual talk",
                "search_query": "лишний запрос",
            },
            VALIDATION_CONTEXT,
        )

    assert "smalltalk must have empty search_query" in str(exc.value)


@pytest.mark.unit
def test_validate_dispatcher_result_rejects_product_intent_with_wrong_route() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "kb_answer",
                "intent": "product_filter",
                "reason": "wrong route",
                "search_query": "защита капитала",
            },
            VALIDATION_CONTEXT,
        )

    assert "product filter intents must use route='product_filter'" in str(exc.value)


@pytest.mark.unit
def test_validate_dispatcher_result_rejects_advisor_intent_with_wrong_route() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "kb_answer",
                "intent": "advisor_recommendation",
                "reason": "advisor_recommendation",
                "search_query": "подобрать продукты для этого клиента",
            },
            VALIDATION_CONTEXT,
        )

    assert "advisor recommendation intent must use route='advisor'" in str(exc.value)


@pytest.mark.unit
def test_validate_dispatcher_result_requires_search_query_for_main_intent() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "doc_search",
                "intent": "doc_search",
                "reason": "missing query",
                "search_query": "",
            },
            VALIDATION_CONTEXT,
        )

    assert "search_query is required for non-follow-up intents" in str(exc.value)


@pytest.mark.unit
def test_validate_dispatcher_result_requires_search_query_for_product_intent() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "product_info",
                "intent": "product_card",
                "reason": "missing query",
                "search_query": "",
            },
            VALIDATION_CONTEXT,
        )

    assert "search_query is required for non-follow-up intents" in str(exc.value)


@pytest.mark.unit
@pytest.mark.parametrize(
    "intent",
    ["product_recommendation", "product_explanation", "product_alternatives"],
)
def test_validate_dispatcher_result_rejects_removed_product_intents(intent: str) -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "product_info",
                "intent": intent,
                "reason": "removed intent",
                "search_query": "Fort Knox",
            },
            VALIDATION_CONTEXT,
        )

    assert "invalid intent" in str(exc.value)


@pytest.mark.unit
def test_validate_dispatcher_result_rejects_follow_up_with_query() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "doc_search",
                "intent": "show_more",
                "reason": "pagination",
                "search_query": "не должно быть",
            },
            VALIDATION_CONTEXT,
        )

    assert "follow-up intents must not carry search_query" in str(exc.value)


@pytest.mark.unit
def test_validate_dispatcher_result_requires_reason() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "kb_answer",
                "intent": "smalltalk",
                "reason": "",
                "search_query": "",
            },
            VALIDATION_CONTEXT,
        )

    assert "reason is required" in str(exc.value)
