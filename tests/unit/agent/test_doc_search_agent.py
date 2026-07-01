import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_doc_search_module():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "agents" / "doc_search_agent.py"

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
    config_stub.KBSEARCH_MCP_URL = ""
    config_stub.MCP_TOKEN = ""
    config_stub.MCP_TIMEOUT_SEC = 30.0

    helpers_stub = types.ModuleType("agent.helpers")
    helpers_stub.load_prompt = lambda *args, **kwargs: "prompt"

    prompt_loader_stub = types.ModuleType("agent.prompt_loader")
    prompt_loader_stub.start_prompt_watcher = lambda *args, **kwargs: None

    tools_pkg = types.ModuleType("agent.tools")
    tools_pkg.__path__ = [str(repo_root / "agent" / "tools")]

    refreshing_toolset_stub = types.ModuleType("agent.tools.refreshing_mcp_toolset")
    refreshing_toolset_stub.RefreshingMcpToolset = type("RefreshingMcpToolset", (), {})

    adk_agents_stub = types.ModuleType("google.adk.agents")
    adk_agents_stub.LlmAgent = type("LlmAgent", (), {})

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

    sys.modules["agent"] = agent_pkg
    sys.modules["agent.agents"] = agents_pkg
    sys.modules["utils.logger"] = logger_stub
    sys.modules["agent.config"] = config_stub
    sys.modules["agent.helpers"] = helpers_stub
    sys.modules["agent.prompt_loader"] = prompt_loader_stub
    sys.modules["agent.tools"] = tools_pkg
    sys.modules["agent.tools.refreshing_mcp_toolset"] = refreshing_toolset_stub
    sys.modules["google.adk.agents"] = adk_agents_stub
    sys.modules["google.adk.models.lite_llm"] = lite_llm_stub
    sys.modules[
        "google.adk.tools.mcp_tool.mcp_session_manager"
    ] = mcp_session_manager_stub
    sys.modules["google.adk.tools.mcp_tool.mcp_toolset"] = mcp_toolset_stub

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
    assert validation_utils_spec is not None and validation_utils_spec.loader is not None
    assert kb_context_spec is not None and kb_context_spec.loader is not None
    assert validation_spec is not None and validation_spec.loader is not None
    validation_utils_module = importlib.util.module_from_spec(validation_utils_spec)
    kb_context_module = importlib.util.module_from_spec(kb_context_spec)
    validation_module = importlib.util.module_from_spec(validation_spec)
    sys.modules["agent.agents.validation_utils"] = validation_utils_module
    sys.modules["agent.doc_search_kb_context"] = kb_context_module
    sys.modules["agent.doc_search_validation"] = validation_module
    validation_utils_spec.loader.exec_module(validation_utils_module)
    kb_context_spec.loader.exec_module(kb_context_module)
    validation_spec.loader.exec_module(validation_module)

    spec = importlib.util.spec_from_file_location("agent.agents.doc_search_agent", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.agents.doc_search_agent"] = module
    spec.loader.exec_module(module)
    return module


doc_search_module = _load_doc_search_module()
validate_doc_search_result = doc_search_module.validate_doc_search_result
DocSearchRetryableValidationError = sys.modules[
    "agent.doc_search_validation"
].DocSearchRetryableValidationError
VALIDATION_CONTEXT: dict = {}
KB_HITS_CONTEXT = {
    "_doc_search_kb_hits": [
        {
            "rank": 1,
            "document_id": "doc-1",
            "source_name": "file.pdf",
            "source_path": "/x/file.pdf",
        }
    ]
}


@pytest.mark.unit
def test_validate_doc_search_result_accepts_document_list() -> None:
    result = validate_doc_search_result(
        {
            "status": "ok",
            "mode": "document_list",
            "message": "",
            "results": [
                {
                    "document_id": "doc-2",
                    "source_name": "second.pdf",
                    "source_path": "/x/second.pdf",
                    "is_relevant": True,
                    "new_rank": 2,
                    "snippet": "second",
                },
                {
                    "document_id": "doc-1",
                    "source_name": "file.pdf",
                    "source_path": "/x/file.pdf",
                    "is_relevant": True,
                    "new_rank": 1,
                    "snippet": "first",
                },
                {
                    "document_id": "doc-3",
                    "source_name": "skip.pdf",
                    "is_relevant": False,
                    "new_rank": None,
                    "snippet": "filtered out",
                },
            ],
        },
        VALIDATION_CONTEXT,
    )

    assert result["mode"] == "document_list"
    assert [item["document_id"] for item in result["results"]] == ["doc-1", "doc-2"]


@pytest.mark.unit
def test_validate_doc_search_result_allows_duplicate_new_rank() -> None:
    result = validate_doc_search_result(
        {
            "status": "ok",
            "mode": "document_list",
            "message": "",
            "results": [
                {
                    "document_id": "doc-a",
                    "source_name": "a.pdf",
                    "is_relevant": True,
                    "new_rank": 1,
                    "snippet": "a",
                },
                {
                    "document_id": "doc-b",
                    "source_name": "b.pdf",
                    "is_relevant": True,
                    "new_rank": 1,
                    "snippet": "b",
                },
                {
                    "document_id": "doc-c",
                    "source_name": "c.pdf",
                    "is_relevant": True,
                    "new_rank": 2,
                    "snippet": "c",
                },
            ],
        },
        VALIDATION_CONTEXT,
    )

    assert [item["document_id"] for item in result["results"]] == ["doc-a", "doc-b", "doc-c"]


@pytest.mark.unit
def test_validate_doc_search_result_accepts_no_data_without_results() -> None:
    result = validate_doc_search_result(
        {
            "status": "ok",
            "mode": "no_data",
            "message": "Ничего не найдено",
        },
        VALIDATION_CONTEXT,
    )

    assert result["results"] == []


@pytest.mark.unit
def test_validate_doc_search_result_rejects_invalid_mode() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result({"status": "ok", "mode": "bad", "message": "x"}, VALIDATION_CONTEXT)

    assert "doc_search_agent" in str(exc.value)
    assert "invalid mode" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_requires_message_for_non_document_list() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result({"status": "ok", "mode": "info", "message": ""}, VALIDATION_CONTEXT)

    assert "requires non-empty message" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_rejects_results_for_non_document_list() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result(
            {
                "status": "ok",
                "mode": "info",
                "message": "служебно",
                "results": [{"document_id": "1", "source_name": "x"}],
            },
            VALIDATION_CONTEXT,
        )

    assert "must not contain results" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_rejects_empty_document_list() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result(
            {"status": "ok", "mode": "document_list", "message": "", "results": []},
            VALIDATION_CONTEXT,
        )

    assert "requires non-empty results array" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_rejects_missing_rank_fields() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result(
            {
                "status": "ok",
                "mode": "document_list",
                "message": "",
                "results": [
                    {
                        "document_id": "doc-1",
                        "source_name": "file.pdf",
                        "snippet": "x",
                    }
                ],
            },
            VALIDATION_CONTEXT,
        )

    assert "missing new_rank" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_accepts_relevant_without_is_relevant() -> None:
    result = validate_doc_search_result(
        {
            "status": "ok",
            "mode": "document_list",
            "message": "",
            "results": [
                {
                    "document_id": "doc-1",
                    "source_name": "file.pdf",
                    "new_rank": 1,
                    "snippet": "x",
                }
            ],
        },
        KB_HITS_CONTEXT,
    )

    assert result["results"][0]["document_id"] == "doc-1"


@pytest.mark.unit
def test_validate_doc_search_result_rejects_unknown_document_id() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result(
            {
                "status": "ok",
                "mode": "document_list",
                "message": "",
                "results": [
                    {
                        "document_id": "doc-unknown",
                        "source_name": "file.pdf",
                        "new_rank": 1,
                        "snippet": "x",
                    }
                ],
            },
            KB_HITS_CONTEXT,
        )

    assert "not in kb_search results" in str(exc.value)


@pytest.mark.unit
def test_validate_doc_search_result_retries_when_all_irrelevant_and_kb_had_hits() -> None:
    with pytest.raises(DocSearchRetryableValidationError) as exc:
        validate_doc_search_result(
            {
                "status": "ok",
                "mode": "document_list",
                "message": "",
                "results": [
                    {
                        "document_id": "doc-1",
                        "source_name": "file.pdf",
                        "is_relevant": False,
                        "new_rank": None,
                        "snippet": "x",
                    }
                ],
            },
            KB_HITS_CONTEXT,
        )

    assert exc.value.reason == "empty_relevant"


@pytest.mark.unit
def test_validate_doc_search_result_retries_no_data_when_kb_had_hits() -> None:
    with pytest.raises(DocSearchRetryableValidationError) as exc:
        validate_doc_search_result(
            {
                "status": "ok",
                "mode": "no_data",
                "message": "Ничего не найдено",
            },
            KB_HITS_CONTEXT,
        )

    assert exc.value.reason == "empty_relevant"


@pytest.mark.unit
def test_validate_doc_search_result_reports_invalid_items_after_normalization() -> None:
    with pytest.raises(ValueError) as exc:
        validate_doc_search_result(
            {
                "status": "ok",
                "mode": "document_list",
                "message": "",
                "results": [
                    {
                        "source_name": "x",
                        "is_relevant": True,
                        "new_rank": 1,
                    }
                ],
            },
            VALIDATION_CONTEXT,
        )

    assert "missing document_id" in str(exc.value)
