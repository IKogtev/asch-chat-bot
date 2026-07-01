import asyncio
import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_json_leaf_runner_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "json_leaf_runner.py"

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = [str(repo_root / "agent")]

    logger_stub = types.ModuleType("utils.logger")
    logger_stub.setup_logger = lambda *args, **kwargs: types.SimpleNamespace(
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
        info=lambda *a, **k: None,
    )

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.truncate_for_log = lambda text, max_length=200: (text or "")[:max_length]

    def extract_json(text: str):
        import json
        import re

        text = (text or "").strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("JSON object not found")
        return json.loads(match.group(0))

    helpers_stub.extract_json = extract_json

    agents_pkg = types.ModuleType("agent.agents")
    agents_pkg.__path__ = [str(repo_root / "agent" / "agents")]
    sys.modules["agent.agents"] = agents_pkg

    kb_context_spec = importlib.util.spec_from_file_location(
        "agent.doc_search_kb_context",
        repo_root / "agent" / "doc_search_kb_context.py",
    )
    validation_spec = importlib.util.spec_from_file_location(
        "agent.doc_search_validation",
        repo_root / "agent" / "doc_search_validation.py",
    )
    assert kb_context_spec is not None and kb_context_spec.loader is not None
    assert validation_spec is not None and validation_spec.loader is not None
    kb_context_module = importlib.util.module_from_spec(kb_context_spec)
    validation_module = importlib.util.module_from_spec(validation_spec)
    sys.modules["agent.doc_search_kb_context"] = kb_context_module
    sys.modules["agent.doc_search_validation"] = validation_module
    kb_context_spec.loader.exec_module(kb_context_module)
    validation_spec.loader.exec_module(validation_module)

    adk_agents_stub = types.ModuleType("google.adk.agents")
    adk_agents_stub.LlmAgent = type("LlmAgent", (), {})
    adk_agents_stub.InvocationContext = type("InvocationContext", (), {})

    adk_events_stub = types.ModuleType("google.adk.events")
    adk_events_stub.Event = type("Event", (), {})

    sys.modules["agent"] = agent_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.events"] = adk_events_stub

    spec = importlib.util.spec_from_file_location("agent.json_leaf_runner", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.json_leaf_runner"] = module
    spec.loader.exec_module(module)
    return module


json_leaf_runner_module = _load_json_leaf_runner_module()
AgentValidationFailure = json_leaf_runner_module.AgentValidationFailure
run_json_leaf_agent = json_leaf_runner_module.run_json_leaf_agent
strip_thought_parts = json_leaf_runner_module.strip_thought_parts
_extract_tool_event_summaries = json_leaf_runner_module._extract_tool_event_summaries


class _FakeAgent:
    def __init__(self, events=None):
        self.events = events or []

    async def run_async(self, ctx):
        for event in self.events:
            yield event


def _make_ctx(raw: str):
    return types.SimpleNamespace(session=types.SimpleNamespace(state={"owasp_result_json": raw}))


def _make_event(parts, actions=None):
    return types.SimpleNamespace(
        content=types.SimpleNamespace(parts=parts),
        actions=actions,
    )


async def _drain(gen):
    items = []
    async for item in gen:
        items.append(item)
    return items


@pytest.mark.unit
def test_strip_thought_parts_removes_only_thought_parts_without_mutating_source() -> None:
    visible = types.SimpleNamespace(text="visible")
    thought = types.SimpleNamespace(text="hidden", thought=True)
    event = _make_event([visible, thought])

    cleaned = strip_thought_parts(event)

    assert cleaned is not None
    assert cleaned is not event
    assert cleaned.content.parts == [visible]
    assert event.content.parts == [visible, thought]


@pytest.mark.unit
def test_strip_thought_parts_drops_empty_event_without_actions() -> None:
    event = _make_event([types.SimpleNamespace(text="hidden", thought=True)])

    assert strip_thought_parts(event) is None


@pytest.mark.unit
def test_strip_thought_parts_preserves_function_parts_and_actions() -> None:
    function_call = types.SimpleNamespace(function_call={"name": "kb_search"})
    function_response = types.SimpleNamespace(function_response={"name": "kb_search"})
    thought = types.SimpleNamespace(text="hidden", thought=True)
    actions = types.SimpleNamespace(state_delta={"x": 1})
    event = _make_event([thought, function_call, function_response], actions=actions)

    cleaned = strip_thought_parts(event)

    assert cleaned is not None
    assert cleaned.content.parts == [function_call, function_response]
    assert cleaned.actions is actions


@pytest.mark.unit
def test_strip_thought_parts_keeps_thought_only_event_with_meaningful_actions() -> None:
    actions = types.SimpleNamespace(state_delta={"x": 1})
    event = _make_event(
        [types.SimpleNamespace(text="hidden", thought=True)],
        actions=actions,
    )

    cleaned = strip_thought_parts(event)

    assert cleaned is not None
    assert cleaned.content.parts == []
    assert cleaned.actions is actions


@pytest.mark.unit
def test_run_json_leaf_agent_yields_sanitized_events() -> None:
    visible = types.SimpleNamespace(text="visible")
    thought = types.SimpleNamespace(text="hidden", thought=True)
    event = _make_event([thought, visible])
    ctx = _make_ctx('{"status":"ok"}')

    def validator(data, context):
        assert isinstance(context, dict)
        return data

    events = asyncio.run(
        _drain(
            run_json_leaf_agent(
                ctx=ctx,
                agent=_FakeAgent([event]),
                output_key="owasp_result_json",
                parsed_state_key="_owasp_result_parsed",
                validator=validator,
                log_label="owasp_result_json",
                validation_error_user_message="stub",
            )
        )
    )

    assert len(events) == 1
    assert events[0].content.parts == [visible]
    assert event.content.parts == [thought, visible]
    assert ctx.session.state["_owasp_result_parsed"] == {"status": "ok"}


@pytest.mark.unit
def test_run_json_leaf_agent_passes_tool_call_names_to_validator_context() -> None:
    function_call = types.SimpleNamespace(
        function_call=types.SimpleNamespace(name="execute_sql", args={"sql": "select 1"})
    )
    event = _make_event([function_call])
    ctx = _make_ctx('{"status":"ok"}')

    def validator(data, context):
        assert context["_adk_tool_calls"] == ["execute_sql"]
        assert context["_adk_tool_event_summaries"] == [
            {
                "type": "call",
                "name": "execute_sql",
                "args_preview": '{"sql": "select 1"}',
            }
        ]
        return data

    asyncio.run(
        _drain(
            run_json_leaf_agent(
                ctx=ctx,
                agent=_FakeAgent([event]),
                output_key="owasp_result_json",
                parsed_state_key="_owasp_result_parsed",
                validator=validator,
                log_label="owasp_result_json",
                validation_error_user_message="stub",
            )
        )
    )

    assert ctx.session.state["_owasp_result_parsed"] == {"status": "ok"}


@pytest.mark.unit
def test_extract_tool_event_summaries_supports_dict_function_response() -> None:
    event = {
        "content": {
            "parts": [
                {
                    "functionResponse": {
                        "name": "execute_sql",
                        "response": {"rows": [{"id": 2832}], "success": True},
                    }
                }
            ]
        }
    }

    assert _extract_tool_event_summaries(event) == [
        {
            "type": "response",
            "name": "execute_sql",
            "response_preview": '{"rows": [{"id": 2832}], "success": true}',
        }
    ]


@pytest.mark.unit
def test_run_json_leaf_agent_raises_non_fatal_validation_failure_for_invalid_json() -> None:
    ctx = _make_ctx("not-json")

    def validator(data, context):
        assert isinstance(context, dict)
        return data

    with pytest.raises(AgentValidationFailure) as exc:
        asyncio.run(
            _drain(
                run_json_leaf_agent(
                    ctx=ctx,
                    agent=_FakeAgent(),
                    output_key="owasp_result_json",
                    parsed_state_key="_owasp_result_parsed",
                    validator=validator,
                    log_label="owasp_result_json",
                    validation_error_user_message="stub",
                )
            )
        )

    assert exc.value.log_label == "owasp_result_json"
    assert exc.value.user_message == "stub"
    assert ctx.session.state.get("_owasp_result_parsed") is None


@pytest.mark.unit
def test_run_json_leaf_agent_raises_non_fatal_validation_failure_for_invalid_schema() -> None:
    ctx = _make_ctx('{"status":"bad","route":"continue"}')

    def validator(data, context):
        assert isinstance(context, dict)
        raise ValueError(f"Invalid status: {data['status']}")

    with pytest.raises(AgentValidationFailure) as exc:
        asyncio.run(
            _drain(
                run_json_leaf_agent(
                    ctx=ctx,
                    agent=_FakeAgent(),
                    output_key="owasp_result_json",
                    parsed_state_key="_owasp_result_parsed",
                    validator=validator,
                    log_label="owasp_result_json",
                    validation_error_user_message="blocked",
                )
            )
        )

    assert exc.value.validation_error == "Invalid status: bad"
    assert exc.value.user_message == "blocked"


@pytest.mark.unit
def test_run_json_leaf_agent_stores_doc_search_kb_hits() -> None:
    kb_response = """CONTEXT
rank [1] FILE_NAME: a.pdf
RELATIVE_PATH: path/a.pdf

DOCUMENT_ID: doc-a

TEXT:
hello
"""
    event = _make_event(
        [
            types.SimpleNamespace(
                function_response={
                    "name": "kb_search",
                    "response": {"content": kb_response},
                }
            )
        ]
    )
    ctx = types.SimpleNamespace(
        session=types.SimpleNamespace(
            state={"doc_search_result_json": '{"status":"ok","mode":"no_data","message":"x"}'}
        )
    )

    def validator(data, context):
        return data

    asyncio.run(
        _drain(
            run_json_leaf_agent(
                ctx=ctx,
                agent=_FakeAgent([event]),
                output_key="doc_search_result_json",
                parsed_state_key="_doc_search_result_parsed",
                validator=validator,
                log_label="doc_search_result_json",
                validation_error_user_message="stub",
            )
        )
    )

    hits = ctx.session.state.get("_doc_search_kb_hits")
    assert hits == [
        {
            "rank": 1,
            "source_name": "a.pdf",
            "source_path": "path/a.pdf",
            "document_id": "doc-a",
        }
    ]


@pytest.mark.unit
def test_run_json_leaf_agent_uses_last_kb_search_response_for_hits() -> None:
    first_response = """CONTEXT
rank [1] FILE_NAME: old.pdf
RELATIVE_PATH: path/old.pdf

DOCUMENT_ID: doc-old

TEXT:
old
"""
    last_response = """CONTEXT
rank [1] FILE_NAME: new.pdf
RELATIVE_PATH: path/new.pdf

DOCUMENT_ID: doc-new

TEXT:
new
"""
    events = [
        _make_event(
            [
                types.SimpleNamespace(
                    function_response={
                        "name": "kb_search",
                        "response": {"content": first_response},
                    }
                )
            ]
        ),
        _make_event(
            [
                types.SimpleNamespace(
                    function_response={
                        "name": "kb_search",
                        "response": {"content": last_response},
                    }
                )
            ]
        ),
    ]
    ctx = types.SimpleNamespace(
        session=types.SimpleNamespace(
            state={"doc_search_result_json": '{"status":"ok","mode":"no_data","message":"x"}'}
        )
    )

    def validator(data, context):
        return data

    asyncio.run(
        _drain(
            run_json_leaf_agent(
                ctx=ctx,
                agent=_FakeAgent(events),
                output_key="doc_search_result_json",
                parsed_state_key="_doc_search_result_parsed",
                validator=validator,
                log_label="doc_search_result_json",
                validation_error_user_message="stub",
            )
        )
    )

    hits = ctx.session.state.get("_doc_search_kb_hits")
    assert hits == [
        {
            "rank": 1,
            "source_name": "new.pdf",
            "source_path": "path/new.pdf",
            "document_id": "doc-new",
        }
    ]
