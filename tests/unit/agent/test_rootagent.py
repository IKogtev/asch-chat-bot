import importlib.util
import json
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
    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.setup_logger = lambda *args, **kwargs: _build_logger()

    config_stub = types.ModuleType("agent.config")
    config_stub.DEBUG_EXCEPTIONS = False
    config_stub.FAQ_DOCUMENTS_COLLECTION = "faq"
    config_stub.KB_DOCUMENTS_COLLECTION = "kb"
    config_stub.AGENT_DIALOG_MEMORY_MAX_TURNS = 3
    config_stub.DATABASE_URL = "postgresql://test"
    config_stub.COMPARE_FRAZE = "сравни"
    config_stub.PRODUCT_CARD_KIT_OFFER = "комплект"

    stage_metrics_spec = importlib.util.spec_from_file_location(
        "agent.stage_metrics",
        repo_root / "agent" / "stage_metrics.py",
    )
    stage_metrics_module = importlib.util.module_from_spec(stage_metrics_spec)
    assert stage_metrics_spec is not None and stage_metrics_spec.loader is not None
    sys.modules["agent.stage_metrics"] = stage_metrics_module
    stage_metrics_spec.loader.exec_module(stage_metrics_module)

    glossary_stub = types.ModuleType("agent.glossary")

    class GlossaryLookup:
        async def find(self, text):
            return []

        async def expand_search_query(self, query):
            return query

    glossary_stub.GlossaryLookup = GlossaryLookup

    smart_fallback_spec = importlib.util.spec_from_file_location(
        "agent.smart_fallback",
        repo_root / "agent" / "smart_fallback.py",
    )
    smart_fallback_module = importlib.util.module_from_spec(smart_fallback_spec)
    assert smart_fallback_spec is not None and smart_fallback_spec.loader is not None
    sys.modules["agent.smart_fallback"] = smart_fallback_module
    smart_fallback_spec.loader.exec_module(smart_fallback_module)

    doc_search_format_stub = types.ModuleType("utils.doc_search_format")

    def _extract_download_ranks(text):
        text = str(text or "").strip()
        if text.isdigit():
            return [int(text)]
        return []

    doc_search_format_stub.extract_download_ranks = _extract_download_ranks

    asyncpg_stub = types.ModuleType("asyncpg")

    async def _asyncpg_connect(*args, **kwargs):
        raise RuntimeError("asyncpg stub")

    asyncpg_stub.connect = _asyncpg_connect

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.extract_json = lambda text: json.loads(text[text.find("{"): text.rfind("}") + 1])
    helpers_stub.truncate_for_log = lambda text, max_length=200: (text or "")[:max_length]
    helpers_stub.format_text_answer = lambda text: str(text).strip()
    helpers_stub.format_reject_answer = lambda text: str(text).strip()

    async def _fake_run_json_leaf_agent(**kwargs):
        if False:
            yield None

    class AgentValidationFailure(Exception):
        def __init__(self, *, log_label, validation_error, raw, user_message):
            self.log_label = log_label
            self.validation_error = validation_error
            self.raw = raw
            self.user_message = user_message
            super().__init__(f"{log_label}: {validation_error}")

    json_leaf_runner_stub = types.ModuleType("agent.json_leaf_runner")
    json_leaf_runner_stub.AgentValidationFailure = AgentValidationFailure
    json_leaf_runner_stub.run_json_leaf_agent = _fake_run_json_leaf_agent

    owasp_stub = types.ModuleType("agent.agents.owasp_agent")
    owasp_stub.validate_owasp_result = lambda data, context: data

    dispatcher_stub = types.ModuleType("agent.agents.dispatcher_agent")
    dispatcher_stub.validate_dispatcher_result = lambda data, context: data

    kb_answer_stub = types.ModuleType("agent.agents.kb_answer_agent")
    kb_answer_stub.validate_kb_answer_result = lambda data, context: data

    smalltalk_stub = types.ModuleType("agent.agents.smalltalk_agent")
    smalltalk_stub.validate_smalltalk_result = lambda data, context: data

    product_info_stub = types.ModuleType("agent.agents.product_info_contract")
    product_info_stub.ProductInfoResponseSchema = type(
        "ProductInfoResponseSchema",
        (),
        {},
    )
    product_info_stub.validate_product_info_result = lambda data, context: data

    product_filter_stub = types.ModuleType("agent.agents.product_filter_contract")
    product_filter_stub.ProductFilterResponseSchema = type(
        "ProductFilterResponseSchema",
        (),
        {},
    )
    product_filter_stub.validate_product_filter_result = lambda data, context: data

    product_resolver_stub = types.ModuleType("agent.product_resolver_service")

    class ProductResolverService:
        async def resolve_product(self, query):
            return types.SimpleNamespace(
                to_dict=lambda: {"status": "not_found", "mention": query}
            )

        async def resolve_products(self, query, expected_count=None):
            return types.SimpleNamespace(
                to_dict=lambda: {"status": "not_found", "items": []}
            )

        async def resolve_product_filter(self, query):
            return types.SimpleNamespace(
                to_dict=lambda: {"status": "not_found", "query": query, "product_codes": [], "products": []}
            )

    product_resolver_stub.ProductResolverService = ProductResolverService

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
        state_delta: dict | None = None

    @dataclass
    class Event:
        author: str
        invocation_id: str
        content: Content
        actions: EventActions
        timestamp: float = 0.0

    adk_events_stub.Event = Event
    adk_events_stub.EventActions = EventActions

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["utils.doc_search_format"] = doc_search_format_stub
    sys.modules["agent.config"] = config_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.json_leaf_runner"] = json_leaf_runner_stub
    sys.modules["agent.stage_metrics"] = stage_metrics_module
    sys.modules["agent.glossary"] = glossary_stub
    sys.modules["agent.smart_fallback"] = smart_fallback_module
    sys.modules["agent.agents.owasp_agent"] = owasp_stub
    sys.modules["agent.agents.dispatcher_agent"] = dispatcher_stub
    sys.modules["agent.agents.kb_answer_agent"] = kb_answer_stub
    sys.modules["agent.agents.smalltalk_agent"] = smalltalk_stub
    sys.modules["agent.agents.product_info_contract"] = product_info_stub
    sys.modules["agent.agents.product_filter_contract"] = product_filter_stub
    sys.modules["agent.agents.doc_search_orchestrator"] = doc_search_stub
    sys.modules["agent.product_resolver_service"] = product_resolver_stub
    sys.modules["asyncpg"] = asyncpg_stub
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


def _make_agent(**kwargs) -> RootAgent:
    class EmptyGlossaryLookup:
        async def find(self, text):
            return []

        async def expand_search_query(self, text):
            return text

        async def build_doc_search_query(self, text):
            return await self.expand_search_query(text)

    class EmptyProductResolver:
        async def resolve_product(self, query):
            return types.SimpleNamespace(
                to_dict=lambda: {"status": "not_found", "mention": query}
            )

        async def resolve_products(self, query, expected_count=None):
            return types.SimpleNamespace(
                to_dict=lambda: {"status": "not_found", "items": []}
            )

        async def resolve_product_filter(self, query):
            return types.SimpleNamespace(
                to_dict=lambda: {"status": "not_found", "query": query, "product_codes": [], "products": []}
            )

        async def fetch_product_full_details(self, product_code):
            return {}

    kwargs.setdefault("glossary_lookup", EmptyGlossaryLookup())
    kwargs.setdefault("product_resolver", EmptyProductResolver())
    fake_subagent = object()
    fake_doc_orchestrator = type("DocSearchOrchestratorFake", (), {"run_async": lambda self, ctx: ()})()
    return RootAgent(
        owasp_agent=fake_subagent,
        dispatcher_agent=fake_subagent,
        doc_search_orchestrator=fake_doc_orchestrator,
        kb_answer_agent=fake_subagent,
        smalltalk_agent=fake_subagent,
        product_info_content_agent=fake_subagent,
        product_info_format_agent=fake_subagent,
        product_filter_content_agent=fake_subagent,
        product_filter_format_agent=fake_subagent,
        **kwargs,
    )


def _make_ctx(
    *,
    user_state=None,
    session_state=None,
    session_events=None,
    parts=None,
    invocation_id="inv-1",
):
    return types.SimpleNamespace(
        user=types.SimpleNamespace(state=user_state or {}),
        session=types.SimpleNamespace(
            id="session-1",
            app_name="agent",
            user_id="user-1",
            state=session_state or {},
            events=session_events or [],
            last_update_time=0,
        ),
        user_content=types.SimpleNamespace(parts=parts or []),
        invocation_id=invocation_id,
    )


def _make_event(role: str, text: str, idx: int, *, state_delta=None):
    return rootagent_module.Event(
        author=role,
        invocation_id=f"inv-{idx}",
        content=rootagent_module.genai_types.Content(
            role=role,
            parts=[rootagent_module.genai_types.Part(text=text)],
        ),
        actions=rootagent_module.EventActions(
            state_delta=state_delta,
        ),
        timestamp=float(idx),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_trim_dialog_memory_keeps_last_max_turns_and_state() -> None:
    agent = _make_agent()
    events = [
        _make_event("user", "u1", 1),
        _make_event("model", "m1", 2),
        _make_event("user", "u2", 3),
        _make_event("model", "m2", 4),
        _make_event("user", "u3", 5),
        _make_event("model", "m3", 6),
        _make_event("user", "u4", 7),
        _make_event("model", "m4", 8),
    ]
    ctx = _make_ctx(
        session_state={"product": "kept"},
        session_events=events,
    )

    await agent._trim_dialog_memory(ctx)

    assert [event.content.parts[0].text for event in ctx.session.events] == [
        "u2",
        "m2",
        "u3",
        "m3",
        "u4",
        "m4",
    ]
    assert ctx.session.state == {"product": "kept"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_trim_dialog_memory_keeps_event_state_delta_in_memory_only() -> None:
    agent = _make_agent()
    events = [
        _make_event("user", "u1", 1),
        _make_event("model", "m1", 2),
        _make_event("user", "u2", 3),
        _make_event("model", "m2", 4, state_delta={"product": "old"}),
        _make_event("user", "u3", 5),
        _make_event("model", "m3", 6),
        _make_event("user", "u4", 7),
        _make_event("model", "m4", 8),
    ]
    ctx = _make_ctx(
        session_state={"product": "current"},
        session_events=events,
    )

    await agent._trim_dialog_memory(ctx)

    assert ctx.session.state == {"product": "current"}
    retained_with_delta = [
        event
        for event in ctx.session.events
        if getattr(event.actions, "state_delta", None)
    ]
    assert retained_with_delta[0].actions.state_delta == {"product": "old"}


@pytest.mark.unit
def test_retained_dialog_memory_events_keeps_complete_recent_turns() -> None:
    events = [
        _make_event("user", "u1", 1),
        _make_event("model", "owasp1", 2),
        _make_event("model", "dispatcher1", 3),
        _make_event("model", "answer1", 4),
        _make_event("user", "u2", 5),
        _make_event("model", "owasp2", 6),
        _make_event("model", "answer2", 7),
        _make_event("user", "u3", 8),
        _make_event("model", "answer3", 9),
    ]

    retained = RootAgent._retained_dialog_memory_events(events, 2)

    assert [event.content.parts[0].text for event in retained] == [
        "u2",
        "owasp2",
        "answer2",
        "u3",
        "answer3",
    ]


@pytest.mark.unit
def test_retained_dialog_memory_events_does_nothing_when_disabled() -> None:
    events = [
        _make_event("user", "u1", 1),
        _make_event("model", "m1", 2),
    ]

    assert RootAgent._retained_dialog_memory_events(events, 0) == events


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
    assert event.actions.state_delta == {}


@pytest.mark.unit
def test_detects_response_schema_configuration_error() -> None:
    error = ValueError(
        "Failed to parse the parameter clarification_options of function "
        "set_model_response for automatic function calling."
    )

    assert rootagent_module.is_response_schema_configuration_error(error) is True
    assert (
        rootagent_module.is_response_schema_configuration_error(
            ValueError("database timeout")
        )
        is False
    )


@pytest.mark.unit
def test_build_final_event_includes_bot_action_state_delta() -> None:
    ctx = _make_ctx(
        invocation_id="abc",
        session_state={
            "_bot_action": {
                "type": "send_product_kit",
                "product_code": "2832",
            }
        },
    )

    event = RootAgent._build_final_event(ctx, "Answer")

    assert event.actions.state_delta == {
        "_bot_action": {
            "type": "send_product_kit",
            "product_code": "2832",
        }
    }


@pytest.mark.unit
def test_build_final_event_includes_flat_stage_timing() -> None:
    ctx = _make_ctx(
        invocation_id="abc",
        session_state={
            rootagent_module.STAGE_METRICS_STATE_KEY: {
                "owasp": {
                    "ms": 120,
                    "ttft_ms": 40,
                    "input_tokens": 10,
                    "output_tokens": 5,
                    "tool_calls": 0,
                    "model_turns": 1,
                },
                "kb_answer": {
                    "ms": 800,
                    "ttft_ms": 200,
                    "input_tokens": 100,
                    "output_tokens": 50,
                    "tool_calls": 3,
                    "model_turns": 2,
                },
            },
            "_dispatcher_result_parsed": {
                "route": "kb_answer",
                "intent": "faq",
                "search_query": "test",
            },
        },
    )

    event = RootAgent._build_final_event(ctx, "Answer")

    assert event.actions.state_delta[rootagent_module.TIMING_STATE_DELTA_KEY] == {
        "owasp_ms": 120,
        "owasp_ttft_ms": 40,
        "owasp_input_tokens": 10,
        "owasp_output_tokens": 5,
        "owasp_tool_calls": 0,
        "owasp_model_turns": 1,
        "kb_answer_ms": 800,
        "kb_answer_ttft_ms": 200,
        "kb_answer_input_tokens": 100,
        "kb_answer_output_tokens": 50,
        "kb_answer_tool_calls": 3,
        "kb_answer_model_turns": 2,
        "route": "kb_answer",
        "intent": "faq",
    }


@pytest.mark.unit
def test_build_final_event_includes_product_dialog_context_state_delta() -> None:
    product_dialog_context = {
        "last_mode": "product_filter",
        "products": [{"code": "8914", "name": "Fort Knox 1 год"}],
        "selected_product": None,
    }
    ctx = _make_ctx(
        invocation_id="abc",
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: product_dialog_context,
        },
    )

    event = RootAgent._build_final_event(ctx, "Answer")

    assert event.actions.state_delta == {
        rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: product_dialog_context,
    }


@pytest.mark.unit
def test_clear_state_keys_removes_requested_keys_only() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={"a": 1, "b": 2, "c": 3})

    agent._clear_state_keys(ctx, ["a", "c", "missing"])

    assert ctx.session.state == {"b": 2}


@pytest.mark.unit
def test_reset_turn_state_removes_turn_specific_state_keys() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            "user_query": "старый вопрос",
            "search_query": "старый поиск",
            "product_info_search_query": "old_info",
            "product_info_intent": "product_card",
            "product_filter_search_query": "old_filter",
            "product_filter_intent": "product_filter",
            "dispatcher_user_query": "старый запрос",
            "_owasp_result_parsed": {"status": "ok"},
            "_root_final_text": "старый ответ",
            "persistent": "keep",
        }
    )

    agent._reset_turn_state(ctx)

    assert ctx.session.state == {"persistent": "keep"}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_resets_turn_state_before_processing_new_message() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="новый вопрос")],
        session_state={
            "user_query": "старый вопрос",
            "search_query": "старый поиск",
            "product_info_search_query": "old_info",
            "product_info_intent": "product_card",
            "product_filter_search_query": "old_filter",
            "product_filter_intent": "product_filter",
            "dispatcher_user_query": "старый запрос",
            "_owasp_result_parsed": {"status": "ok"},
            "_root_final_text": "старый ответ",
            "persistent": "keep",
        },
    )

    async def fake_run_json_leaf_agent(**kwargs):
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            ctx.session.state["_dispatcher_result_parsed"] = {
                "status": "ok",
                "route": "kb_answer",
                "intent": "kb_answer",
                "reason": "ok",
                "search_query": "новый вопрос",
            }
            if False:
                yield None
            return

        if False:
            yield None

    async def fake_handle_kb_answer(ctx_, user_message, search_query, intent):
        assert user_message == "новый вопрос"
        assert search_query == "новый вопрос"
        assert intent == "kb_answer"
        ctx_.session.state["search_query"] = "новый вопрос"
        ctx_.session.state["_root_final_text"] = "ответ"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_kb_answer = fake_handle_kb_answer
    agent.doc_search_orchestrator.run_async = lambda ctx_: ()

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "ответ"
    assert ctx.session.state["user_query"] == "новый вопрос"
    assert ctx.session.state["search_query"] == "новый вопрос"
    assert ctx.session.state["dispatcher_user_query"] == "новый вопрос"
    assert ctx.session.state.get("product_info_search_query") is None
    assert ctx.session.state.get("product_filter_search_query") is None
    assert ctx.session.state.get("_root_final_text") == "ответ"
    assert ctx.session.state["persistent"] == "keep"


@pytest.mark.unit
def test_append_recent_message_keeps_only_bounded_history() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={})

    for idx in range(rootagent_module.OWASP_CONTEXT_WINDOW + 2):
        agent._append_recent_message(ctx, "user", f"message-{idx}")

    history = ctx.session.state[rootagent_module.OWASP_HISTORY_STATE_KEY]
    assert len(history) == rootagent_module.OWASP_CONTEXT_WINDOW
    assert history[0]["text"] == "message-2"
    assert history[-1]["text"] == f"message-{rootagent_module.OWASP_CONTEXT_WINDOW + 1}"


@pytest.mark.unit
def test_prepare_owasp_input_uses_current_message_and_recent_history() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            rootagent_module.OWASP_HISTORY_STATE_KEY: [
                {"role": "user", "text": "старый вредоносный запрос"},
                {"role": "assistant", "text": "отказ"},
            ]
        }
    )

    agent._prepare_owasp_input(ctx, "нормальный новый вопрос")

    assert ctx.session.state["owasp_current_user_message"] == "нормальный новый вопрос"
    recent = json.loads(ctx.session.state["owasp_recent_messages_json"])
    assert recent == [
        {"role": "user", "text": "старый вредоносный запрос"},
        {"role": "assistant", "text": "отказ"},
    ]
    assert all(item["text"] != "нормальный новый вопрос" for item in recent)


@pytest.mark.unit
def test_build_final_event_with_history_appends_current_turn() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={})

    event = agent._build_final_event_with_history(ctx, "новый вопрос", "новый ответ")

    assert event.content.parts[0].text == "новый ответ"
    history = ctx.session.state[rootagent_module.OWASP_HISTORY_STATE_KEY]
    assert history == [
        {"role": "user", "text": "новый вопрос"},
        {"role": "assistant", "text": "новый ответ"},
    ]


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
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("все", "show_all"),
        ("покажи все", "show_all"),
        ("еще", "show_more"),
        ("еще документы", "show_more"),
    ],
)
def test_pagination_intent_from_message_detects_russian_short_commands(
    text: str,
    expected: str,
) -> None:
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
    assert isinstance(ctx.session.state["_root_final_text"], str)
    assert ctx.session.state["_root_final_text"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_info_sets_expected_state_and_final_text() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        user_state={"first_name": "Ivan"},
        session_state={},
    )
    calls = []

    async def fake_run_json_leaf_agent(**kwargs):
        calls.append(kwargs["output_key"])
        if kwargs["output_key"] == "product_info_content_result_json":
            assert kwargs["agent"] is agent.product_info_content_agent
            assert kwargs["validator"] is None
            ctx.session.state["_product_info_content_result_parsed"] = {
                "status": "ok",
                "rows": [{"code": "2832", "name": "Fort Knox"}],
            }
            ctx.session.state["_product_info_content_tool_calls"] = ["execute_sql"]
            if False:
                yield None
            return

        assert kwargs["agent"] is agent.product_info_format_agent
        assert kwargs["output_key"] == "product_info_result_json"
        assert kwargs["parsed_state_key"] == "_product_info_result_parsed"
        assert kwargs["response_schema"] is rootagent_module.ProductInfoResponseSchema
        assert (
            kwargs["validation_tool_calls_state_key"]
            == "_product_info_content_tool_calls"
        )
        ctx.session.state["_product_info_result_parsed"] = {
            "status": "ok",
            "mode": "product_card",
            "message": " Product selection answer ",
            "used_tables": ["products"],
            "resolved_product": {
                "code": "2832",
                "name": "Fort Knox",
                "folder_kit": "Fort Knox (2832)",
            },
            "clarification_options": [],
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_info(
            ctx,
            "Original question",
            "",
            "product_card",
        )
    ]

    assert events == []
    assert ctx.session.state["first_name"] == "Ivan"
    assert ctx.session.state["product_info_search_query"] == "Original question"
    assert ctx.session.state["product_info_intent"] == "product_card"
    assert ctx.session.state["_root_final_text"].startswith("Product selection answer")
    assert "_bot_action" not in ctx.session.state
    assert calls == [
        "product_info_content_result_json",
        "product_info_result_json",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_info_appends_clarification_options() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        ctx.session.state["_product_info_result_parsed"] = {
            "status": "ok",
            "mode": "needs_clarification",
            "message": "Choose product",
            "used_tables": ["products"],
            "resolved_product": None,
            "clarification_options": [
                {"code": "8958", "name": "Bundle Fort Knox 3+12 months"},
                {
                    "code": "8793",
                    "name": "Fort Knox 6 months",
                    "term": "short",
                    "currency": "RUB",
                },
            ],
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_info(
            ctx,
            "Original question",
            "Fort Knox",
            "product_card",
        )
    ]

    assert events == []
    assert ctx.session.state["_root_final_text"] == (
        "Choose product\n"
        "8958 Bundle Fort Knox 3+12 months\n"
        "8793 Fort Knox 6 months - short, RUB"
    )
    assert "_bot_action" not in ctx.session.state


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_filter_stores_products_and_adds_followup_question() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={})
    calls = []

    async def fake_run_json_leaf_agent(**kwargs):
        calls.append(kwargs["output_key"])
        if kwargs["output_key"] == "product_filter_content_result_json":
            assert kwargs["agent"] is agent.product_filter_content_agent
            assert kwargs["validator"] is None
            ctx.session.state["_product_filter_content_result_parsed"] = {
                "status": "ok",
                "rows": [{"code": "2867"}],
            }
            ctx.session.state["_product_filter_content_tool_calls"] = ["execute_sql"]
            if False:
                yield None
            return

        assert kwargs["agent"] is agent.product_filter_format_agent
        assert kwargs["response_schema"] is rootagent_module.ProductFilterResponseSchema
        assert (
            kwargs["validation_tool_calls_state_key"]
            == "_product_filter_content_tool_calls"
        )
        ctx.session.state["_product_filter_result_parsed"] = {
            "status": "ok",
            "mode": "product_filter",
            "message": "Найдено продуктов: 1.\n2867 - Bundle Fort Knox 3+36 месяцев",
            "used_tables": ["products"],
            "resolved_product": None,
            "clarification_options": [],
            "products": [
                {
                    "code": "2867",
                    "name": "Bundle Fort Knox 3+36 месяцев",
                    "folder_kit": "Fort Knox (2867)",
                }
            ],
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_filter(
            ctx,
            "show products",
            "список продуктов",
            "product_filter",
        )
    ]

    assert events == []
    assert rootagent_module.PRODUCT_FILTER_FOLLOWUP_QUESTION in ctx.session.state["_root_final_text"]
    assert ctx.session.state[rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY] == {
        "last_mode": "product_filter",
        "products": [
            {
                "code": "2867",
                "name": "Bundle Fort Knox 3+36 месяцев",
                "folder_kit": "Fort Knox (2867)",
            }
        ],
        "selected_product": None,
    }
    assert calls == [
        "product_filter_content_result_json",
        "product_filter_result_json",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_product_format_retry_does_not_repeat_content_stage() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={})
    content_calls = 0
    format_calls = 0

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal content_calls, format_calls
        if kwargs["output_key"] == "product_info_content_result_json":
            content_calls += 1
            ctx.session.state["_product_info_content_result_parsed"] = {
                "status": "ok",
                "rows": [{"code": "2832", "name": "Fort Knox"}],
            }
            ctx.session.state["_product_info_content_tool_calls"] = ["execute_sql"]
            if False:
                yield None
            return

        format_calls += 1
        if format_calls == 1:
            raise rootagent_module.AgentValidationFailure(
                log_label="product_info_result_json",
                validation_error="message is required",
                raw='{"mode":"product_card"}',
                user_message=rootagent_module.VALIDATION_ERROR_USER_MESSAGE,
            )

        assert "message is required" in ctx.session.state[
            "product_info_format_correction"
        ]
        ctx.session.state["_product_info_result_parsed"] = {
            "mode": "product_card",
            "message": "Карточка продукта",
            "used_tables": ["products"],
            "resolved_product": {"code": "2832", "name": "Fort Knox"},
            "clarification_options": [],
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_info(
            ctx,
            "Покажи продукт",
            "Fort Knox",
            "product_card",
        )
    ]

    assert events == []
    assert content_calls == 1
    assert format_calls == 2
    assert ctx.session.state["_product_info_format_attempt"] == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_product_format_retry_stops_after_second_failure() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            "_product_info_content_tool_calls": ["execute_sql"],
            "_product_info_content_tool_events": [],
        }
    )
    format_calls = 0

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal format_calls
        format_calls += 1
        raise rootagent_module.AgentValidationFailure(
            log_label="product_info_result_json",
            validation_error=f"invalid format attempt {format_calls}",
            raw="{}",
            user_message=rootagent_module.VALIDATION_ERROR_USER_MESSAGE,
        )
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    with pytest.raises(
        rootagent_module.AgentValidationFailure,
        match="invalid format attempt 2",
    ):
        [
            event
            async for event in agent._run_product_format_agent(
                ctx=ctx,
                agent=agent.product_info_format_agent,
                output_key="product_info_result_json",
                parsed_state_key="_product_info_result_parsed",
                validator=rootagent_module.validate_product_info_result,
                response_schema=rootagent_module.ProductInfoResponseSchema,
                log_label="product_info_result_json",
                correction_state_key="product_info_format_correction",
                attempt_state_key="_product_info_format_attempt",
                validation_tool_calls_state_key="_product_info_content_tool_calls",
                validation_tool_events_state_key="_product_info_content_tool_events",
            )
        ]

    assert format_calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_filter_attribute_values_stores_context_and_adds_followup_question() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        ctx.session.state["_product_filter_result_parsed"] = {
            "status": "ok",
            "mode": "product_attribute_values",
            "message": "Available values:\n- RUB\n- CNY",
            "used_tables": ["products"],
            "resolved_product": None,
            "clarification_options": [],
            "products": [],
            "attribute_name": "currency",
            "attribute_column": "currency",
            "attribute_values": ["RUB", "CNY"],
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_filter(
            ctx,
            "which currencies exist",
            "which currencies exist",
            "product_attribute_values",
        )
    ]

    assert events == []
    assert rootagent_module.PRODUCT_ATTRIBUTE_FOLLOWUP_QUESTION in ctx.session.state["_root_final_text"]
    assert ctx.session.state[rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY] == {
        "last_mode": "product_attribute_values",
        "attribute_name": "currency",
        "attribute_column": "currency",
        "attribute_values": ["RUB", "CNY"],
        "products": [],
        "selected_product": None,
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_info_sets_bot_action_for_product_kit() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "product_card",
                "products": [{"code": "2832", "name": "Fort Knox"}],
                "selected_product": {"code": "2832", "name": "Fort Knox"},
            }
        }
    )

    async def fake_run_json_leaf_agent(**kwargs):
        ctx.session.state["_product_info_result_parsed"] = {
            "status": "ok",
            "mode": "product_kit",
            "message": " Kit answer ",
            "used_tables": ["products"],
            "resolved_product": {
                "code": "2832",
                "name": "Fort Knox",
                "folder_kit": "Fort Knox (2832)",
            },
            "clarification_options": [],
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_info(
            ctx,
            "Original question",
            "Fort Knox",
            "product_kit",
        )
    ]

    assert events == []
    assert ctx.session.state["_root_final_text"] == "Kit answer"
    assert ctx.session.state["_bot_action"] == {
        "type": "send_product_kit",
        "product_code": "2832",
        "product_name": "Fort Knox",
        "folder_kit": "Fort Knox (2832)",
    }
    assert ctx.session.state[rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY] == {
        "last_mode": "product_kit",
        "products": [{"code": "2832", "name": "Fort Knox"}],
        "selected_product": {
            "code": "2832",
            "name": "Fort Knox",
            "folder_kit": "Fort Knox (2832)",
        },
    }


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_filter_keeps_context_for_product_compare() -> None:
    product_dialog_context = {
        "last_mode": "product_card",
        "products": [{"code": "2832", "name": "Fort Knox"}],
        "selected_product": {"code": "2832", "name": "Fort Knox"},
    }
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: product_dialog_context,
        }
    )

    async def fake_run_json_leaf_agent(**kwargs):
        ctx.session.state["_product_filter_result_parsed"] = {
            "status": "ok",
            "mode": "product_compare",
            "message": " Compare answer ",
            "used_tables": ["products"],
            "comparison": [],
            "clarification_options": [],
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_filter(
            ctx,
            "Compare",
            "Fort Knox and other product",
            "product_compare",
        )
    ]

    assert events == []
    assert ctx.session.state["_root_final_text"] == "Compare answer"
    stored = ctx.session.state[rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY]
    assert stored["last_mode"] == "product_compare"
    assert stored["selected_product"] == product_dialog_context["selected_product"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_stops_chain_and_returns_generic_stub_on_dispatcher_validation_failure() -> None:
    agent = _make_agent()
    ctx = _make_ctx(parts=[types.SimpleNamespace(text="привет")], session_state={})
    kb_called = False
    doc_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        raise rootagent_module.AgentValidationFailure(
            log_label=kwargs["log_label"],
            validation_error="bad contract",
            raw='{"status":"bad"}',
            user_message=rootagent_module.VALIDATION_ERROR_USER_MESSAGE,
        )
        if False:
            yield None

    async def fake_handle_kb_answer(*args, **kwargs):
        nonlocal kb_called
        kb_called = True
        if False:
            yield None

    async def fake_doc_run_async(ctx):
        nonlocal doc_called
        doc_called = True
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_kb_answer = fake_handle_kb_answer
    agent.doc_search_orchestrator.run_async = fake_doc_run_async

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    expected_fallback = rootagent_module.generate_agent_fallback(
        "привет",
        error_type="validation_failure",
        agent_name="dispatcher",
        context={"validation_error": "bad contract"},
    )
    assert events[0].content.parts[0].text == expected_fallback
    assert kb_called is False
    assert doc_called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_uses_product_info_message_fallback_on_validation_failure() -> None:
    agent = _make_agent()
    ctx = _make_ctx(parts=[types.SimpleNamespace(text="show fort knox")], session_state={})
    debug_messages = []
    original_logger = rootagent_module.logger
    rootagent_module.logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: debug_messages.append(a[0] if a else ""),
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )

    async def fake_run_json_leaf_agent(**kwargs):
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            ctx.session.state["_dispatcher_result_parsed"] = {
                "status": "ok",
                "route": "product_info",
                "intent": "product_card",
                "reason": "product_card",
                "search_query": "fort knox",
            }
            if False:
                yield None
            return

        if False:
            yield None
        raise rootagent_module.AgentValidationFailure(
            log_label="product_info_result_json",
            validation_error="bad contract",
            raw=json.dumps(
                {
                    "status": "ok",
                    "mode": "needs_clarification",
                    "message": "Choose product",
                    "clarification_options": [
                        "Bundle Fort Knox 3+12 months (8958)",
                        "Fort Knox 6 months (8793)",
                    ],
                }
            ),
            user_message=rootagent_module.VALIDATION_ERROR_USER_MESSAGE,
        )
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    try:
        events = [event async for event in agent._run_async_impl(ctx)]
    finally:
        rootagent_module.logger = original_logger

    assert len(events) == 1
    assert events[0].content.parts[0].text == (
        "Choose product\n"
        "Bundle Fort Knox 3+12 months (8958)\n"
        "Fort Knox 6 months (8793)"
    )
    assert any(
        "product fallback diagnostics" in message
        for message in debug_messages
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_blocks_product_info_fallback_on_tool_usage_failure() -> None:
    agent = _make_agent()
    ctx = _make_ctx(parts=[types.SimpleNamespace(text="show product")], session_state={})
    debug_messages = []
    original_logger = rootagent_module.logger
    rootagent_module.logger = types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: debug_messages.append(a[0] if a else ""),
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )

    async def fake_run_json_leaf_agent(**kwargs):
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            ctx.session.state["_dispatcher_result_parsed"] = {
                "status": "ok",
                "route": "product_info",
                "intent": "product_card",
                "reason": "product_card",
                "search_query": "product",
            }
            if False:
                yield None
            return

        if False:
            yield None
        raise rootagent_module.AgentValidationFailure(
            log_label="product_info_result_json",
            validation_error="product_info_agent validation failed at tool_usage",
            raw=json.dumps(
                {
                    "status": "ok",
                    "mode": "product_card",
                    "message": "Unsafe generated card",
                    "resolved_product": {"code": "123", "name": "Generated product"},
                    "clarification_options": [],
                }
            ),
            user_message=rootagent_module.VALIDATION_ERROR_USER_MESSAGE,
        )
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    try:
        events = [event async for event in agent._run_async_impl(ctx)]
    finally:
        rootagent_module.logger = original_logger

    assert len(events) == 1
    fallback_text = events[0].content.parts[0].text
    assert "Не могу найти нужный продукт" in fallback_text
    assert "команду /reset" in fallback_text
    assert any(
        "product fallback diagnostics" in message
        for message in debug_messages
    )


@pytest.mark.unit
def test_product_compare_tool_usage_fallback_mentions_unconfirmed_sql_data() -> None:
    message = rootagent_module.generate_agent_fallback(
        "compare products",
        error_type="validation_failure",
        agent_name="product_filter",
        context={
            "validation_error": "product_filter_agent validation failed at tool_usage",
            "mode": "product_compare",
            "search_query": "compare products 111 and 222",
        },
    )

    assert "Не могу подтвердить данные для сравнения" in message
    assert "Укажи два точных названия или кода и критерии" in message
    assert "Сравни 8837 и 8914" in message


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_product_filter_to_product_agent_only() -> None:
    agent = _make_agent()
    ctx = _make_ctx(parts=[types.SimpleNamespace(text="compare products")], session_state={})
    product_called = False
    kb_called = False
    doc_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            ctx.session.state["_dispatcher_result_parsed"] = {
                "status": "ok",
                "route": "product_filter",
                "intent": "product_compare",
                "reason": "product comparison",
                "search_query": "Fort Knox and protected capital",
            }
            if False:
                yield None
            return

        if False:
            yield None

    async def fake_handle_product_filter(ctx, user_message, search_query, intent):
        nonlocal product_called
        product_called = True
        assert user_message == "compare products"
        assert search_query == "Fort Knox and protected capital"
        assert intent == "product_compare"
        ctx.session.state["_root_final_text"] = "product comparison answer"
        if False:
            yield None

    async def fake_handle_kb_answer(*args, **kwargs):
        nonlocal kb_called
        kb_called = True
        if False:
            yield None

    async def fake_doc_run_async(ctx):
        nonlocal doc_called
        doc_called = True
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_product_filter = fake_handle_product_filter
    agent._handle_kb_answer = fake_handle_kb_answer
    agent.doc_search_orchestrator.run_async = fake_doc_run_async

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "product comparison answer"
    assert product_called is True
    assert kb_called is False
    assert doc_called is False


@pytest.mark.unit
@pytest.mark.asyncio
@pytest.mark.parametrize("user_text", ["Что сейчас в фокусе?", "что в фокусе"])
async def test_run_async_impl_routes_focus_questions_to_product_filter(user_text: str) -> None:
    agent = _make_agent()
    ctx = _make_ctx(parts=[types.SimpleNamespace(text=user_text)], session_state={})
    product_called = False
    dispatcher_called = False
    kb_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            dispatcher_called = True
            ctx.session.state["_dispatcher_result_parsed"] = {
                "status": "ok",
                "route": "product_selection",
                "intent": "product_filter",
                "reason": "focus question",
                "search_query": "покажи продукты в фокусе",
            }
            if False:
                yield None
            return

        if False:
            yield None

    async def fake_handle_product_filter(ctx, user_message, search_query, intent):
        nonlocal product_called
        product_called = True
        assert user_message == user_text
        assert search_query == "покажи продукты в фокусе"
        assert intent == "product_filter"
        ctx.session.state["_root_final_text"] = "focus products"
        if False:
            yield None

    async def fake_handle_kb_answer(*args, **kwargs):
        nonlocal kb_called
        kb_called = True
        ctx.session.state["_root_final_text"] = "kb answer"

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_product_filter = fake_handle_product_filter
    agent._handle_kb_answer = fake_handle_kb_answer

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "focus products"
    assert product_called is True
    assert dispatcher_called is True
    assert kb_called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_attribute_value_followup_to_product_filter() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="CNY")],
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "product_attribute_values",
                "attribute_name": "currency",
                "attribute_column": "currency",
                "attribute_values": ["RUB", "CNY"],
                "products": [],
                "selected_product": None,
            }
        },
    )
    product_called = False
    dispatcher_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            dispatcher_called = True
            if False:
                yield None

    async def fake_handle_product_filter(ctx, user_message, search_query, intent):
        nonlocal product_called
        product_called = True
        assert user_message == "CNY"
        assert search_query == "покажи продукты, у которых currency: CNY"
        assert intent == "product_filter"
        ctx.session.state["_root_final_text"] = "filtered products"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_product_filter = fake_handle_product_filter

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "filtered products"
    assert product_called is True
    assert dispatcher_called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_product_card_followup_from_product_code_only() -> None:
    agent = _make_agent()
    product = {
        "code": "2867",
        "name": "Bundle Fort Knox 3+36 months",
    }
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="2867")],
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "product_filter",
                "products": [product],
                "selected_product": None,
            }
        },
    )
    product_called = False
    dispatcher_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            dispatcher_called = True
            if False:
                yield None

    async def fake_handle_product_info(ctx, user_message, search_query, intent):
        nonlocal product_called
        product_called = True
        assert user_message == "2867"
        assert search_query == "показать карточку продукта 2867"
        assert intent == "product_card"
        product_context = ctx.session.state[rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY]
        assert product_context["selected_product"] == product
        ctx.session.state["_root_final_text"] = "product card answer"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_product_info = fake_handle_product_info

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "product card answer"
    assert product_called is True
    assert dispatcher_called is False


@pytest.mark.unit
def test_store_needs_clarification_keeps_pending_compare_context() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            "product_filter_intent": "product_compare",
            "product_filter_search_query": "сравни ФД 3 года и ФД 1 год",
            "product_resolutions": {
                "status": "ambiguous",
                "items": [
                    {
                        "status": "resolved",
                        "product_code": "8941",
                        "product_name": "Фиксированный доход 3 года + Альфа-Вклад Актив",
                    },
                    {
                        "status": "ambiguous",
                        "product_code": None,
                        "product_name": None,
                        "options": [],
                    },
                ],
            },
        }
    )

    agent._store_product_dialog_context(
        ctx,
        {
            "mode": "needs_clarification",
            "clarification_options": [
                {"code": "8914", "name": "Фиксированный доход 1 год"},
                {"code": "8959", "name": "Фиксированный доход 1 год + Альфа-Вклад Актив"},
            ],
        },
    )

    stored = ctx.session.state[rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY]
    assert stored["last_mode"] == "needs_clarification"
    assert stored["pending_intent"] == "product_compare"
    assert stored["compare_resolved_products"] == [
        {
            "code": "8941",
            "name": "Фиксированный доход 3 года + Альфа-Вклад Актив",
        }
    ]
    assert [item["code"] for item in stored["clarification_options"]] == ["8914", "8959"]


@pytest.mark.unit
def test_product_followup_dispatch_resumes_compare_after_clarification_code() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            "last_intent": "product_compare",
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "needs_clarification",
                "pending_intent": "product_compare",
                "products": [
                    {"code": "8914", "name": "Фиксированный доход 1 год"},
                    {"code": "8959", "name": "Фиксированный доход 1 год + Альфа-Вклад Актив"},
                ],
                "clarification_options": [
                    {"code": "8914", "name": "Фиксированный доход 1 год"},
                    {"code": "8959", "name": "Фиксированный доход 1 год + Альфа-Вклад Актив"},
                ],
                "compare_resolved_products": [
                    {
                        "code": "8941",
                        "name": "Фиксированный доход 3 года + Альфа-Вклад Актив",
                    }
                ],
                "original_search_query": "сравни ФД 3 года и ФД 1 год",
                "selected_product": None,
            },
        }
    )

    dispatch = agent._product_followup_dispatch(
        ctx,
        "8914 Фиксированный доход 1 год",
    )

    assert dispatch is not None
    assert dispatch["route"] == "product_filter"
    assert dispatch["intent"] == "product_compare"
    assert dispatch["reason"] == "product_compare_clarification_followup"
    assert dispatch["search_query"] == "сравни продукты 8941 и 8914"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_product_card_followup_from_saved_product_list() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="параметры 2867")],
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "product_filter",
                "products": [
                    {
                        "code": "2867",
                        "name": "Bundle Fort Knox 3+36 месяцев",
                    }
                ],
                "selected_product": None,
            }
        },
    )
    product_called = False
    dispatcher_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            dispatcher_called = True
            if False:
                yield None

    async def fake_handle_product_info(ctx, user_message, search_query, intent):
        nonlocal product_called
        product_called = True
        assert user_message == "параметры 2867"
        assert search_query == "показать параметры продукта 2867"
        assert intent == "product_card"
        ctx.session.state["_root_final_text"] = "product card answer"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_product_info = fake_handle_product_info

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == rootagent_module.VALIDATION_ERROR_USER_MESSAGE
    assert product_called is False
    assert dispatcher_called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_product_card_followup_from_selected_product() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="покажи карточку")],
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "product_card",
                "products": [
                    {
                        "code": "2867",
                        "name": "Bundle Fort Knox 3+36 месяцев",
                    }
                ],
                "selected_product": {
                    "code": "2867",
                    "name": "Bundle Fort Knox 3+36 месяцев",
                },
            }
        },
    )
    product_called = False
    dispatcher_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            dispatcher_called = True
            if False:
                yield None

    async def fake_handle_product_info(ctx, user_message, search_query, intent):
        nonlocal product_called
        product_called = True
        assert user_message == "покажи карточку"
        assert "2867" in search_query
        assert intent == "product_card"
        ctx.session.state["_root_final_text"] = "product card answer"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_product_info = fake_handle_product_info

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "product card answer"
    assert product_called is True
    assert dispatcher_called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_explicit_product_kit_without_dispatcher() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="пакет")],
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "product_card",
                "products": [
                    {
                        "code": "2867",
                        "name": "Bundle Fort Knox 3+36 месяцев",
                    }
                ],
                "selected_product": {
                    "code": "2867",
                    "name": "Bundle Fort Knox 3+36 месяцев",
                },
            }
        },
    )
    product_called = False
    dispatcher_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            dispatcher_called = True
            if False:
                yield None

    async def fake_handle_product_info(ctx, user_message, search_query, intent):
        nonlocal product_called
        product_called = True
        assert user_message == "пакет"
        assert search_query == "пакет"
        assert intent == "product_kit"
        ctx.session.state["_root_final_text"] = "product kit answer"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_product_info = fake_handle_product_info

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == rootagent_module.VALIDATION_ERROR_USER_MESSAGE
    assert product_called is False
    assert dispatcher_called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_product_kit_followup_from_selected_product() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="дай документы")],
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "product_card",
                "products": [
                    {
                        "code": "2867",
                        "name": "Bundle Fort Knox 3+36 месяцев",
                    }
                ],
                "selected_product": {
                    "code": "2867",
                    "name": "Bundle Fort Knox 3+36 месяцев",
                },
            }
        },
    )
    product_called = False
    dispatcher_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            dispatcher_called = True
            if False:
                yield None

    async def fake_handle_product_info(ctx, user_message, search_query, intent):
        nonlocal product_called
        product_called = True
        assert user_message == "дай документы"
        assert search_query == "скачать комплект документов по продукту 2867"
        assert intent == "product_kit"
        ctx.session.state["_root_final_text"] = "product kit answer"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_product_info = fake_handle_product_info

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "product kit answer"
    assert product_called is True
    assert dispatcher_called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_sets_from_glossary_before_dispatcher() -> None:
    class FakeGlossaryLookup:
        async def find(self, text):
            assert text == "Что такое НСЖ?"
            return [["НСЖ", "накопительное страхование жизни", "сокращение"]]

    agent = _make_agent(glossary_lookup=FakeGlossaryLookup())
    ctx = _make_ctx(parts=[types.SimpleNamespace(text="Что такое НСЖ?")], session_state={})
    kb_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            assert ctx.session.state["from_glossary"] == [
                ["НСЖ", "накопительное страхование жизни", "сокращение"]
            ]
            ctx.session.state["_dispatcher_result_parsed"] = {
                "status": "ok",
                "route": "kb_answer",
                "intent": "kb_answer",
                "reason": "glossary_context",
                "search_query": "Что такое НСЖ?",
            }
            if False:
                yield None
            return

        if False:
            yield None

    async def fake_handle_kb_answer(ctx, user_message, search_query, intent):
        nonlocal kb_called
        kb_called = True
        assert ctx.session.state["from_glossary"] == [
            ["НСЖ", "накопительное страхование жизни", "сокращение"]
        ]
        ctx.session.state["_root_final_text"] = "answer"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_kb_answer = fake_handle_kb_answer

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "answer"
    assert kb_called is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_kb_answer_clears_product_dialog_context() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY: {
                "last_mode": "product_card",
                "products": [{"code": "7725", "name": "Альфа Kids+ 5 лет"}],
                "selected_product": {"code": "7725", "name": "Альфа Kids+ 5 лет"},
            }
        }
    )

    async def fake_run_json_leaf_agent(**kwargs):
        ctx.session.state["_kb_answer_result_parsed"] = {"message": " ok "}
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_kb_answer(
            ctx,
            "Что такое НСЖ?",
            "Что такое НСЖ?",
            "kb_answer",
        )
    ]

    assert events == []
    assert ctx.session.state["_root_final_text"] == "ok"
    assert rootagent_module.PRODUCT_DIALOG_CONTEXT_STATE_KEY not in ctx.session.state


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_kb_answer_expands_search_query_with_glossary() -> None:
    class FakeGlossaryLookup:
        async def find(self, text):
            return []

        async def expand_search_query(self, text):
            if "НСЖ" in text:
                return "НСЖ накопительное страхование жизни"
            return text

        async def build_doc_search_query(self, text):
            return await self.expand_search_query(text)

    agent = _make_agent(glossary_lookup=FakeGlossaryLookup())
    ctx = _make_ctx(parts=[], session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        assert ctx.session.state["search_query"] == "НСЖ накопительное страхование жизни"
        ctx.session.state["_kb_answer_result_parsed"] = {"message": "ok"}
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_kb_answer(
            ctx,
            "Что такое НСЖ?",
            "Что такое НСЖ?",
            "kb_answer",
        )
    ]

    assert events == []
    assert ctx.session.state["_root_final_text"] == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_info_sets_single_product_resolution_state() -> None:
    class FakeProductResolver:
        async def resolve_product(self, query):
            assert query == "Fort Knox"
            return types.SimpleNamespace(
                to_dict=lambda: {
                    "status": "resolved",
                    "mention": query,
                    "product_code": "2832",
                    "product_name": "Fort Knox",
                    "options": [],
                    "error": None,
                }
            )

        async def resolve_products(self, query, expected_count=None):
            raise AssertionError("resolve_products must not be called")

        async def resolve_product_filter(self, query):
            raise AssertionError("resolve_product_filter must not be called")

        async def fetch_product_full_details(self, product_code):
            return {}

    agent = _make_agent(product_resolver=FakeProductResolver())
    ctx = _make_ctx(parts=[], session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        assert ctx.session.state["product_resolution"]["product_code"] == "2832"
        assert ctx.session.state["product_resolutions"] == {}
        ctx.session.state["_product_info_result_parsed"] = {
            "message": "ok",
            "mode": "no_data",
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_info(
            ctx,
            "show Fort Knox",
            "Fort Knox",
            "product_card",
        )
    ]

    assert events == []
    assert ctx.session.state["product_resolution"]["status"] == "resolved"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_filter_sets_compare_product_resolutions_state() -> None:
    class FakeProductResolver:
        async def resolve_product(self, query):
            raise AssertionError("resolve_product must not be called")

        async def resolve_products(self, query, expected_count=None):
            assert query == "Fort Knox and Unit Linked"
            return types.SimpleNamespace(
                to_dict=lambda: {
                    "status": "resolved",
                    "items": [
                        {"status": "resolved", "product_code": "2832"},
                        {"status": "resolved", "product_code": "7698"},
                    ],
                }
            )

        async def resolve_product_filter(self, query):
            raise AssertionError("resolve_product_filter must not be called")

        async def fetch_product_full_details(self, product_code):
            return {}

    agent = _make_agent(product_resolver=FakeProductResolver())
    ctx = _make_ctx(parts=[], session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        assert ctx.session.state["product_resolutions"]["status"] == "resolved"
        assert ctx.session.state["product_resolution"] == {}
        ctx.session.state["_product_filter_result_parsed"] = {
            "message": "ok",
            "mode": "no_data",
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_filter(
            ctx,
            "compare products",
            "Fort Knox and Unit Linked",
            "product_compare",
        )
    ]

    assert events == []
    assert len(ctx.session.state["product_resolutions"]["items"]) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_filter_deduplicates_compare_product_resolutions_state() -> None:
    class FakeProductResolver:
        async def resolve_product(self, query):
            raise AssertionError("resolve_product must not be called")

        async def resolve_products(self, query, expected_count=None):
            return types.SimpleNamespace(
                to_dict=lambda: {
                    "status": "resolved",
                    "items": [
                        {
                            "status": "resolved",
                            "mention": "Product A 111",
                            "product_code": "111",
                            "product_name": "Product A",
                        },
                        {
                            "status": "resolved",
                            "mention": "111",
                            "product_code": "111",
                            "product_name": "Product A",
                        },
                        {
                            "status": "resolved",
                            "mention": "Product B 222",
                            "product_code": "222",
                            "product_name": "Product B",
                        },
                        {
                            "status": "resolved",
                            "mention": "222",
                            "product_code": "222",
                            "product_name": "Product B",
                        },
                    ],
                }
            )

        async def resolve_product_filter(self, query):
            raise AssertionError("resolve_product_filter must not be called")

        async def fetch_product_full_details(self, product_code):
            return {}

    agent = _make_agent(product_resolver=FakeProductResolver())
    ctx = _make_ctx(parts=[], session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        items = ctx.session.state["product_resolutions"]["items"]
        assert [item["product_code"] for item in items] == ["111", "222"]
        ctx.session.state["_product_filter_result_parsed"] = {
            "message": "ok",
            "mode": "no_data",
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_filter(
            ctx,
            "compare products",
            "Product A 111 and Product B 222",
            "product_compare",
        )
    ]

    assert events == []


@pytest.mark.unit
def test_product_resolutions_dedup_key_keeps_same_code_with_different_names() -> None:
    state = RootAgent._product_resolutions_to_state(
        {
            "status": "resolved",
            "items": [
                {
                    "status": "resolved",
                    "product_code": "7698",
                    "product_name": "Unit Linked Активные облигации",
                },
                {
                    "status": "resolved",
                    "product_code": "7698",
                    "product_name": "Unit Linked Стратегия роста",
                },
            ],
        }
    )

    assert [
        item["product_name"]
        for item in state["items"]
    ] == [
        "Unit Linked Активные облигации",
        "Unit Linked Стратегия роста",
    ]


@pytest.mark.unit
def test_product_resolutions_dedup_key_uses_option_name_fallback() -> None:
    state = RootAgent._product_resolutions_to_state(
        {
            "status": "resolved",
            "items": [
                {
                    "status": "resolved",
                    "product_code": "7698",
                    "options": [
                        {
                            "product_code": "7698",
                            "canonical_name": "Unit Linked Активные облигации",
                        },
                    ],
                },
                {
                    "status": "resolved",
                    "product_code": "7698",
                    "options": [
                        {
                            "product_code": "7698",
                            "canonical_name": "Unit Linked Активные облигации",
                        },
                    ],
                },
                {
                    "status": "resolved",
                    "product_code": "7698",
                    "options": [
                        {
                            "product_code": "7698",
                            "canonical_name": "Unit Linked Стратегия роста",
                        },
                    ],
                },
            ],
        }
    )

    assert len(state["items"]) == 2
    assert state["items"][0]["options"] == [
        {"code": "7698", "name": "Unit Linked Активные облигации"}
    ]
    assert state["items"][1]["options"] == [
        {"code": "7698", "name": "Unit Linked Стратегия роста"}
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_filter_resolves_product_filter() -> None:
    class FakeProductResolver:
        async def resolve_product(self, query):
            raise AssertionError("resolve_product must not be called")

        async def resolve_products(self, query, expected_count=None):
            raise AssertionError("resolve_products must not be called")

        async def resolve_product_filter(self, query):
            assert query == "products in USD"
            return types.SimpleNamespace(
                to_dict=lambda: {
                    "status": "resolved",
                    "query": query,
                    "product_codes": ["2832", "2867"],
                    "products": [
                        {"product_code": "2832", "canonical_name": "Fort Knox"},
                    ],
                    "matched_terms": ["products in USD"],
                    "error": None,
                }
            )

        async def fetch_product_full_details(self, product_code):
            return {}

    agent = _make_agent(product_resolver=FakeProductResolver())
    ctx = _make_ctx(parts=[], session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        assert ctx.session.state["product_resolution"] == {}
        assert ctx.session.state["product_resolutions"] == {}
        assert ctx.session.state["product_filter_resolution"]["product_codes"] == ["2832", "2867"]
        assert "products" not in ctx.session.state["product_filter_resolution"]
        ctx.session.state["_product_filter_result_parsed"] = {
            "message": "ok",
            "mode": "no_data",
        }
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_filter(
            ctx,
            "show products",
            "products in USD",
            "product_filter",
        )
    ]

    assert events == []
    assert ctx.session.state["product_filter_resolution"]["status"] == "resolved"

@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_product_info_expands_search_query_with_glossary() -> None:
    class FakeGlossaryLookup:
        async def find(self, text):
            return []

        async def expand_search_query(self, text):
            if "ФК" in text:
                return text.replace("ФК", "Fort Knox")
            return text

        async def build_doc_search_query(self, text):
            return await self.expand_search_query(text)

    agent = _make_agent(glossary_lookup=FakeGlossaryLookup())
    ctx = _make_ctx(parts=[], session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        assert (
            ctx.session.state["product_info_search_query"]
            == "карточка продукта Fort Knox"
        )
        ctx.session.state["_product_info_result_parsed"] = {"message": "ok", "mode": "no_data"}
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [
        event
        async for event in agent._handle_product_info(
            ctx,
            "карточка продукта ФК",
            "карточка продукта ФК",
            "product_card",
        )
    ]

    assert events == []
    assert ctx.session.state["_root_final_text"] == "ok"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_returns_owasp_specific_stub_on_validation_failure() -> None:
    agent = _make_agent()
    ctx = _make_ctx(parts=[types.SimpleNamespace(text="привет")], session_state={})

    async def fake_run_json_leaf_agent(**kwargs):
        raise rootagent_module.AgentValidationFailure(
            log_label=kwargs["log_label"],
            validation_error="bad contract",
            raw="not-json",
            user_message=rootagent_module.OWASP_INVALID_CONTRACT_USER_MESSAGE,
        )
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    expected_fallback = rootagent_module.generate_agent_fallback(
        "привет",
        error_type="validation_failure",
        agent_name=None,
        context={"validation_error": "bad contract"},
    )
    assert events[0].content.parts[0].text == expected_fallback


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_smalltalk_capabilities_request_to_smalltalk() -> None:
    agent = _make_agent()
    ctx = _make_ctx(parts=[types.SimpleNamespace(text="Что ты умеешь?")], session_state={})
    smalltalk_called = False
    doc_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            ctx.session.state["_dispatcher_result_parsed"] = {
                "status": "ok",
                "route": "smalltalk",
                "intent": "smalltalk",
                "reason": "assistant_capabilities_smalltalk",
                "search_query": "",
            }
            if False:
                yield None
            return

        if False:
            yield None

    async def fake_handle_smalltalk(ctx, user_message, intent):
        nonlocal smalltalk_called
        smalltalk_called = True
        assert user_message == "Что ты умеешь?"
        assert intent == "smalltalk"
        ctx.session.state["_root_final_text"] = "Я умею искать документы и помогать продавать продукты АСЖ."
        if False:
            yield None

    async def fake_doc_run_async(ctx):
        nonlocal doc_called
        doc_called = True
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent._handle_smalltalk = fake_handle_smalltalk
    agent.doc_search_orchestrator.run_async = fake_doc_run_async

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "Я умею искать документы и помогать продавать продукты АСЖ."
    assert smalltalk_called is True
    assert doc_called is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_doc_search_sets_doc_search_query_from_glossary() -> None:
    class FakeGlossaryLookup:
        async def find(self, text):
            return []

        async def build_doc_search_query(self, text):
            assert text == "дай презентеры по ФК"
            return "дай презентеры по Fort Knox"

        async def expand_search_query(self, text):
            return await self.build_doc_search_query(text)

    agent = _make_agent(glossary_lookup=FakeGlossaryLookup())
    ctx = _make_ctx(parts=[], session_state={})
    orchestrator_called = False

    async def fake_doc_run_async(ctx):
        nonlocal orchestrator_called
        orchestrator_called = True
        if False:
            yield None

    agent.doc_search_orchestrator.run_async = fake_doc_run_async

    events = [
        event
        async for event in agent._handle_doc_search(
            ctx,
            "дай презентеры по ФК",
            "doc_search",
        )
    ]

    assert events == []
    assert orchestrator_called is True
    assert ctx.session.state["doc_search_intent"] == "doc_search"
    assert ctx.session.state["doc_search_query"] == "дай презентеры по Fort Knox"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_sets_doc_search_query_before_orchestrator() -> None:
    class FakeGlossaryLookup:
        async def find(self, text):
            assert text == "дай презентеры по ФК"
            return [["ФК", "Fort Knox", "продукт"]]

        async def build_doc_search_query(self, text):
            assert text == "дай презентеры по ФК"
            return "дай презентеры по Fort Knox"

        async def expand_search_query(self, text):
            return await self.build_doc_search_query(text)

    agent = _make_agent(glossary_lookup=FakeGlossaryLookup())
    ctx = _make_ctx(parts=[types.SimpleNamespace(text="дай презентеры по ФК")], session_state={})
    doc_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return

        if kwargs["log_label"] == "dispatcher_result_json":
            ctx.session.state["_dispatcher_result_parsed"] = {
                "status": "ok",
                "route": "doc_search",
                "intent": "doc_search",
                "reason": "documents",
                "search_query": "дай презентеры по ФК",
            }
            if False:
                yield None
            return

        if False:
            yield None

    async def fake_doc_run_async(ctx):
        nonlocal doc_called
        doc_called = True
        assert ctx.session.state["doc_search_query"] == "дай презентеры по Fort Knox"
        assert ctx.session.state["doc_search_intent"] == "doc_search"
        ctx.session.state["_root_final_text"] = "ok"
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent.doc_search_orchestrator.run_async = fake_doc_run_async

    events = [event async for event in agent._run_async_impl(ctx)]

    assert len(events) == 1
    assert events[0].content.parts[0].text == "ok"
    assert doc_called is True


@pytest.mark.unit
def test_has_doc_list_followup_context_requires_doc_search_route_and_list() -> None:
    agent = _make_agent()
    assert agent._has_doc_list_followup_context(
        _make_ctx(session_state={"last_route": "doc_search", "last_document_list": "1. Doc"})
    )
    assert not agent._has_doc_list_followup_context(
        _make_ctx(session_state={"last_route": "kb_answer", "last_document_list": "1. Doc"})
    )
    assert not agent._has_doc_list_followup_context(
        _make_ctx(session_state={"last_route": "doc_search", "last_document_list": ""})
    )


@pytest.mark.unit
def test_doc_list_followup_dispatch_returns_none_without_context() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={"last_route": "", "last_document_list": ""})
    assert agent._doc_list_followup_dispatch(ctx, "3") is None


@pytest.mark.unit
def test_doc_list_followup_dispatch_returns_file_download_by_rank_with_context() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            "last_route": "doc_search",
            "last_document_list": "1. Document title",
        }
    )
    dispatch = agent._doc_list_followup_dispatch(ctx, "1") 
    assert dispatch is not None
    assert dispatch["route"] == "doc_search"
    assert dispatch["intent"] == "file_download"
    assert dispatch["reason"] == "doc_list_followup_download_by_rank"


@pytest.mark.unit
def test_doc_list_followup_dispatch_returns_show_more_with_context() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            "last_route": "doc_search",
            "last_document_list": "1. Document title",
        }
    )
    dispatch = agent._doc_list_followup_dispatch(ctx, "еще")
    assert dispatch is not None
    assert dispatch["intent"] == "show_more"
    assert dispatch["reason"] == "doc_list_followup_show_more"


@pytest.mark.unit
def test_doc_list_followup_dispatch_returns_show_all_with_context() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            "last_route": "doc_search",
            "last_document_list": "1. Document title",
        }
    )
    dispatch = agent._doc_list_followup_dispatch(ctx, "покажи все")
    assert dispatch is not None
    assert dispatch["intent"] == "show_all"
    assert dispatch["reason"] == "doc_list_followup_show_all"


@pytest.mark.unit
def test_doc_list_followup_dispatch_ignores_pagination_without_context() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={"last_route": "", "last_document_list": ""})
    assert agent._doc_list_followup_dispatch(ctx, "еще") is None


@pytest.mark.unit
def test_doc_list_followup_dispatch_ignores_generic_phrase_without_rank() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        session_state={
            "last_route": "doc_search",
            "last_document_list": "1. Document title",
        }
    )
    assert agent._doc_list_followup_dispatch(ctx, "скачай его") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_doc_search_file_download_sets_download_bot_action() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={})

    events = [event async for event in agent._handle_doc_search(ctx, "3", "file_download")]

    assert events == []
    assert ctx.session.state["_bot_action"] == {
        "type": "download_by_ranks",
        "ranks": [3],
    }
    assert ctx.session.state["_root_final_text"] == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_handle_doc_search_show_more_sets_pagination_bot_action() -> None:
    agent = _make_agent()
    ctx = _make_ctx(session_state={})

    events = [
        event
        async for event in agent._handle_doc_search(ctx, "еще", "show_more")
    ]

    assert events == []
    assert ctx.session.state["_bot_action"] == {"type": "show_doc_list_more"}
    assert ctx.session.state["_root_final_text"] == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_doc_list_download_followup_skips_dispatcher() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="3")],
        session_state={
            "last_route": "doc_search",
            "last_document_list": "1. Some document",
            "last_user_query": "",
            "last_intent": "",
            "last_search_query": "",
            "last_product": "",
        },
    )
    dispatcher_called = False
    orchestrator_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return
        dispatcher_called = True
        if False:
            yield None

    async def fake_doc_run_async(ctx):
        nonlocal orchestrator_called
        orchestrator_called = True
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent.doc_search_orchestrator.run_async = fake_doc_run_async

    events = [event async for event in agent._run_async_impl(ctx)]

    assert dispatcher_called is False
    assert orchestrator_called is False
    assert ctx.session.state["_bot_action"] == {
        "type": "download_by_ranks",
        "ranks": [3],
    }
    assert len(events) == 1
    assert events[0].actions.state_delta["_bot_action"]["type"] == "download_by_ranks"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_async_impl_routes_doc_list_show_more_followup_skips_dispatcher() -> None:
    agent = _make_agent()
    ctx = _make_ctx(
        parts=[types.SimpleNamespace(text="еще")],
        session_state={
            "last_route": "doc_search",
            "last_document_list": "1. Some document",
            "last_user_query": "",
            "last_intent": "",
            "last_search_query": "",
            "last_product": "",
        },
    )
    dispatcher_called = False
    orchestrator_called = False

    async def fake_run_json_leaf_agent(**kwargs):
        nonlocal dispatcher_called
        if kwargs["log_label"] == "owasp_result_json":
            ctx.session.state["_owasp_result_parsed"] = {
                "status": "ok",
                "route": "continue",
                "reason": "ok",
            }
            if False:
                yield None
            return
        dispatcher_called = True
        if False:
            yield None

    async def fake_doc_run_async(ctx):
        nonlocal orchestrator_called
        orchestrator_called = True
        if False:
            yield None

    agent._run_json_leaf_agent = fake_run_json_leaf_agent
    agent.doc_search_orchestrator.run_async = fake_doc_run_async

    events = [event async for event in agent._run_async_impl(ctx)]

    assert dispatcher_called is True
    assert orchestrator_called is False
    assert ctx.session.state["_bot_action"] == {"type": "show_doc_list_more"}
    assert len(events) == 1
    assert events[0].actions.state_delta["_bot_action"]["type"] == "show_doc_list_more"
