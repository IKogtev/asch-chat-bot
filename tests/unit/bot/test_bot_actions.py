from unittest.mock import AsyncMock

import pytest

from bot.services.bot_actions import ARCHIVE_KIT_NOTICE, NO_SAVED_LIST, apply_bot_action


class _Store:
    def __init__(self):
        self.meta = None
        self.items = []
        self.updated = None

    async def get_last_search_meta(self, user_id, session_id):
        return self.meta

    async def get_last_search_results(self, user_id, session_id):
        return list(self.items)

    async def get_latest_search_session_id(self, user_id, channel=None):
        return None

    async def get_result_by_rank(self, user_id, session_id, rank):
        for item in self.items:
            if item.get("rank") == rank:
                return item
        return None

    async def update_shown_count(self, user_id, session_id, shown_count):
        self.updated = shown_count


class _Urls:
    def kit_url(self, user_id, path, name):
        return f"/files/kit-{name}"

    def kb_url(self, user_id, document_id, name):
        return f"/files/kb-{document_id}"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_kit_keeps_answer_and_adds_file_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "bot.services.bot_actions.get_product_kit",
        lambda **kwargs: {
            "status": "ok",
            "message": "Комплект продукта найден.",
            "files": [{"path": "/kits/a.pdf", "name": "a.pdf", "size": 12}],
        },
    )
    delivery = await apply_bot_action(
        None,
        user_id="user-1",
        session_id="user-1::web::t",
        answer="Карточка продукта",
        bot_action={"type": "send_product_kit", "product_code": "2832"},
        file_urls=_Urls(),
    )
    assert delivery.replace_answer is False
    assert "Карточка продукта" in delivery.text
    assert delivery.documents == [{"name": "a.pdf", "url": "/files/kit-a.pdf", "size": 12}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_kit_archive_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []

    def fake_kit(**kwargs):
        calls.append(kwargs.get("folder_kit_root"))
        if kwargs.get("folder_kit_root") == "archive":
            return {
                "status": "ok",
                "message": "ok",
                "files": [{"path": "/arch/a.pdf", "name": "a.pdf", "size": 1}],
            }
        return {"status": "not_found", "message": "нет", "files": []}

    monkeypatch.setattr("bot.services.bot_actions.get_product_kit", fake_kit)
    delivery = await apply_bot_action(
        None,
        user_id="u",
        session_id="s",
        answer="текст",
        bot_action={"type": "send_product_kit", "product_code": "1"},
        file_urls=_Urls(),
    )
    assert calls == ["", "archive"]
    assert ARCHIVE_KIT_NOTICE in delivery.text
    assert delivery.documents[0]["url"] == "/files/kit-a.pdf"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_download_by_ranks() -> None:
    store = _Store()
    store.items = [
        {"rank": 2, "document_id": "doc_abc", "source_name": "условия.pdf"},
    ]
    delivery = await apply_bot_action(
        store,
        user_id="u",
        session_id="u::web::t",
        answer="ignored",
        bot_action={"type": "download_by_ranks", "ranks": [2, 9]},
        file_urls=_Urls(),
    )
    assert delivery.replace_answer is True
    assert "№9" in delivery.text
    assert delivery.documents == [{"name": "условия.pdf", "url": "/files/kb-doc_abc"}]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_show_more_advances_shown_count() -> None:
    store = _Store()
    store.meta = {"shown_count": 1}
    store.items = [
        {"rank": 1, "document_id": "doc_1", "source_name": "one.pdf", "source_path": "kb/one.pdf"},
        {"rank": 2, "document_id": "doc_2", "source_name": "two.pdf", "source_path": "kb/two.pdf"},
    ]
    delivery = await apply_bot_action(
        store,
        user_id="u",
        session_id="u::web::t",
        answer="",
        bot_action={"type": "show_doc_list_more"},
        file_urls=_Urls(),
        page_size=1,
    )
    assert delivery.replace_answer is True
    assert delivery.documents == [
        {"name": "two.pdf", "url": "/files/kb-doc_2", "rank": 2}
    ]
    assert "ещё" not in delivery.text
    assert store.updated == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_new_search_list_without_bot_action() -> None:
    store = _Store()
    store.meta = {"shown_count": 1, "search_id": "s1"}
    store.items = [
        {
            "rank": 1,
            "document_id": "doc_found",
            "source_name": "found.pdf",
            "source_path": "kb/found.pdf",
        }
    ]
    delivery = await apply_bot_action(
        store,
        user_id="u",
        session_id="u::web::t",
        answer="агент что-то сказал",
        bot_action=None,
        file_urls=_Urls(),
    )
    assert delivery.replace_answer is True
    assert delivery.documents[0]["url"] == "/files/kb-doc_found"
    assert delivery.documents[0]["name"] == "found.pdf"
    assert "напишите номер" not in delivery.text.lower()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_apply_show_all_without_list() -> None:
    delivery = await apply_bot_action(
        _Store(),
        user_id="u",
        session_id="u::web::t",
        answer="",
        bot_action={"type": "show_doc_list_all"},
    )
    assert delivery.text == NO_SAVED_LIST
