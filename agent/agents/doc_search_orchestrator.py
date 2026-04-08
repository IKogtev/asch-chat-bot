"""
Композитный агент: поиск документов (LLM + kb_search), нормализация полного списка, сохранение в БД.

Текст из JSON doc_search_agent (поле message при mode=document_list) пользователю как список не показывается:
итоговый вывод — через UI бота из БД. Здесь при успехе выставляется краткий служебный _root_final_text
(DOC_SEARCH_SUCCESS_HINT); в Telegram при смене search_id бот дополнительно шлёт отрендеренную первую порцию.

Пагинация и скачивание по номеру — в Telegram-боте (handlers); прямой вызов ADK с intent show_more/show_all/file_download
даёт краткую подсказку (для web без бота).
"""
from typing import Any, AsyncGenerator, Dict, List, Optional

from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event

from ..config import ACTIVE_DOCUMENTS_COLLECTION, DOC_SEARCH_PAGE_SIZE
from ..helpers import DOC_SEARCH_SUCCESS_HINT, format_text_answer, truncate_for_log
from ..json_leaf_runner import run_json_leaf_agent
from .doc_search_agent import validate_doc_search_result
from utils.logger import setup_logger

logger = setup_logger("doc_search_orchestrator", "agent.log")


def _follow_up_unhandled_in_agent_hint(intent: str) -> str:
    """
    Текст, если follow-up (ещё / все / номер) дошёл до оркестратора (не обработан клиентом).
    Формулировки согласованы с bot.services.config (SHOW_MORE_RE, SHOW_ALL_RE) и parse_download_ranks.
    """
    if intent == "show_more":
        return (
            "Следующую порцию списка документов можно запросить отдельным сообщением, "
            "например: «ещё», «покажи ещё», «дальше» или «ещё файлы»."
        )
    if intent == "show_all":
        return (
            "Весь список документов можно запросить отдельным сообщением, "
            "например: «все», «покажи все», «всё», «да» или «полный список»."
        )
    if intent == "file_download":
        return (
            "Скачивание по номеру из списка выполняется так: отправьте номер документа "
            "(например «3»), несколько номеров через запятую или фразу вида «скачай 1», «1 и 3»."
        )

def _telegram_user_id(ctx: InvocationContext) -> Optional[int]:
    """ID пользователя Telegram из ADK Session (тот же, что в /run)."""
    raw = getattr(ctx.session, "user_id", None)
    if raw is None:
        return None
    try:
        return int(str(raw).strip())
    except ValueError:
        return None


async def _persist_full_list(
    ctx: InvocationContext,
    *,
    query: str,
    items: List[Dict[str, Any]],
    shown_count: int,
) -> None:
    from utils.search_results_db import get_shared_pool, save_doc_search_results

    pool = await get_shared_pool()
    if pool is None:
        logger.warning("DATABASE_URL не задан — список документов не сохранён в PostgreSQL")
        return
    uid = _telegram_user_id(ctx)
    if uid is None:
        logger.warning("session.user_id отсутствует — пропуск сохранения списка в БД")
        return
    sid = getattr(ctx.session, "id", None) or ""
    if not sid:
        logger.warning("session.id отсутствует — пропуск сохранения списка в БД")
        return
    db_items: List[Dict[str, Any]] = []
    for it in items:
        db_items.append(
            {
                "rank": int(it["rank"]),
                "document_id": str(it["document_id"]),
                "source_name": str(it["source_name"]),
                "source_path": it.get("source_path"),
                "score": it.get("score"),
                "snippet": (it.get("snippet") or "")[:2000],
            }
        )
    try:
        await save_doc_search_results(pool, uid, sid, query, db_items, shown_count)
        logger.info(
            "doc_search: сохранено в БД user_id=%s session=%s rows=%s shown=%s",
            uid,
            sid,
            len(db_items),
            shown_count,
        )
    except Exception as e:
        logger.error("doc_search: ошибка сохранения в БД: %s", e, exc_info=True)


class DocSearchOrchestrator(BaseAgent):
    """
    Только новый поиск (intent doc_search): LLM + kb_search → полный список → БД.
    """

    doc_search_agent: LlmAgent
    doc_collection: str
    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        doc_search_agent: LlmAgent,
        doc_collection: str = ACTIVE_DOCUMENTS_COLLECTION,
    ):
        super().__init__(
            name="doc_search_orchestrator",
            doc_search_agent=doc_search_agent,
            doc_collection=doc_collection,
            sub_agents=[doc_search_agent],
        )

    def _get_required_state_dict(self, ctx: InvocationContext, key: str) -> Dict[str, Any]:
        value = ctx.session.state.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"State key '{key}' must be dict, got {type(value)}")
        return value

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        user_message = str(ctx.session.state.get("user_query") or "").strip()
        intent = str(ctx.session.state.get("doc_search_intent") or "doc_search").strip()
        search_query = str(ctx.session.state.get("doc_search_search_query") or "").strip()

        logger.info(
            "doc_search_orchestrator: intent=%s query=%s",
            intent,
            truncate_for_log((search_query or user_message).strip(), 300),
        )

        if intent in ("show_more", "show_all", "file_download"):
            ctx.session.state["_root_final_text"] = _follow_up_unhandled_in_agent_hint(intent)
            return

        page = DOC_SEARCH_PAGE_SIZE

        effective_search_query = (search_query or user_message).strip()
        ctx.session.state["search_query"] = effective_search_query
        ctx.session.state["doc_search_collection"] = self.doc_collection

        async for event in run_json_leaf_agent(
            ctx=ctx,
            agent=self.doc_search_agent,
            output_key="doc_search_result_json",
            parsed_state_key="_doc_search_result_parsed",
            validator=validate_doc_search_result,
            log_label="doc_search_result_json",
        ):
            yield event

        doc_search = self._get_required_state_dict(ctx, "_doc_search_result_parsed")

        if doc_search["mode"] != "document_list":
            ctx.session.state["_root_final_text"] = format_text_answer(doc_search["message"])
            return

        results_raw = doc_search["results"]
        normalized: List[Dict[str, Any]] = []
        for i, item in enumerate(results_raw, start=1):
            normalized.append(
                {
                    "document_id": item["document_id"],
                    "source_name": item["source_name"],
                    "source_path": item.get("source_path"),
                    "snippet": item.get("snippet") or "",
                    "rank": i,
                }
            )

        shown = min(page, len(normalized))
        # Не текст LLM из doc_search — список пользователю строит UI из БД (см. handlers: render_results).
        ctx.session.state["_root_final_text"] = DOC_SEARCH_SUCCESS_HINT

        await _persist_full_list(
            ctx,
            query=effective_search_query,
            items=normalized,
            shown_count=shown,
        )


def create_doc_search_orchestrator(
    doc_search_agent: LlmAgent,
    *,
    doc_collection: str = ACTIVE_DOCUMENTS_COLLECTION,
) -> DocSearchOrchestrator:
    return DocSearchOrchestrator(
        doc_search_agent=doc_search_agent,
        doc_collection=doc_collection,
    )
