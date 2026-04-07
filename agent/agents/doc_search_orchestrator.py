"""
Композитный агент: поиск документов (LLM + kb_search), пагинация, сохранение списка в БД,
выдача HTML пользователю и строки document_id для скачивания ботом.
"""
from typing import Any, AsyncGenerator, Dict, List, Optional

from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event

from ..config import ACTIVE_DOCUMENTS_COLLECTION, DOC_SEARCH_PAGE_SIZE
from ..helpers import format_text_answer, truncate_for_log
from ..json_leaf_runner import run_json_leaf_agent
from .doc_search_agent import validate_doc_search_result
from utils.doc_search_format import parse_download_ranks, render_doc_list_html
from utils.logger import setup_logger

logger = setup_logger("doc_search_orchestrator", "agent.log")


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
                "relatuve_path": it.get("relative_path"),
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


async def _persist_shown_count(ctx: InvocationContext, shown_count: int) -> None:
    from utils.search_results_db import get_shared_pool, update_doc_search_shown_count

    pool = await get_shared_pool()
    if pool is None:
        return
    uid = _telegram_user_id(ctx)
    if uid is None:
        return
    sid = getattr(ctx.session, "id", None) or ""
    if not sid:
        return
    try:
        await update_doc_search_shown_count(pool, uid, sid, shown_count)
    except Exception as e:
        logger.error("doc_search: update_shown_count: %s", e, exc_info=True)


class DocSearchOrchestrator(BaseAgent):
    """
    intent из dispatcher (в state до вызова): doc_search | show_more | show_all | file_download.
    user_query и doc_search_search_query выставляет RootAgent.
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

        page = DOC_SEARCH_PAGE_SIZE

        if intent == "show_more":
            items = ctx.session.state.get("doc_search_list_items")
            if not isinstance(items, list) or not items:
                ctx.session.state["_root_final_text"] = (
                    "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                )
                return

            shown = int(ctx.session.state.get("doc_search_shown_count") or 0)
            start = shown
            if start >= len(items):
                ctx.session.state["_root_final_text"] = "Это уже все найденные файлы."
                return

            end = min(start + page, len(items))
            chunk = items[start:end]
            ctx.session.state["doc_search_shown_count"] = end
            await _persist_shown_count(ctx, end)
            ctx.session.state["_root_final_text"] = render_doc_list_html(chunk, total=len(items), offset=start)
            return

        if intent == "show_all":
            items = ctx.session.state.get("doc_search_list_items")
            if not isinstance(items, list) or not items:
                ctx.session.state["_root_final_text"] = (
                    "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                )
                return

            n = len(items)
            ctx.session.state["doc_search_shown_count"] = n
            await _persist_shown_count(ctx, n)
            ctx.session.state["_root_final_text"] = render_doc_list_html(items, total=n, offset=0)
            return

        if intent == "file_download":
            ranks = parse_download_ranks(user_message)
            if not ranks:
                ctx.session.state["_root_final_text"] = (
                    "Укажите номер документа из списка (например: «1» или «скачай 2»)."
                )
                return

            items = ctx.session.state.get("doc_search_list_items")
            if not isinstance(items, list) or not items:
                ctx.session.state["_root_final_text"] = (
                    "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                )
                return

            lines: List[str] = []
            for r in ranks:
                if r < 1 or r > len(items):
                    lines.append(f"Не нашёл документ №{r} в последнем списке.")
                    continue
                doc_id = items[r - 1].get("document_id")
                if not doc_id:
                    lines.append(f"Не удалось определить document_id для документа №{r}.")
                    continue
                lines.append(f"document_id:{doc_id}")

            ctx.session.state["_root_final_text"] = "\n".join(lines)
            return

        # Новый поиск (intent == "doc_search")
        ctx.session.state.pop("doc_search_list_items", None)
        ctx.session.state.pop("doc_search_shown_count", None)

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

        ctx.session.state["doc_search_list_items"] = normalized
        shown = min(page, len(normalized))
        ctx.session.state["doc_search_shown_count"] = shown

        first = normalized[:shown]
        ctx.session.state["_root_final_text"] = render_doc_list_html(
            first, total=len(normalized), offset=0
        )

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
