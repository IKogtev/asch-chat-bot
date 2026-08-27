"""Канально-нейтральный ход диалога: ADK /run → apply_bot_action → blocks."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from bot.services.adk_events import extract_bot_action
from bot.services.bot_actions import FileUrlIssuer, apply_bot_action
from bot.services.database import AdkApiClient
from utils.channel_session import build_session_id
from utils.logger import setup_logger

logger = setup_logger("dialog", "web_bff.log")

CHANNEL_WEB = "web"


@dataclass
class TurnResult:
    message_id: str
    session_id: str
    status: str
    blocks: list[dict[str, Any]]
    error: Optional[str] = None
    bot_action: Optional[dict[str, Any]] = field(default=None, repr=False)


def build_blocks(
    answer: str,
    documents: list[dict[str, Any]] | None = None,
    *,
    shown: int | None = None,
    total: int | None = None,
    has_more: bool | None = None,
) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = []
    text = (answer or "").strip()
    if text:
        blocks.append({"type": "text", "content": text})
    items = [item for item in (documents or []) if item.get("name")]
    if items or total is not None:
        block: dict[str, Any] = {"type": "documents", "items": items}
        if total is not None:
            block["shown"] = shown if shown is not None else len(items)
            block["total"] = total
            block["has_more"] = bool(has_more)
        blocks.append(block)
    return blocks


def _blocks_from_delivery(delivery, fallback_text: str = "") -> list[dict[str, Any]]:
    text = delivery.text if delivery.replace_answer else (delivery.text or fallback_text)
    return build_blocks(
        text,
        delivery.documents,
        shown=delivery.shown,
        total=delivery.total,
        has_more=delivery.has_more,
    )


async def paginate_search(
    store,
    *,
    global_user_id: str,
    mode: str,
    channel: str = CHANNEL_WEB,
    platform_user_id: int | str = 0,
    file_urls: FileUrlIssuer | None = None,
) -> TurnResult:
    """Следующая порция / весь список без ADK (кнопки UI)."""
    if mode not in {"more", "all"}:
        raise ValueError("bad_search_page_mode")
    turn_id = str(uuid.uuid4())
    session_id = build_session_id(global_user_id, channel, turn_id)
    action = "show_doc_list_more" if mode == "more" else "show_doc_list_all"
    label = "ещё" if mode == "more" else "все"
    delivery = await apply_bot_action(
        store,
        user_id=str(global_user_id),
        session_id=session_id,
        answer="",
        bot_action={"type": action},
        file_urls=file_urls,
    )
    blocks = _blocks_from_delivery(delivery)
    if store is not None:
        await store.append(platform_user_id, "user", label, global_user_id, channel=channel)
        await store.append(
            platform_user_id,
            "model",
            delivery.text or "",
            global_user_id,
            channel=channel,
            blocks=blocks,
        )
    return TurnResult(
        message_id=turn_id,
        session_id=session_id,
        status="complete",
        blocks=blocks,
    )


async def run_turn(
    adk: AdkApiClient,
    *,
    global_user_id: str,
    text: str,
    channel: str = CHANNEL_WEB,
    profile: Optional[dict[str, Any]] = None,
    store=None,
    platform_user_id: int | str = 0,
    file_urls: FileUrlIssuer | None = None,
) -> TurnResult:
    """Один ход: новая ADK-сессия как у бота, action → files, ответ в blocks."""
    user_text = (text or "").strip()
    if not user_text:
        raise ValueError("empty_message")

    turn_id = str(uuid.uuid4())
    message_id = turn_id
    session_id = build_session_id(global_user_id, channel, turn_id)
    adk_user_id = str(global_user_id)

    logger.info(
        "run_turn start channel=%s user=%s session=%s text=%s",
        channel,
        adk_user_id,
        session_id,
        user_text[:100],
    )

    await adk.ensure_session(user_id=adk_user_id, session_id=adk_user_id)
    if profile:
        await adk.set_user_state(
            user_id=adk_user_id,
            session_id=session_id,
            user_data=profile,
        )
    await adk.ensure_session(user_id=adk_user_id, session_id=session_id)

    answer, events = await adk.run(user_id=adk_user_id, session_id=session_id, text=user_text)
    bot_action = extract_bot_action(events)
    if bot_action:
        logger.info("run_turn bot_action type=%s", bot_action.get("type"))

    delivery = await apply_bot_action(
        store,
        user_id=adk_user_id,
        session_id=session_id,
        answer=answer or "",
        bot_action=bot_action if isinstance(bot_action, dict) else None,
        file_urls=file_urls,
    )
    if delivery.replace_answer:
        final_text = delivery.text
    else:
        final_text = delivery.text or (answer or "")

    blocks = build_blocks(
        final_text,
        delivery.documents,
        shown=delivery.shown,
        total=delivery.total,
        has_more=delivery.has_more,
    )
    if store is not None:
        await store.append(
            platform_user_id, "user", user_text, global_user_id, channel=channel
        )
        history_text = final_text or (answer or "")
        await store.append(
            platform_user_id,
            "model",
            history_text,
            global_user_id,
            channel=channel,
            blocks=blocks,
        )

    return TurnResult(
        message_id=message_id,
        session_id=session_id,
        status="complete",
        blocks=blocks,
        bot_action=bot_action if isinstance(bot_action, dict) else None,
    )
