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
run_json_leaf_agent = json_leaf_runner_module.run_json_leaf_agent


class _FakeAgent:
    async def run_async(self, ctx):
        if False:
            yield None


def _make_ctx(raw: str):
    return types.SimpleNamespace(session=types.SimpleNamespace(state={"owasp_result_json": raw}))


async def _drain(gen):
    items = []
    async for item in gen:
        items.append(item)
    return items


@pytest.mark.unit
def test_run_json_leaf_agent_uses_fallback_for_invalid_json() -> None:
    ctx = _make_ctx("not-json")

    fallback_calls = []

    def validator(data):
        return data

    def fallback(raw, exc):
        fallback_calls.append((raw, str(exc)))
        return {"status": "blocked", "route": "reject", "reason": "invalid_contract"}

    asyncio.run(
        _drain(
            run_json_leaf_agent(
                ctx=ctx,
                agent=_FakeAgent(),
                output_key="owasp_result_json",
                parsed_state_key="_owasp_result_parsed",
                validator=validator,
                log_label="owasp_result_json",
                on_parse_error=fallback,
            )
        )
    )

    assert fallback_calls
    assert ctx.session.state["_owasp_result_parsed"] == {
        "status": "blocked",
        "route": "reject",
        "reason": "invalid_contract",
    }


@pytest.mark.unit
def test_run_json_leaf_agent_uses_fallback_for_invalid_schema() -> None:
    ctx = _make_ctx('{"status":"bad","route":"continue"}')

    def validator(data):
        raise ValueError(f"Invalid status: {data['status']}")

    def fallback(raw, exc):
        return {
            "status": "blocked",
            "route": "reject",
            "reason": "invalid_contract",
            "user_message": "blocked",
        }

    asyncio.run(
        _drain(
            run_json_leaf_agent(
                ctx=ctx,
                agent=_FakeAgent(),
                output_key="owasp_result_json",
                parsed_state_key="_owasp_result_parsed",
                validator=validator,
                log_label="owasp_result_json",
                on_parse_error=fallback,
            )
        )
    )

    assert ctx.session.state["_owasp_result_parsed"]["status"] == "blocked"
    assert ctx.session.state["_owasp_result_parsed"]["reason"] == "invalid_contract"
