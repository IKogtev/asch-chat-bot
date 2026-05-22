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

    adk_agents_stub = types.ModuleType("google.adk.agents")
    adk_agents_stub.LlmAgent = type("LlmAgent", (), {})

    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = type("LiteLlm", (), {})

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.prompt_loader"] = prompt_loader_stub
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
VALIDATION_CONTEXT = {}


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
            "route": "kb_answer",
            "intent": "smalltalk",
            "reason": "casual talk",
            "search_query": "",
        },
        VALIDATION_CONTEXT,
    )

    assert result["route"] == "kb_answer"
    assert result["intent"] == "smalltalk"


@pytest.mark.unit
@pytest.mark.parametrize(
    "intent",
    ["product_card", "product_kit", "product_filter", "product_compare"],
)
def test_validate_dispatcher_result_accepts_product_selection_with_query(intent: str) -> None:
    result = validate_dispatcher_result(
        {
            "status": "ok",
            "route": "product_selection",
            "intent": intent,
            "reason": "product comparison",
            "search_query": "Fort Knox and protected capital",
        },
        VALIDATION_CONTEXT,
    )

    assert result["route"] == "product_selection"
    assert result["intent"] == intent
    assert result["search_query"] == "Fort Knox and protected capital"


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
                "search_query": "q",
            },
            ("dispatcher_agent", "basic_fields", "invalid status"),
        ),
        (
            {
                "status": "ok",
                "route": "other",
                "intent": "doc_search",
                "reason": "x",
                "search_query": "q",
            },
            ("dispatcher_agent", "basic_fields", "invalid route"),
        ),
        (
            {
                "status": "ok",
                "route": "doc_search",
                "intent": "other",
                "reason": "x",
                "search_query": "q",
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
                "route": "kb_answer",
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
                "search_query": "capital protection",
            },
            VALIDATION_CONTEXT,
        )

    assert "product intents must use route='product_selection'" in str(exc.value)


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

    assert "search_query is required for doc_search, kb_answer, and product intents" in str(exc.value)


@pytest.mark.unit
def test_validate_dispatcher_result_requires_search_query_for_product_intent() -> None:
    with pytest.raises(ValueError) as exc:
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "product_selection",
                "intent": "product_card",
                "reason": "missing query",
                "search_query": "",
            },
            VALIDATION_CONTEXT,
        )

    assert "search_query is required for doc_search, kb_answer, and product intents" in str(exc.value)


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
                "route": "product_selection",
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
