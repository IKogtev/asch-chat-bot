from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from web_bff.config import settings
from web_bff.news import to_news_detail, to_news_list_item


class FakeOtp:
    def __init__(self) -> None:
        self.token = "session-token"
        self.sessions = {self.token: "user-1"}

    async def user_id_for_token(self, token: str):
        return self.sessions.get(token)


class FakeNewsStore:
    def __init__(self, rows: list[dict], groups: dict[str, tuple[bool, bool]] | None = None) -> None:
        self.rows = rows
        self.groups = groups or {}
        self.list_calls: list[tuple[str, int, int]] = []
        self.by_id_calls: list[tuple[str, int]] = []

    def _visible(self, user_id: str, row: dict) -> bool:
        if row.get("status") != "sent":
            return False
        target = row.get("target_group")
        is_manager, is_coach = self.groups.get(user_id, (False, False))
        if target in (None, "all"):
            return True
        if target == "manager_group":
            return is_manager
        if target == "coach_group":
            return is_coach
        return False

    async def get_published_for_web(self, global_user_id: str, *, limit: int, offset: int) -> list[dict]:
        self.list_calls.append((global_user_id, limit, offset))
        visible = [row for row in self.rows if self._visible(global_user_id, row)]
        visible.sort(
            key=lambda row: row.get("scheduled_at") or row.get("created_at"),
            reverse=True,
        )
        return visible[offset : offset + limit + 1]

    async def get_published_for_web_by_id(self, global_user_id: str, news_id: int) -> dict | None:
        self.by_id_calls.append((global_user_id, news_id))
        for row in self.rows:
            if row["id"] == news_id and self._visible(global_user_id, row):
                return row
        return None


def _client(
    monkeypatch: pytest.MonkeyPatch,
    *,
    news_store: FakeNewsStore,
    user: dict | None = None,
    user_id: str = "user-1",
):
    monkeypatch.setattr(settings, "allow_dev_auth", False)
    monkeypatch.setattr(settings, "cookie_name", "nastya_web")
    from web_bff.app import app

    otp = FakeOtp()
    otp.sessions[otp.token] = user_id

    async def fake_lifespan(_app):
        _app.state.otp = otp
        _app.state.pool = object()
        _app.state.news_store = news_store
        yield

    app.router.lifespan_context = fake_lifespan
    client = TestClient(app)
    app.state.otp = otp
    app.state.pool = object()
    app.state.news_store = news_store
    if user is None:
        user = {
            "id": user_id,
            "is_blocked": False,
            "first_name": "Ivan",
            "last_name": "",
            "phone_number": "+79161234567",
        }
    monkeypatch.setattr("web_bff.auth.get_user_by_id", AsyncMock(return_value=user))
    return client


def _row(
    news_id: int,
    *,
    text: str = "Заголовок",
    status: str = "sent",
    target_group: str | None = "all",
    created_at: datetime | None = None,
    scheduled_at: datetime | None = None,
    files=None,
) -> dict:
    return {
        "id": news_id,
        "text": text,
        "status": status,
        "target_group": target_group,
        "created_at": created_at or datetime(2026, 8, 27, 12, 30, tzinfo=timezone.utc),
        "scheduled_at": scheduled_at,
        "files": files or [],
    }


@pytest.mark.unit
def test_title_strips_html_collapses_spaces_and_truncates() -> None:
    item = to_news_list_item(
        _row(
            1,
            text="<p>  Обновлены   условия <b>страхования</b>  </p>\nВторой абзац",
        )
    )
    assert item.title == "Обновлены условия страхования"
    long_title = "ж" * 200
    assert len(to_news_list_item(_row(2, text=long_title)).title) == 120


@pytest.mark.unit
def test_detail_keeps_full_content_and_safe_file_names() -> None:
    detail = to_news_detail(
        _row(
            1,
            text="<p>Полный текст</p>",
            files=[
                {"path": "/var/news/secret/file.pdf", "name": "Условия.pdf"},
                {"path": "/etc/passwd"},
            ],
        )
    )
    assert detail.content == "<p>Полный текст</p>"
    assert [item.name for item in detail.files] == ["Условия.pdf", "passwd"]
    dumped = detail.model_dump()
    assert "path" not in dumped["files"][0]


@pytest.mark.unit
def test_news_unauthorized_without_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, news_store=FakeNewsStore([]))
    response = client.get("/news")
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


@pytest.mark.unit
def test_news_blocked_user_gets_403(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(
        monkeypatch,
        news_store=FakeNewsStore([]),
        user={
            "id": "user-1",
            "is_blocked": True,
            "first_name": "Ivan",
            "last_name": "",
            "phone_number": "+79161234567",
        },
    )
    response = client.get("/news", cookies={"nastya_web": "session-token"})
    assert response.status_code == 403
    assert response.json()["detail"] == "user_blocked"


@pytest.mark.unit
def test_news_list_returns_only_sent_and_all_for_any_user(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeNewsStore(
        [
            _row(1, text="Sent all", status="sent", target_group="all"),
            _row(2, text="Pending", status="pending", target_group="all"),
        ],
        groups={"user-1": (False, False)},
    )
    client = _client(monkeypatch, news_store=store)
    response = client.get("/news", cookies={"nastya_web": "session-token"})
    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body["items"]] == [1]
    assert body["has_more"] is False
    assert body["limit"] == 20
    assert body["offset"] == 0


@pytest.mark.unit
def test_news_list_respects_manager_and_coach_groups(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        _row(1, text="Managers", target_group="manager_group"),
        _row(2, text="Coaches", target_group="coach_group"),
        _row(3, text="Unknown", target_group="vip"),
    ]
    store = FakeNewsStore(rows, groups={"user-1": (True, False)})
    client = _client(monkeypatch, news_store=store)
    cookies = {"nastya_web": "session-token"}

    assert [item["id"] for item in client.get("/news", cookies=cookies).json()["items"]] == [1]

    store.groups["user-1"] = (False, True)
    assert [item["id"] for item in client.get("/news", cookies=cookies).json()["items"]] == [2]

    store.groups["user-1"] = (False, False)
    assert client.get("/news", cookies=cookies).json()["items"] == []


@pytest.mark.unit
def test_merged_account_groups_see_both_targeted_news(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeNewsStore(
        [
            _row(1, text="Managers", target_group="manager_group"),
            _row(2, text="Coaches", target_group="coach_group"),
        ],
        groups={"user-1": (True, True)},
    )
    client = _client(monkeypatch, news_store=store)
    ids = [item["id"] for item in client.get("/news", cookies={"nastya_web": "session-token"}).json()["items"]]
    assert ids == [1, 2]


@pytest.mark.unit
def test_group_news_hidden_on_direct_get(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeNewsStore(
        [_row(9, text="Managers", target_group="manager_group")],
        groups={"user-1": (False, False)},
    )
    client = _client(monkeypatch, news_store=store)
    response = client.get("/news/9", cookies={"nastya_web": "session-token"})
    assert response.status_code == 404
    assert response.json()["detail"] == "news_not_found"


@pytest.mark.unit
def test_unknown_news_id_returns_404(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, news_store=FakeNewsStore([]))
    response = client.get("/news/404", cookies={"nastya_web": "session-token"})
    assert response.status_code == 404
    assert response.json()["detail"] == "news_not_found"


@pytest.mark.unit
def test_news_sorted_newest_first_and_has_more(monkeypatch: pytest.MonkeyPatch) -> None:
    older = datetime(2026, 1, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 8, 1, tzinfo=timezone.utc)
    store = FakeNewsStore(
        [
            _row(1, text="Old", created_at=older),
            _row(2, text="New", created_at=newer),
            _row(3, text="Mid", created_at=datetime(2026, 4, 1, tzinfo=timezone.utc)),
        ]
    )
    client = _client(monkeypatch, news_store=store)
    response = client.get("/news", params={"limit": 2, "offset": 0}, cookies={"nastya_web": "session-token"})
    body = response.json()
    assert [item["id"] for item in body["items"]] == [2, 3]
    assert body["has_more"] is True
    assert body["limit"] == 2


@pytest.mark.unit
def test_news_limit_capped_at_100(monkeypatch: pytest.MonkeyPatch) -> None:
    client = _client(monkeypatch, news_store=FakeNewsStore([]))
    response = client.get("/news", params={"limit": 101}, cookies={"nastya_web": "session-token"})
    assert response.status_code == 422


@pytest.mark.unit
def test_news_detail_success(monkeypatch: pytest.MonkeyPatch) -> None:
    store = FakeNewsStore(
        [
            _row(
                12,
                text="Обновлены условия\nПолный текст новости",
                files=[{"name": "Условия страхования.pdf", "path": "/secret/x.pdf"}],
            )
        ]
    )
    client = _client(monkeypatch, news_store=store)
    response = client.get("/news/12", cookies={"nastya_web": "session-token"})
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == 12
    assert body["title"] == "Обновлены условия"
    assert body["content"] == "Обновлены условия\nПолный текст новости"
    assert body["files"] == [{"name": "Условия страхования.pdf"}]
    assert body["published_at"].endswith("Z")
    assert "path" not in body["files"][0]
