from .rootagent import RootAgent
from .config import (
    ACTIVE_DOCUMENTS_COLLECTION,
    KB_DOCUMENTS_COLLECTION,
    build_common_model,
)
from .agents.owasp_agent import create_owasp_agent
from .agents.dispatcher_agent import create_dispatcher_agent
from .agents.doc_search_agent import create_doc_search_agent
from .agents.kb_answer_agent import create_kb_answer_agent

def build_agent_chain() -> RootAgent:
    """
    Создает полную цепочку агентов.
    """
    model = build_common_model()

    doc_search_agent = create_doc_search_agent(model)
    kb_answer_agent = create_kb_answer_agent(model)
    dispatcher_agent = create_dispatcher_agent(model)
    owasp_agent = create_owasp_agent(model)

    return RootAgent(
        owasp_agent=owasp_agent,
        dispatcher_agent=dispatcher_agent,
        doc_search_agent=doc_search_agent,
        kb_answer_agent=kb_answer_agent,
        doc_collection=ACTIVE_DOCUMENTS_COLLECTION,
        kb_collection=KB_DOCUMENTS_COLLECTION,
    )


root_agent = build_agent_chain()

__all__ = [
    "root_agent",
    "RootAgent",
    "build_agent_chain",
]