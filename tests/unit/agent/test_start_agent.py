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
    config_stub.ADVISOR_COMPROMISE_PENALTY = 20
    config_stub.ADVISOR_DIVERSITY_MAX_PER_FAMILY = 1
    config_stub.ADVISOR_DIVERSITY_MAX_SCORE_GAP = 25
    config_stub.ADVISOR_MINIMUM_CLIENT_TYPE_CONFIDENCE = 0.75
    config_stub.ADVISOR_MINIMUM_SCORE = 0
    config_stub.ADVISOR_PREFERRED_WEIGHT = 100
    config_stub.ADVISOR_SCORING_POLICY_VERSION = "test-pilot-v1"
    config_stub.ADVISOR_TOP_N = 3
    common_model = object()
    format_model = object()
    owasp_model = object()
    config_stub.build_common_model = lambda: common_model
    config_stub.build_format_model = lambda: format_model
    config_stub.build_owasp_model = lambda: owasp_model

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

    product_info_content_stub = types.ModuleType(
        "agent.agents.product_info_content_agent"
    )
    product_info_content_stub.create_product_info_content_agent = _agent_factory(
        "product_info_content_agent"
    )
    product_info_format_stub = types.ModuleType(
        "agent.agents.product_info_format_agent"
    )
    product_info_format_stub.create_product_info_format_agent = _agent_factory(
        "product_info_format_agent"
    )

    product_filter_content_stub = types.ModuleType(
        "agent.agents.product_filter_content_agent"
    )
    product_filter_content_stub.create_product_filter_content_agent = _agent_factory(
        "product_filter_content_agent"
    )
    product_filter_format_stub = types.ModuleType(
        "agent.agents.product_filter_format_agent"
    )
    product_filter_format_stub.create_product_filter_format_agent = _agent_factory(
        "product_filter_format_agent"
    )

    advisor_content_stub = types.ModuleType("agent.agents.advisor_content_agent")
    advisor_content_stub.create_advisor_content_agent = _agent_factory(
        "advisor_content_agent"
    )
    advisor_content_stub.create_advisor_content_repair_agent = _agent_factory(
        "advisor_content_repair_agent"
    )
    advisor_format_stub = types.ModuleType("agent.agents.advisor_format_agent")
    advisor_format_stub.create_advisor_format_agent = _agent_factory(
        "advisor_format_agent"
    )

    advisor_ranking_stub = types.ModuleType("agent.advisor_ranking_service")

    class AdvisorScoringPolicy:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    class AdvisorRankingService:
        def __init__(self, policy):
            self.policy = policy

    advisor_ranking_stub.AdvisorScoringPolicy = AdvisorScoringPolicy
    advisor_ranking_stub.AdvisorRankingService = AdvisorRankingService

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
        "agent.agents.product_info_content_agent": product_info_content_stub,
        "agent.agents.product_info_format_agent": product_info_format_stub,
        "agent.agents.product_filter_content_agent": product_filter_content_stub,
        "agent.agents.product_filter_format_agent": product_filter_format_stub,
        "agent.agents.advisor_content_agent": advisor_content_stub,
        "agent.agents.advisor_format_agent": advisor_format_stub,
        "agent.advisor_ranking_service": advisor_ranking_stub,
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
    assert module.root_agent.smalltalk_agent.name == "smalltalk_agent"
    assert (
        module.root_agent.product_info_content_agent.name
        == "product_info_content_agent"
    )
    assert module.root_agent.product_info_format_agent.name == "product_info_format_agent"
    assert (
        module.root_agent.product_filter_content_agent.name
        == "product_filter_content_agent"
    )
    assert (
        module.root_agent.product_filter_format_agent.name
        == "product_filter_format_agent"
    )
    assert module.root_agent.advisor_content_agent.name == "advisor_content_agent"
    assert (
        module.root_agent.advisor_content_repair_agent.name
        == "advisor_content_repair_agent"
    )
    assert module.root_agent.advisor_format_agent.name == "advisor_format_agent"
    assert module.root_agent.advisor_ranking_service.policy.version == "test-pilot-v1"
    assert (
        module.root_agent.product_info_content_agent.model
        is module.root_agent.dispatcher_agent.model
    )
    assert (
        module.root_agent.product_info_format_agent.model
        is module.root_agent.product_filter_format_agent.model
    )
    assert (
        module.root_agent.product_info_format_agent.model
        is not module.root_agent.product_info_content_agent.model
    )
    assert module.root_agent.owasp_agent.model is not module.root_agent.dispatcher_agent.model
