import importlib.util
import sys
import types
from dataclasses import dataclass
from pathlib import Path

import pytest


def _build_logger():
    return types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )


def _load_rootagent_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "rootagent.py"

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = [str(repo_root / "agent")]
    agents_pkg = types.ModuleType("agent.agents")
    agents_pkg.__path__ = [str(repo_root / "agent" / "agents")]

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.setup_logger = lambda *args, **kwargs: _build_logger()

    config_stub = types.ModuleType("agent.config")
    config_stub.DEBUG_EXCEPTIONS = False
    config_stub.FAQ_DOCUMENTS_COLLECTION = "faq"
    config_stub.KB_DOCUMENTS_COLLECTION = "kb"

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.truncate_for_log = lambda text, max_length=200: (text or "")[:max_length]
    helpers_stub.format_text_answer = lambda text: str(text).strip()
    helpers_stub.format_reject_answer = lambda text: str(text).strip()

    async def _fake_run_json_leaf_agent(**kwargs):
        if False:
            yield None

    json_leaf_runner_stub = types.ModuleType("agent.json_leaf_runner")
    json_leaf_runner_stub.run_json_leaf_agent = _fake_run_json_leaf_agent

    owasp_stub = types.ModuleType("agent.agents.owasp_agent")
    owasp_stub.validate_owasp_result = lambda data: data

    dispatcher_stub = types.ModuleType("agent.agents.dispatcher_agent")
    dispatcher_stub.validate_dispatcher_result = lambda data: data

    kb_answer_stub = types.ModuleType("agent.agents.kb_answer_agent")
    kb_answer_stub.validate_kb_answer_result = lambda data: data

    doc_search_stub = types.ModuleType("agent.agents.doc_search_orchestrator")
    doc_search_stub.DocSearchOrchestrator = type(
        "DocSearchOrchestrator",
        (),
        {"run_async": lambda self, ctx: _fake_run_json_leaf_agent()},
    )

    genai_types_stub = types.ModuleType("google.genai.types")
    google_pkg = types.ModuleType("google")
    genai_pkg = types.ModuleType("google.genai")
    adk_pkg = types.ModuleType("google.adk")

    @dataclass
    class Part:
        text: str | None = None

    @dataclass
    class Content:
        role: str
        parts: list

    genai_types_stub.Part = Part
    genai_types_stub.Content = Content
    genai_pkg.types = genai_types_stub

    adk_agents_stub = types.ModuleType("google.adk.agents")

    class BaseAgent:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)
            self.name = kwargs.get("name")
            self.sub_agents = kwargs.get("sub_agents", [])

    adk_agents_stub.BaseAgent = BaseAgent
    adk_agents_stub.LlmAgent = type("LlmAgent", (), {})
    adk_agents_stub.InvocationContext = type("InvocationContext", (), {})

    adk_events_stub = types.ModuleType("google.adk.events")

    @dataclass
    class EventActions:
        end_of_agent: bool = False

    @dataclass
    class Event:
        author: str
        invocation_id: str
        content: Content
        actions: EventActions

    adk_events_stub.Event = Event
    adk_events_stub.EventActions = EventActions

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.config"] = config_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.json_leaf_runner"] = json_leaf_runner_stub
    sys.modules["agent.agents.owasp_agent"] = owasp_stub
    sys.modules["agent.agents.dispatcher_agent"] = dispatcher_stub
    sys.modules["agent.agents.kb_answer_agent"] = kb_answer_stub
    sys.modules["agent.agents.doc_search_orchestrator"] = doc_search_stub
    sys.modules["google"] = google_pkg
    sys.modules["google.genai"] = genai_pkg
    sys.modules["google.genai.types"] = genai_types_stub
    sys.modules["google.adk"] = adk_pkg
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.events"] = adk_events_stub

    spec = importlib.util.spec_from_file_location("agent.rootagent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.rootagent"] = module
    spec.loader.exec_module(module)
    return module


rootagent_module = _load_rootagent_module()
RootAgent = rootagent_module.RootAgent
is_bot_user_profile_injection_message = rootagent_module.is_bot_user_profile_injection_message


def _make_agent() -> RootAgent:
    fake_subagent = object()
    fake_doc_orchestrator = type("DocSearchOrchestratorFake", (), {"run_async": lambda self, ctx: ()})()
    return RootAgent(
        owasp_agent=fake_subagent,
        dispatcher_agent=fake_subagent,
        doc_search_orchestrator=fake_doc_orchestrator,
        kb_answer_agent=fake_subagent,
    )


def _make_ctx(
    *,
    user_state=None,
    session_state=None,
    parts=None,
    invocation_id="inv-1",
):
    return types.SimpleNamespace(
        user=types.SimpleNamespace(state=user_state or {}),
        session=types.SimpleNamespace(state=session_state or {}),
        user_content=types.SimpleNamespace(parts=parts or []),
        invocation_id=invocation_id,
    )


@pytest.mark.unit
def test_is_bot_user_profile_injection_message_detects_prefix_after_spaces() -> None:
    text = "   " + rootagent_module.BOT_USER_PROFILE_MESSAGE_PREFIX + " данные"

    assert is_bot_user_profile_injection_message(text) is True
    assert is_bot_user_profile_injection_message("обычное сообщение") is False


@pytest.mark.unit
def test_get_user_profile_prefers_user_state_and_falls_back_to_session() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        user_state={"first_name": "Иван", "region": ""},
        session_state={"region": "Москва", "username": "ivanov"},
    )

    profile = agent._get_user_profile(ctx)

    assert profile == {
        "first_name": "Иван",
        "region": "Москва",
        "username": "ivanov",
    }


@pytest.mark.unit
def test_extract_user_text_joins_text_parts_and_skips_empty_parts() -> None:
    parts = [
        types.SimpleNamespace(text="Первая строка"),
        types.SimpleNamespace(text=None),
        types.SimpleNamespace(text="Вторая строка"),
    ]
    ctx = _make_ctx(parts=parts)

    assert RootAgent._extract_user_text(ctx) == "Первая строка\nВторая строка"


@pytest.mark.unit
def test_extract_user_text_returns_empty_string_when_no_parts() -> None:
    ctx = _make_ctx(parts=[])

    assert RootAgent._extract_user_text(ctx) == ""


@pytest.mark.unit
def test_build_final_event_creates_end_of_agent_event() -> None:
    ctx = _make_ctx(invocation_id="abc")

    event = RootAgent._build_final_event(ctx, "Ответ")

    assert event.author == "root_agent"
    assert event.invocation_id == "abc"
    assert event.content.role == "model"
    assert event.content.parts[0].text == "Ответ"
    assert event.actions.end_of_agent is True


@pytest.mark.unit
def test_clear_state_keys_removes_requested_keys_only() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={"a": 1, "b": 2, "c": 3})

    agent._clear_state_keys(ctx, ["a", "c", "missing"])

    assert ctx.session.state == {"b": 2}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("all", "show_all"),
        ("more", "show_more"),
        ("next", "show_more"),
        ("something else", None),
    ],
)
def test_pagination_intent_from_message_detects_short_commands(text: str, expected: str | None) -> None:
    assert RootAgent._pagination_intent_from_message(text) == expected


@pytest.mark.unit
def test_get_required_state_dict_returns_dict_value() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={"payload": {"x": 1}})

    assert agent._get_required_state_dict(ctx, "payload") == {"x": 1}


@pytest.mark.unit
def test_get_required_state_dict_raises_for_non_dict() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={"payload": "text"})

    with pytest.raises(ValueError, match="must be dict"):
        agent._get_required_state_dict(ctx, "payload")


@pytest.mark.unit
def test_get_required_state_text_returns_string_value() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={"payload": "value"})

    assert agent._get_required_state_text(ctx, "payload") == "value"


@pytest.mark.unit
def test_get_required_state_text_raises_for_non_string() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={"payload": {"x": 1}})

    with pytest.raises(ValueError, match="must be str"):
        agent._get_required_state_text(ctx, "payload")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_kb_answer_sets_expected_state_and_final_text() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        user_state={"first_name": "Иван"},
        session_state={},
    )

    async def fake_run_json_leaf_agent(**kwargs):
        ctx.session.state["_kb_answer_result_parsed"] = {
            "status": "ok",
            "mode": "text_answer",
            "message": " Готовый ответ ",
            "source": "faq_search",
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [event async for event in agent._handle_kb_answer(ctx, "Исходный вопрос", "", "kb_answer")]

    assert events == []
    assert ctx.session.state["first_name"] == "Иван"
    assert ctx.session.state["search_query"] == "Исходный вопрос"
    assert ctx.session.state["faq_collection"] == "faq"
    assert ctx.session.state["kb_answer_collection"] == "kb"
    assert ctx.session.state["intent"] == "kb_answer"
    assert ctx.session.state["_root_final_text"] == "Готовый ответ"
