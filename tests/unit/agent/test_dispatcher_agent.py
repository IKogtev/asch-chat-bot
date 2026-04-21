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


@pytest.mark.unit
def test_validate_dispatcher_result_accepts_doc_search_with_query() -> None:
    result = validate_dispatcher_result(
        {
            "status": "ok",
            "route": "doc_search",
            "intent": "doc_search",
            "reason": "user asks to find docs",
            "search_query": "отпуск",
        }
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
        }
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
        }
    )

    assert result["route"] == "kb_answer"
    assert result["intent"] == "smalltalk"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("payload", "error"),
    [
        (
            {
                "status": "bad",
                "route": "doc_search",
                "intent": "doc_search",
                "reason": "x",
                "search_query": "q",
            },
            "Invalid status",
        ),
        (
            {
                "status": "ok",
                "route": "other",
                "intent": "doc_search",
                "reason": "x",
                "search_query": "q",
            },
            "Invalid route",
        ),
        (
            {
                "status": "ok",
                "route": "doc_search",
                "intent": "other",
                "reason": "x",
                "search_query": "q",
            },
            "Invalid intent",
        ),
    ],
)
def test_validate_dispatcher_result_rejects_invalid_basic_fields(payload, error: str) -> None:
    with pytest.raises(ValueError, match=error):
        validate_dispatcher_result(payload)


@pytest.mark.unit
def test_validate_dispatcher_result_rejects_doc_route_intent_with_wrong_route() -> None:
    with pytest.raises(ValueError, match="doc_search route intents must use route=doc_search"):
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "kb_answer",
                "intent": "show_all",
                "reason": "wrong route",
                "search_query": "",
            }
        )


@pytest.mark.unit
def test_validate_dispatcher_result_rejects_kb_intent_with_wrong_route() -> None:
    with pytest.raises(ValueError, match="intent=kb_answer\\|smalltalk must use route=kb_answer"):
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "doc_search",
                "intent": "kb_answer",
                "reason": "wrong route",
                "search_query": "q",
            }
        )


@pytest.mark.unit
def test_validate_dispatcher_result_requires_search_query_for_main_intent() -> None:
    with pytest.raises(ValueError, match="search_query is required"):
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "doc_search",
                "intent": "doc_search",
                "reason": "missing query",
                "search_query": "",
            }
        )


@pytest.mark.unit
def test_validate_dispatcher_result_requires_reason() -> None:
    with pytest.raises(ValueError, match="reason is required"):
        validate_dispatcher_result(
            {
                "status": "ok",
                "route": "kb_answer",
                "intent": "smalltalk",
                "reason": "",
                "search_query": "",
            }
        )
