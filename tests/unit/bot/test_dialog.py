from unittest.mock import AsyncMock

import pytest

from bot.services.dialog import build_blocks, paginate_search, run_turn


@pytest.mark.unit
def test_build_blocks_text_only() -> None:
    assert build_blocks("Привет", None) == [{"type": "text", "content": "Привет"}]


@pytest.mark.unit
def test_build_blocks_includes_documents() -> None:
    blocks = build_blocks("ok", [{"name": "a.pdf", "url": "/files/x"}])
    assert blocks[0] == {"type": "text", "content": "ok"}
    assert blocks[1] == {"type": "documents", "items": [{"name": "a.pdf", "url": "/files/x"}]}


@pytest.mark.unit
def test_build_blocks_includes_search_pagination() -> None:
    blocks = build_blocks(
        "Найдено 3",
        [{"name": "a.pdf", "url": "/files/a"}],
        shown=1,
        total=3,
        has_more=True,
    )
    assert blocks[1]["has_more"] is True
    assert blocks[1]["shown"] == 1
    assert blocks[1]["total"] == 3


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_turn_calls_adk_and_returns_complete() -> None:
    adk = AsyncMock()
    adk.run.return_value = (
        "карточка продукта",
        [{"author": "root_agent", "actions": {"stateDelta": {}}}],
    )

    result = await run_turn(adk, global_user_id="user-uuid", text="покажи карточку")

    assert result.status == "complete"
    assert result.error is None
    assert result.blocks[0]["content"] == "карточка продукта"
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
    store.get_last_search_meta = AsyncMock(return_value=None)
    store.get_last_search_results = AsyncMock(return_value=[])

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
    model = store.append.await_args_list[1]
    assert model.kwargs["blocks"] == [{"type": "text", "content": "ответ"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_turn_attaches_kit_files(monkeypatch: pytest.MonkeyPatch) -> None:
    adk = AsyncMock()
    adk.run.return_value = (
        "карточка",
        [
            {
                "author": "root_agent",
                "actions": {
                    "stateDelta": {
                        "_bot_action": {"type": "send_product_kit", "product_code": "2832"}
                    }
                },
            }
        ],
    )
    monkeypatch.setattr(
        "bot.services.bot_actions.get_product_kit",
        lambda **kwargs: {
            "status": "ok",
            "message": "ok",
            "files": [{"path": "/kits/a.pdf", "name": "a.pdf", "size": 3}],
        },
    )

    class _Urls:
        def kit_url(self, user_id, path, name):
            return "/files/token"

        def kb_url(self, user_id, document_id, name):
            return "/files/kb"

    result = await run_turn(
        adk, global_user_id="user-uuid", text="комплект", file_urls=_Urls()
    )

    assert result.blocks[0]["content"] == "карточка"
    assert result.blocks[1]["items"][0]["url"] == "/files/token"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_run_turn_rejects_empty_text() -> None:
    adk = AsyncMock()
    with pytest.raises(ValueError, match="empty_message"):
        await run_turn(adk, global_user_id="u", text="  ")
    adk.run.assert_not_called()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_paginate_search_more_does_not_call_adk() -> None:
    store = AsyncMock()
    store.get_last_search_meta = AsyncMock(return_value={"shown_count": 1})
    store.get_last_search_results = AsyncMock(
        return_value=[
            {"rank": 1, "document_id": "d1", "source_name": "one.pdf", "source_path": "kb/a"},
            {"rank": 2, "document_id": "d2", "source_name": "two.pdf", "source_path": "kb/b"},
        ]
    )
    store.get_latest_search_session_id = AsyncMock(return_value=None)
    store.update_shown_count = AsyncMock()
    store.append = AsyncMock()

    class _Urls:
        def kit_url(self, user_id, path, name):
            return "/files/kit"

        def kb_url(self, user_id, document_id, name):
            return f"/files/{document_id}"

    result = await paginate_search(
        store,
        global_user_id="user-1",
        mode="more",
        file_urls=_Urls(),
    )

    docs = next(block for block in result.blocks if block["type"] == "documents")
    assert docs["items"][0]["name"] == "two.pdf"
    assert docs["has_more"] is False
    assert store.append.await_count == 2
