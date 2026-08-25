"""Канально-нейтральное исполнение _bot_action: комплект, скачивание, пагинация поиска.

Не импортирует handlers.py: там Telegram/MAX send и /reset.
Переиспользует get_product_kit, PostgresChatStore и список документов.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

from bot.services.config import Settings
from bot.services.product_kits import get_product_kit
from utils.channel_session import parse_session_id
from utils.doc_search_format import is_archive_document
from utils.logger import setup_logger

logger = setup_logger("bot_actions", "web_bff.log")

NO_SAVED_LIST = "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
ARCHIVE_KIT_NOTICE = "Внимание: данный продукт находится в архиве."


class FileUrlIssuer(Protocol):
    def kit_url(self, user_id: str, path: str, name: str) -> str: ...

    def kb_url(self, user_id: str, document_id: str, name: str) -> str: ...


@dataclass
class ActionDelivery:
    """Текст для ленты + файлы. replace_answer=True — не показывать сырой ответ ADK."""

    text: str = ""
    replace_answer: bool = False
    documents: list[dict[str, Any]] = field(default_factory=list)


async def _search_session_id(store, user_id: str, request_session_id: str) -> str:
    meta = await store.get_last_search_meta(user_id, request_session_id)
    if meta:
        return request_session_id
    latest = await store.get_latest_search_session_id(
        user_id, channel=parse_session_id(request_session_id).channel
    )
    return latest or request_session_id


def _doc_item(*, name: str, url: str | None, size: int | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name}
    if url:
        item["url"] = url
    if size is not None:
        item["size"] = size
    return item


def _search_list_caption(visible: int, total: int, offset: int) -> str:
    shown_end = offset + visible
    if shown_end < total:
        return (
            f"Найдено документов: {total}. Показано {shown_end} из {total}. "
            "Напишите «ещё» или «все», чтобы увидеть остальные."
        )
    return f"Найдено документов: {total}."


def _search_items_to_documents(
    items: list[dict[str, Any]],
    *,
    user_id: str,
    file_urls: FileUrlIssuer | None,
    offset: int = 0,
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []
    for index, item in enumerate(items):
        rank = int(item.get("rank") or (offset + index + 1))
        name = item.get("source_name") or f"document-{rank}"
        doc_id = item.get("document_id")
        url = None
        if file_urls is not None and doc_id:
            url = file_urls.kb_url(user_id, str(doc_id), name)
        entry = _doc_item(name=name, url=url)
        entry["rank"] = rank
        if is_archive_document(item):
            entry["archive"] = True
        documents.append(entry)
    return documents


def _load_product_kit(bot_action: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    product_code = str(bot_action.get("product_code") or "").strip()
    product_name = str(bot_action.get("product_name") or "").strip()
    folder_kit = str(bot_action.get("folder_kit") or "").strip()
    folder_kit_root = str(bot_action.get("folder_kit_root") or "").strip()
    result = get_product_kit(
        product_code=product_code,
        product_name=product_name,
        folder_kit=folder_kit,
        folder_kit_root=folder_kit_root,
    )
    from_archive = False
    if result["status"] in ("not_found", "empty") and folder_kit_root != "archive":
        result = get_product_kit(
            product_code=product_code,
            product_name=product_name,
            folder_kit=folder_kit,
            folder_kit_root="archive",
        )
        from_archive = result["status"] == "ok"
    return result, from_archive


async def _deliver_kit(
    bot_action: dict[str, Any],
    *,
    user_id: str,
    answer: str,
    file_urls: FileUrlIssuer | None,
) -> ActionDelivery:
    result, from_archive = _load_product_kit(bot_action)
    if result["status"] != "ok":
        return ActionDelivery(text=result["message"], replace_answer=True)

    notices: list[str] = []
    if from_archive:
        notices.append(ARCHIVE_KIT_NOTICE)
    if (answer or "").strip():
        notices.append(answer.strip())

    documents = []
    for file_info in result["files"]:
        url = None
        if file_urls is not None:
            url = file_urls.kit_url(user_id, file_info["path"], file_info["name"])
        documents.append(
            _doc_item(name=file_info["name"], url=url, size=file_info.get("size"))
        )
    return ActionDelivery(text="\n\n".join(notices), documents=documents)


async def _deliver_downloads(
    store,
    bot_action: dict[str, Any],
    *,
    user_id: str,
    session_id: str,
    file_urls: FileUrlIssuer | None,
) -> ActionDelivery:
    ranks = bot_action.get("ranks") or []
    search_session_id = await _search_session_id(store, user_id, session_id)
    notices: list[str] = []
    documents: list[dict[str, Any]] = []
    for rank in ranks:
        item = await store.get_result_by_rank(user_id, search_session_id, rank)
        if not item:
            notices.append(f"Не нашёл документ №{rank} в последнем списке.")
            continue
        doc_id = item.get("document_id")
        name = item.get("source_name") or f"document-{rank}"
        if not doc_id:
            notices.append(f"Не удалось определить document_id для документа №{rank}.")
            continue
        url = file_urls.kb_url(user_id, str(doc_id), name) if file_urls is not None else None
        documents.append(_doc_item(name=name, url=url))
    return ActionDelivery(
        text="\n".join(notices),
        replace_answer=True,
        documents=documents,
    )


async def _deliver_show_more(
    store,
    *,
    user_id: str,
    session_id: str,
    page_size: int,
    file_urls: FileUrlIssuer | None,
) -> ActionDelivery:
    search_session_id = await _search_session_id(store, user_id, session_id)
    meta = await store.get_last_search_meta(user_id, search_session_id)
    items = await store.get_last_search_results(user_id, search_session_id)
    if not meta or not items:
        return ActionDelivery(text=NO_SAVED_LIST, replace_answer=True)

    start = int(meta.get("shown_count") or 0)
    if start >= len(items):
        return ActionDelivery(text="Это уже все найденные файлы.", replace_answer=True)

    end = min(start + page_size, len(items))
    chunk = items[start:end]
    await store.update_shown_count(user_id, search_session_id, end)
    return ActionDelivery(
        text=_search_list_caption(len(chunk), len(items), start),
        replace_answer=True,
        documents=_search_items_to_documents(
            chunk, user_id=user_id, file_urls=file_urls, offset=start
        ),
    )


async def _deliver_show_all(
    store,
    *,
    user_id: str,
    session_id: str,
    file_urls: FileUrlIssuer | None,
) -> ActionDelivery:
    search_session_id = await _search_session_id(store, user_id, session_id)
    items = await store.get_last_search_results(user_id, search_session_id)
    if not items:
        return ActionDelivery(text=NO_SAVED_LIST, replace_answer=True)
    await store.update_shown_count(user_id, search_session_id, len(items))
    return ActionDelivery(
        text=_search_list_caption(len(items), len(items), 0),
        replace_answer=True,
        documents=_search_items_to_documents(
            items, user_id=user_id, file_urls=file_urls, offset=0
        ),
    )


async def _deliver_new_search_list(
    store,
    *,
    user_id: str,
    session_id: str,
    file_urls: FileUrlIssuer | None,
) -> ActionDelivery | None:
    meta = await store.get_last_search_meta(user_id, session_id)
    if not meta:
        return None
    items = await store.get_last_search_results(user_id, session_id)
    if not items:
        return None
    shown = min(max(int(meta.get("shown_count") or 5), 0), len(items))
    chunk = items[:shown]
    return ActionDelivery(
        text=_search_list_caption(len(chunk), len(items), 0),
        replace_answer=True,
        documents=_search_items_to_documents(
            chunk, user_id=user_id, file_urls=file_urls, offset=0
        ),
    )


async def apply_bot_action(
    store,
    *,
    user_id: str,
    session_id: str,
    answer: str,
    bot_action: dict[str, Any] | None,
    file_urls: FileUrlIssuer | None = None,
    page_size: int | None = None,
) -> ActionDelivery:
    """Собирает текст и documents для web (и любого канала без bot_res)."""
    size = page_size if page_size is not None else Settings.SHOW_MAX
    action_type = bot_action.get("type") if isinstance(bot_action, dict) else None

    if action_type == "send_product_kit":
        return await _deliver_kit(
            bot_action, user_id=user_id, answer=answer, file_urls=file_urls
        )
    if store is None:
        return ActionDelivery(text=(answer or "").strip())
    if action_type == "download_by_ranks":
        return await _deliver_downloads(
            store,
            bot_action,
            user_id=user_id,
            session_id=session_id,
            file_urls=file_urls,
        )
    if action_type == "show_doc_list_more":
        return await _deliver_show_more(
            store,
            user_id=user_id,
            session_id=session_id,
            page_size=size,
            file_urls=file_urls,
        )
    if action_type == "show_doc_list_all":
        return await _deliver_show_all(
            store, user_id=user_id, session_id=session_id, file_urls=file_urls
        )

    new_list = await _deliver_new_search_list(
        store, user_id=user_id, session_id=session_id, file_urls=file_urls
    )
    if new_list is not None:
        logger.info("new search list for session=%s user=%s", session_id, user_id)
        return new_list

    return ActionDelivery(text=(answer or "").strip())
