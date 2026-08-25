from unittest.mock import AsyncMock

import pytest

from bot.services.dialog import build_blocks, run_turn


@pytest.mark.unit
def test_build_blocks_text_only() -> None:
    assert build_blocks("Привет", None) == [{"type": "text", "content": "Привет"}]


@pytest.mark.unit
def test_build_blocks_includes_empty_documents_placeholder_for_action() -> None:
    blocks = build_blocks("ok", {"type": "send_product_kit"})
    assert blocks[0] == {"type": "text", "content": "ok"}
    assert blocks[1] == {"type": "documents", "items": []}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_turn_calls_adk_and_returns_complete() -> None:
    adk = AsyncMock()
    adk.run.return_value = (
        "карточка продукта",
        [
            {
                "author": "root_agent",
                "actions": {"stateDelta": {"_bot_action": {"type": "send_product_kit"}}},
            }
        ],
    )

    result = await run_turn(adk, global_user_id="user-uuid", text="покажи карточку")

    assert result.status == "complete"
    assert result.error is None
    assert result.blocks[0]["content"] == "карточка продукта"
    assert result.blocks[1]["type"] == "documents"
    assert result.session_id.startswith("user-uuid::web::")
    adk.ensure_session.assert_awaited()
    adk.run.assert_awaited_once()
    assert adk.run.await_args.kwargs["text"] == "покажи карточку"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_turn_writes_history_for_channel() -> None:
    adk = AsyncMock()
    adk.run.return_value = ("ответ", [])
    store = AsyncMock()

    await run_turn(
        adk,
        global_user_id="user-uuid",
        text="вопрос",
        channel="web",
        store=store,
        platform_user_id=0,
    )

    assert store.append.await_count == 2
    first = store.append.await_args_list[0]
    assert first.args[:3] == (0, "user", "вопрос")
    assert first.kwargs["channel"] == "web"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_turn_rejects_empty_text() -> None:
    adk = AsyncMock()
    with pytest.raises(ValueError, match="empty_message"):
        await run_turn(adk, global_user_id="u", text="  ")
    adk.run.assert_not_called()
