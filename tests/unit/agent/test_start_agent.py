import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_start_agent_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "start_agent.py"

    agent_pkg = types.ModuleType("agent")
    agent_pkg.__path__ = [str(repo_root / "agent")]

    class App:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    app_stub = types.ModuleType("google.adk.apps.app")
    app_stub.App = App

    rootagent_stub = types.ModuleType("agent.rootagent")

    class RootAgent:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    rootagent_stub.RootAgent = RootAgent

    config_stub = types.ModuleType("agent.config")
    config_stub.ACTIVE_DOCUMENTS_COLLECTION = "active_docs"
    config_stub.KB_DOCUMENTS_COLLECTION = "kb_docs"
    config_stub.build_common_model = lambda: object()

    def _agent_factory(name):
        return lambda model: types.SimpleNamespace(name=name, model=model)

    owasp_stub = types.ModuleType("agent.agents.owasp_agent")
    owasp_stub.create_owasp_agent = _agent_factory("owasp_agent")

    dispatcher_stub = types.ModuleType("agent.agents.dispatcher_agent")
    dispatcher_stub.create_dispatcher_agent = _agent_factory("dispatcher_agent")

    doc_search_stub = types.ModuleType("agent.agents.doc_search_agent")
    doc_search_stub.create_doc_search_agent = _agent_factory("doc_search_agent")

    doc_orchestrator_stub = types.ModuleType("agent.agents.doc_search_orchestrator")
    doc_orchestrator_stub.create_doc_search_orchestrator = (
        lambda agent, doc_collection: types.SimpleNamespace(
            agent=agent,
            doc_collection=doc_collection,
        )
    )

    kb_answer_stub = types.ModuleType("agent.agents.kb_answer_agent")
    kb_answer_stub.create_kb_answer_agent = _agent_factory("kb_answer_agent")

    smalltalk_stub = types.ModuleType("agent.agents.smalltalk_agent")
    smalltalk_stub.create_smalltalk_agent = _agent_factory("smalltalk_agent")

    product_selection_stub = types.ModuleType("agent.agents.product_selection_agent")
    product_selection_stub.create_product_selection_agent = _agent_factory("product_selection_agent")

    for name, module in {
        "agent": agent_pkg,
        "google.adk.apps.app": app_stub,
        "agent.rootagent": rootagent_stub,
        "agent.config": config_stub,
        "agent.agents.owasp_agent": owasp_stub,
        "agent.agents.dispatcher_agent": dispatcher_stub,
        "agent.agents.doc_search_agent": doc_search_stub,
        "agent.agents.doc_search_orchestrator": doc_orchestrator_stub,
        "agent.agents.kb_answer_agent": kb_answer_stub,
        "agent.agents.smalltalk_agent": smalltalk_stub,
        "agent.agents.product_selection_agent": product_selection_stub,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)

    spec = importlib.util.spec_from_file_location("agent.start_agent_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_start_agent_exports_app(monkeypatch) -> None:
    module = _load_start_agent_module(monkeypatch)

    assert module.app.name == "agent"
    assert module.app.root_agent is module.root_agent
    assert not hasattr(module.app, "events_compaction_config")
    assert module.root_agent.product_selection_agent.name == "product_selection_agent"
