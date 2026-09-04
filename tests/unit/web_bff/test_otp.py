from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from fastapi import HTTPException

from web_bff.auth import require_user
from web_bff.config import settings
from web_bff.otp import OtpError, OtpService, hash_secret, hash_session_token
from web_bff.phones import normalize_phone


class _Acquire:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, *args):
        return False


class FakeConn:
    def __init__(self, *, fetchval=0, fetchrow=None):
        self.fetchval_result = fetchval
        self.fetchrow_result = fetchrow
        self.executed: list[tuple] = []

    def transaction(self):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def fetchval(self, query, *args):
        self.executed.append(("fetchval", query, args))
        return self.fetchval_result

    async def fetchrow(self, query, *args):
        self.executed.append(("fetchrow", query, args))
        return self.fetchrow_result

    async def execute(self, query, *args):
        self.executed.append(("execute", query, args))

    async def fetch(self, query, *args):
        self.executed.append(("fetch", query, args))
        return []


class FakePool:
    def __init__(self, conn: FakeConn):
        self.conn = conn

    def acquire(self):
        return _Acquire(self.conn)


def _settings(**overrides):
    data = dict(
        otp_secret="test-secret",
        otp_stub=True,
        otp_stub_phone="+79161234567",
        otp_stub_code="654321",
        otp_ttl_sec=600,
        otp_max_attempts=5,
        otp_max_requests=5,
        otp_request_window_sec=900,
        session_ttl_sec=3600,
        otp_internal_secret="internal-secret",
        bot_telegram_api="http://bot:8001",
        bot_max_api="http://bot-max:8002",
        cookie_name="nastya_web",
        cookie_secure=False,
        allow_dev_auth=False,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.unit
def test_normalize_phone_8_to_plus7() -> None:
    assert normalize_phone("8 (916) 123-45-67") == "+79161234567"


@pytest.mark.unit
def test_hash_secret_differs_by_pepper() -> None:
    assert hash_secret("a", "111111") != hash_secret("b", "111111")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_otp_request_unknown_phone_same_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(fetchval=0)
    service = OtpService(FakePool(conn), _settings())
    monkeypatch.setattr("web_bff.otp.get_user_by_phone", AsyncMock(return_value=None))

    result = await service.request("+79161234567")

    assert result == {"status": "ok"}
    assert "dev_code" not in result
    inserts = [item for item in conn.executed if item[0] == "execute" and "INSERT" in item[1]]
    assert inserts
    assert inserts[0][2][5] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_otp_request_stub_returns_code(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(fetchval=0)
    service = OtpService(FakePool(conn), _settings(otp_stub=True))
    monkeypatch.setattr(
        "web_bff.otp.get_user_by_phone",
        AsyncMock(return_value={"id": "user-1", "is_blocked": False}),
    )
    monkeypatch.setattr("web_bff.otp.generate_otp_code", lambda: "000000")

    result = await service.request("79161234567")

    assert result == {"status": "ok", "dev_code": "654321"}
    inserts = [item for item in conn.executed if item[0] == "execute" and "INSERT" in item[1]]
    assert inserts[0][2][2] == hash_secret("test-secret", "654321")
    assert inserts[0][2][5] is True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_otp_stub_ignores_other_known_phone(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(fetchval=0)
    service = OtpService(FakePool(conn), _settings())
    monkeypatch.setattr(
        "web_bff.otp.get_user_by_phone",
        AsyncMock(return_value={"id": "user-2", "is_blocked": False}),
    )

    result = await service.request("+79001112233")

    assert result == {"status": "ok"}
    assert "dev_code" not in result
    inserts = [item for item in conn.executed if item[0] == "execute" and "INSERT" in item[1]]
    assert inserts[0][2][5] is False


@pytest.mark.unit
@pytest.mark.asyncio
async def test_otp_request_blocked_no_code(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(fetchval=0)
    service = OtpService(FakePool(conn), _settings())
    monkeypatch.setattr(
        "web_bff.otp.get_user_by_phone",
        AsyncMock(return_value={"id": "user-1", "is_blocked": True}),
    )

    result = await service.request("+79161234567")

    assert result == {"status": "ok"}
    assert "dev_code" not in result


@pytest.mark.unit
@pytest.mark.asyncio
async def test_otp_request_delivers_to_messengers(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(fetchval=0)
    service = OtpService(FakePool(conn), _settings(otp_stub=False))
    monkeypatch.setattr(
        "web_bff.otp.get_user_by_phone",
        AsyncMock(return_value={"id": "user-1", "is_blocked": False}),
    )
    monkeypatch.setattr(
        "web_bff.otp.get_messenger_accounts",
        AsyncMock(return_value=[{"platform": "telegram", "platform_user_id": 99}]),
    )
    monkeypatch.setattr("web_bff.otp.generate_otp_code", lambda: "111111")
    deliver = AsyncMock(return_value=1)
    monkeypatch.setattr("web_bff.otp.deliver_otp_code", deliver)

    result = await service.request("+79161234567")

    assert result == {"status": "ok"}
    deliver.assert_awaited_once()
    assert deliver.await_args.args[2] == "111111"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_otp_request_requires_messenger(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(fetchval=0)
    service = OtpService(FakePool(conn), _settings(otp_stub=False))
    monkeypatch.setattr(
        "web_bff.otp.get_user_by_phone",
        AsyncMock(return_value={"id": "user-1", "is_blocked": False}),
    )
    monkeypatch.setattr("web_bff.otp.get_messenger_accounts", AsyncMock(return_value=[]))

    with pytest.raises(OtpError) as exc:
        await service.request("+79161234567")
    assert exc.value.detail == "messenger_required"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_otp_verify_wrong_code(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(
        fetchrow={
            "id": "ch-1",
            "user_id": "user-1",
            "code_hash": hash_secret("test-secret", "111111"),
            "attempts": 0,
            "max_attempts": 5,
        }
    )
    service = OtpService(FakePool(conn), _settings())

    with pytest.raises(OtpError) as exc:
        await service.verify("+79161234567", "000000")
    assert exc.value.detail == "invalid_code"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_otp_verify_success_creates_session(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = FakeConn(
        fetchrow={
            "id": "ch-1",
            "user_id": "user-1",
            "code_hash": hash_secret("test-secret", "111111"),
            "attempts": 0,
            "max_attempts": 5,
        }
    )
    service = OtpService(FakePool(conn), _settings())
    monkeypatch.setattr("web_bff.otp.generate_session_token", lambda: "tok-abc")

    token = await service.verify("+79161234567", "111111")

    assert token == "tok-abc"
    session_insert = [
        item
        for item in conn.executed
        if item[0] == "execute" and "INSERT INTO web_sessions" in item[1]
    ]
    assert session_insert
    assert session_insert[0][2][1] == hash_session_token("tok-abc")


_ACTIVE_USER = {
    "id": "from-cookie",
    "is_blocked": False,
    "first_name": "Ivan",
    "last_name": "",
    "phone_number": "+79161234567",
}


def _request(otp, cookie: str | None = None) -> Request:
    headers = []
    if cookie:
        headers.append((b"cookie", f"nastya_web={cookie}".encode()))
    return Request(
        {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/me",
            "raw_path": b"/me",
            "query_string": b"",
            "headers": headers,
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
            "app": SimpleNamespace(state=SimpleNamespace(otp=otp, pool=object())),
        }
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_require_user_prefers_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_dev_auth", True)
    monkeypatch.setattr(settings, "cookie_name", "nastya_web")
    monkeypatch.setattr(
        "web_bff.auth.get_user_by_id",
        AsyncMock(return_value={**_ACTIVE_USER, "id": "from-cookie"}),
    )
    otp = SimpleNamespace(user_id_for_token=AsyncMock(return_value="from-cookie"))
    request = _request(otp, "sess")
    assert await require_user(request, x_user_id="from-header") == "from-cookie"
    assert request.state.user["id"] == "from-cookie"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_require_user_falls_back_to_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_dev_auth", True)
    monkeypatch.setattr(settings, "cookie_name", "nastya_web")
    monkeypatch.setattr(
        "web_bff.auth.get_user_by_id",
        AsyncMock(return_value={**_ACTIVE_USER, "id": "from-header"}),
    )
    otp = SimpleNamespace(user_id_for_token=AsyncMock(return_value=None))
    assert await require_user(_request(otp), x_user_id="from-header") == "from-header"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_require_user_rejects_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_dev_auth", False)
    monkeypatch.setattr(settings, "cookie_name", "nastya_web")
    monkeypatch.setattr(
        "web_bff.auth.get_user_by_id",
        AsyncMock(return_value={**_ACTIVE_USER, "is_blocked": True}),
    )
    otp = SimpleNamespace(user_id_for_token=AsyncMock(return_value="from-cookie"))
    with pytest.raises(HTTPException) as exc:
        await require_user(_request(otp, "sess"), x_user_id=None)
    assert exc.value.status_code == 403
    assert exc.value.detail == "user_blocked"
