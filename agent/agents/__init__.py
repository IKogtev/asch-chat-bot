"""
Подпакет с реализацией отдельных агентов.

Каждый агент реализован в отдельном модуле и экспортирует функцию create_*_agent(),
которая возвращает настроенный LlmAgent из Google ADK.

Доступные агенты:
- owasp_agent: проверка безопасности запроса
- dispatcher_agent: классификация запроса и выбор маршрута
- doc_search_agent: поиск документов в базе (LLM + kb_search)
- doc_search_orchestrator: композитный агент — поиск, пагинация, выдача document_id
- kb_answer_agent: ответы по базе знаний
"""

from .owasp_agent import create_owasp_agent
from .dispatcher_agent import create_dispatcher_agent
from .doc_search_agent import create_doc_search_agent
from .doc_search_orchestrator import create_doc_search_orchestrator
from .kb_answer_agent import create_kb_answer_agent

__all__ = [
    "create_owasp_agent",
    "create_dispatcher_agent",
    "create_doc_search_agent",
    "create_doc_search_orchestrator",
    "create_kb_answer_agent",
]