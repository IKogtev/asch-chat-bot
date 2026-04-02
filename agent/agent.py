from .rootagent import RootAgent
from .config import (
    ACTIVE_DOCUMENTS_COLLECTION,
    KB_DOCUMENTS_COLLECTION,
    KB_TOP_K,
    build_common_model,
    KbSearchBackend,
    StubKbSearchBackend,
)
from .agents.owasp_agent import create_owasp_agent
from .agents.dispatcher_agent import create_dispatcher_agent
from .agents.doc_search_agent import create_doc_search_agent
from .agents.kb_answer_agent import create_kb_answer_agent
from typing import Optional


def build_agent_chain(kb_backend: Optional[KbSearchBackend] = None) -> RootAgent:
    """
    Создает полную цепочку агентов.
    
    Args:
        kb_backend: Бэкенд для поиска в базе знаний (опционально)
    
    Returns:
        RootAgent: Корневой агент цепочки
    """
    model = build_common_model()
    
    owasp_agent = create_owasp_agent(model)
    dispatcher_agent = create_dispatcher_agent(model)
    doc_search_agent = create_doc_search_agent(model)
    kb_answer_agent = create_kb_answer_agent(model)
    
    if kb_backend is None:
        kb_backend = StubKbSearchBackend()
    
    return RootAgent(
        owasp_agent=owasp_agent,
        dispatcher_agent=dispatcher_agent,
        doc_search_agent=doc_search_agent,
        kb_answer_agent=kb_answer_agent,
        kb_backend=kb_backend,
        doc_collection=ACTIVE_DOCUMENTS_COLLECTION,
        kb_collection=KB_DOCUMENTS_COLLECTION,
        top_k=KB_TOP_K,
    )


# Глобальный экземпляр для ADK runtime / adk web
root_agent = build_agent_chain()

__all__ = [
    "root_agent",
    "RootAgent",
    "build_agent_chain",
]