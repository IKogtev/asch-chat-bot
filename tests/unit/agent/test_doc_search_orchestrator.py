import asyncio
import importlib.util
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest


def _load_doc_search_orchestrator_module():
    repo_root = Path(__file__).resolve().parents[3]

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
    config_stub.ACTIVE_DOCUMENTS_COLLECTION = "test_collection"
    config_stub.DOC_SEARCH_PAGE_SIZE = 5

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.DOC_SEARCH_SUCCESS_HINT = "Найдены документы по запросу."
    helpers_stub.truncate_for_log = lambda text, max_length=200: (text or "")[:max_length]
    helpers_stub.format_text_answer = lambda message: str(message or "").strip()

    def extract_json(text: str):
        import json
        import re

        text = (text or "").strip()
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise ValueError("JSON object not found")
        return json.loads(match.group(0))

    helpers_stub.extract_json = extract_json

    validation_utils_spec = importlib.util.spec_from_file_location(
        "agent.agents.validation_utils",
        repo_root / "agent" / "agents" / "validation_utils.py",
    )
    kb_context_spec = importlib.util.spec_from_file_location(
        "agent.doc_search_kb_context",
        repo_root / "agent" / "doc_search_kb_context.py",
    )
    validation_spec = importlib.util.spec_from_file_location(
        "agent.doc_search_validation",
        repo_root / "agent" / "doc_search_validation.py",
    )
    doc_search_agent_spec = importlib.util.spec_from_file_location(
        "agent.agents.doc_search_agent",
        repo_root / "agent" / "agents" / "doc_search_agent.py",
    )
    json_leaf_runner_spec = importlib.util.spec_from_file_location(
        "agent.json_leaf_runner",
        repo_root / "agent" / "json_leaf_runner.py",
    )
    orchestrator_spec = importlib.util.spec_from_file_location(
        "agent.agents.doc_search_orchestrator",
        repo_root / "agent" / "agents" / "doc_search_orchestrator.py",
    )

    for spec in (
        validation_utils_spec,
        kb_context_spec,
        validation_spec,
        doc_search_agent_spec,
        json_leaf_runner_spec,
        orchestrator_spec,
    ):
        assert spec is not None and spec.loader is not None

    genai_types_stub = types.ModuleType("google.genai.types")
    genai_types_stub.GenerateContentConfig = type("GenerateContentConfig", (), {})

    adk_agents_stub = types.ModuleType("google.adk.agents")
    adk_agents_stub.LlmAgent = type("LlmAgent", (), {})

    class BaseAgent:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    adk_agents_stub.BaseAgent = BaseAgent
    adk_agents_stub.InvocationContext = type("InvocationContext", (), {})

    adk_events_stub = types.ModuleType("google.adk.events")
    adk_events_stub.Event = type("Event", (), {})

    lite_llm_stub = types.ModuleType("google.adk.models.lite_llm")
    lite_llm_stub.LiteLlm = type("LiteLlm", (), {})

    mcp_session_manager_stub = types.ModuleType(
        "google.adk.tools.mcp_tool.mcp_session_manager"
    )
    mcp_toolset_stub = types.ModuleType("google.adk.tools.mcp_tool.mcp_toolset")
    mcp_toolset_stub.McpToolset = type("McpToolset", (), {})
    mcp_session_manager_stub.StreamableHTTPConnectionParams = type(
        "StreamableHTTPConnectionParams", (), {}
    )

    tools_pkg = types.ModuleType("agent.tools")
    tools_pkg.__path__ = [str(repo_root / "agent" / "tools")]
    refreshing_toolset_stub = types.ModuleType("agent.tools.refreshing_mcp_toolset")
    refreshing_toolset_stub.RefreshingMcpToolset = type("RefreshingMcpToolset", (), {})

    prompt_loader_stub = types.ModuleType("agent.prompt_loader")
    prompt_loader_stub.start_prompt_watcher = lambda *args, **kwargs: None

    config_doc_search_stub = types.ModuleType("agent.config")
    config_doc_search_stub.KBSEARCH_MCP_URL = ""
    config_doc_search_stub.MCP_TOKEN = ""
    config_doc_search_stub.MCP_TIMEOUT_SEC = 30.0
    config_doc_search_stub.ACTIVE_DOCUMENTS_COLLECTION = "test_collection"
    config_doc_search_stub.DOC_SEARCH_PAGE_SIZE = 5
    config_doc_search_stub.DOC_SEARCH_TEMPERATURE = -1

    helpers_doc_search_stub = types.ModuleType("agent.helpers")
    helpers_doc_search_stub.load_prompt = lambda *args, **kwargs: "prompt"

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.config"] = config_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["google.genai.types"] = genai_types_stub
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.events"] = adk_events_stub
    sys.modules["google.adk.models.lite_llm"] = lite_llm_stub
    sys.modules[
        "google.adk.tools.mcp_tool.mcp_session_manager"
    ] = mcp_session_manager_stub
    sys.modules["google.adk.tools.mcp_tool.mcp_toolset"] = mcp_toolset_stub
    sys.modules["agent.tools"] = tools_pkg
    sys.modules["agent.tools.refreshing_mcp_toolset"] = refreshing_toolset_stub
    sys.modules["agent.prompt_loader"] = prompt_loader_stub

    validation_utils_module = importlib.util.module_from_spec(validation_utils_spec)
    kb_context_module = importlib.util.module_from_spec(kb_context_spec)
    validation_module = importlib.util.module_from_spec(validation_spec)
    sys.modules["agent.agents.validation_utils"] = validation_utils_module
    sys.modules["agent.doc_search_kb_context"] = kb_context_module
    sys.modules["agent.doc_search_validation"] = validation_module
    validation_utils_spec.loader.exec_module(validation_utils_module)
    kb_context_spec.loader.exec_module(kb_context_module)
    validation_spec.loader.exec_module(validation_module)

    # doc_search_agent needs its own config/helpers stubs during import
    saved_config = sys.modules["agent.config"]
    saved_helpers = sys.modules["agent.helpers"]
    sys.modules["agent.config"] = config_doc_search_stub
    sys.modules["agent.helpers"] = helpers_doc_search_stub
    doc_search_agent_module = importlib.util.module_from_spec(doc_search_agent_spec)
    sys.modules["agent.agents.doc_search_agent"] = doc_search_agent_module
    doc_search_agent_spec.loader.exec_module(doc_search_agent_module)
    sys.modules["agent.config"] = saved_config
    sys.modules["agent.helpers"] = saved_helpers

    json_leaf_runner_module = importlib.util.module_from_spec(json_leaf_runner_spec)
    sys.modules["agent.json_leaf_runner"] = json_leaf_runner_module
    json_leaf_runner_spec.loader.exec_module(json_leaf_runner_module)

    orchestrator_module = importlib.util.module_from_spec(orchestrator_spec)
    sys.modules["agent.agents.doc_search_orchestrator"] = orchestrator_module
    orchestrator_spec.loader.exec_module(orchestrator_module)
    return orchestrator_module


orchestrator_module = _load_doc_search_orchestrator_module()
DocSearchOrchestrator = orchestrator_module.DocSearchOrchestrator
AgentValidationFailure = orchestrator_module.AgentValidationFailure
DocSearchRetryableValidationError = orchestrator_module.DocSearchRetryableValidationError
DOC_SEARCH_NO_DATA_MESSAGE = orchestrator_module.DOC_SEARCH_NO_DATA_MESSAGE
DOC_SEARCH_SUCCESS_HINT = orchestrator_module.DOC_SEARCH_SUCCESS_HINT
VALIDATION_ERROR_USER_MESSAGE = orchestrator_module.VALIDATION_ERROR_USER_MESSAGE


def _make_ctx() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        session=types.SimpleNamespace(
            state={
                "user_query": "Fort Knox презентеры",
                "doc_search_query": "Fort Knox презентеры",
                "doc_search_intent": "doc_search",
            },
            user_id="42",
            id="session-1",
        )
    )


def _make_orchestrator() -> DocSearchOrchestrator:
    return DocSearchOrchestrator(
        doc_search_agent=types.SimpleNamespace(name="doc_search_agent"),
        doc_collection="test_collection",
    )


def _document_list_result() -> dict:
    return {
        "status": "ok",
        "mode": "document_list",
        "message": "",
        "results": [
            {
                "document_id": "doc-1",
                "source_name": "a.pdf",
                "source_path": "/a.pdf",
                "snippet": "snippet",
            }
        ],
    }


async def _drain(gen):
    items = []
    async for item in gen:
        items.append(item)
    return items


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrator_sets_default_prompt_state_on_first_attempt() -> None:
    ctx = _make_ctx()
    orchestrator = _make_orchestrator()
    seen_states: list[dict[str, object]] = []

    async def fake_run_json_leaf_agent(*, ctx, parsed_state_key, **kwargs):
        seen_states.append(
            {
                "doc_search_rerank_only": ctx.session.state.get("doc_search_rerank_only"),
                "doc_search_retry_reason": ctx.session.state.get("doc_search_retry_reason"),
            }
        )
        ctx.session.state[parsed_state_key] = _document_list_result()
        return
        yield  # pragma: no cover

    with patch.object(orchestrator_module, "run_json_leaf_agent", fake_run_json_leaf_agent):
        with patch.object(orchestrator_module, "_persist_full_list", new=AsyncMock()):
            await _drain(orchestrator._run_async_impl(ctx))

    assert seen_states == [{"doc_search_rerank_only": False, "doc_search_retry_reason": ""}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrator_retries_on_empty_relevant_then_succeeds() -> None:
    ctx = _make_ctx()
    orchestrator = _make_orchestrator()
    calls: list[int] = []

    async def fake_run_json_leaf_agent(*, ctx, parsed_state_key, **kwargs):
        calls.append(int(ctx.session.state.get("doc_search_attempt") or 0))
        if len(calls) == 1:
            raise DocSearchRetryableValidationError("empty_relevant", "test")
        ctx.session.state[parsed_state_key] = _document_list_result()
        return
        yield  # pragma: no cover

    with patch.object(orchestrator_module, "run_json_leaf_agent", fake_run_json_leaf_agent):
        with patch.object(
            orchestrator_module,
            "_persist_full_list",
            new=AsyncMock(),
        ) as persist_mock:
            await _drain(orchestrator._run_async_impl(ctx))

    assert calls == [1, 2]
    assert ctx.session.state.get("doc_search_rerank_only") is True
    assert ctx.session.state["_root_final_text"] == DOC_SEARCH_SUCCESS_HINT
    persist_mock.assert_awaited_once()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrator_returns_no_data_after_two_empty_relevant_attempts() -> None:
    ctx = _make_ctx()
    orchestrator = _make_orchestrator()

    async def fake_run_json_leaf_agent(*, ctx, **kwargs):
        raise DocSearchRetryableValidationError("empty_relevant", "still empty")
        yield  # pragma: no cover

    with patch.object(orchestrator_module, "run_json_leaf_agent", fake_run_json_leaf_agent):
        await _drain(orchestrator._run_async_impl(ctx))

    assert ctx.session.state["_root_final_text"] == DOC_SEARCH_NO_DATA_MESSAGE


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrator_retries_on_invalid_document_id_then_succeeds() -> None:
    ctx = _make_ctx()
    orchestrator = _make_orchestrator()
    calls: list[int] = []

    async def fake_run_json_leaf_agent(*, ctx, parsed_state_key, **kwargs):
        calls.append(int(ctx.session.state.get("doc_search_attempt") or 0))
        if len(calls) == 1:
            raise DocSearchRetryableValidationError(
                "invalid_document_id",
                "doc-bad",
            )
        ctx.session.state[parsed_state_key] = _document_list_result()
        return
        yield  # pragma: no cover

    with patch.object(orchestrator_module, "run_json_leaf_agent", fake_run_json_leaf_agent):
        with patch.object(orchestrator_module, "_persist_full_list", new=AsyncMock()):
            await _drain(orchestrator._run_async_impl(ctx))

    assert calls == [1, 2]
    assert ctx.session.state.get("doc_search_retry_reason", "").startswith(
        "doc_search retryable validation: invalid_document_id"
    )
    assert ctx.session.state["_root_final_text"] == DOC_SEARCH_SUCCESS_HINT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrator_retries_on_validation_failure_then_succeeds() -> None:
    ctx = _make_ctx()
    orchestrator = _make_orchestrator()
    calls: list[int] = []

    async def fake_run_json_leaf_agent(*, ctx, parsed_state_key, **kwargs):
        calls.append(int(ctx.session.state.get("doc_search_attempt") or 0))
        if len(calls) == 1:
            raise AgentValidationFailure(
                log_label="doc_search_result_json",
                validation_error="unknown document_id",
                raw="{}",
                user_message=VALIDATION_ERROR_USER_MESSAGE,
            )
        ctx.session.state[parsed_state_key] = _document_list_result()
        return
        yield  # pragma: no cover

    with patch.object(orchestrator_module, "run_json_leaf_agent", fake_run_json_leaf_agent):
        with patch.object(orchestrator_module, "_persist_full_list", new=AsyncMock()):
            await _drain(orchestrator._run_async_impl(ctx))

    assert calls == [1, 2]
    assert ctx.session.state.get("doc_search_retry_reason") == "unknown document_id"
    assert ctx.session.state["_root_final_text"] == DOC_SEARCH_SUCCESS_HINT


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrator_raises_after_two_validation_failures() -> None:
    ctx = _make_ctx()
    orchestrator = _make_orchestrator()

    async def fake_run_json_leaf_agent(**kwargs):
        raise AgentValidationFailure(
            log_label="doc_search_result_json",
            validation_error="broken json",
            raw="not-json",
            user_message=VALIDATION_ERROR_USER_MESSAGE,
        )
        yield  # pragma: no cover

    with patch.object(orchestrator_module, "run_json_leaf_agent", fake_run_json_leaf_agent):
        with pytest.raises(AgentValidationFailure) as exc:
            await _drain(orchestrator._run_async_impl(ctx))

    assert exc.value.user_message == VALIDATION_ERROR_USER_MESSAGE
    assert ctx.session.state.get("_root_final_text") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_orchestrator_clears_kb_hits_state_on_new_search() -> None:
    ctx = _make_ctx()
    ctx.session.state["_doc_search_kb_hits"] = [{"document_id": "stale"}]
    orchestrator = _make_orchestrator()

    async def fake_run_json_leaf_agent(*, ctx, parsed_state_key, **kwargs):
        ctx.session.state[parsed_state_key] = _document_list_result()
        return
        yield  # pragma: no cover

    with patch.object(orchestrator_module, "run_json_leaf_agent", fake_run_json_leaf_agent):
        with patch.object(orchestrator_module, "_persist_full_list", new=AsyncMock()):
            await _drain(orchestrator._run_async_impl(ctx))

    assert "_doc_search_kb_hits" not in ctx.session.state
