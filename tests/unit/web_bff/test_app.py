from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from web_bff.config import settings
from web_bff.otp import OtpError


class FakeOtp:
    def __init__(self) -> None:
        self.token = "session-token"
        self.sessions = {self.token: "user-1"}

    async def request(self, phone: str):
        return {"status": "ok", "dev_code": "123456"}

    async def verify(self, phone: str, code: str) -> str:
        if code != "123456":
            raise OtpError(401, "invalid_code")
        return self.token

    async def user_id_for_token(self, token: str):
        return self.sessions.get(token)

    async def revoke_token(self, token: str) -> None:
        self.sessions.pop(token, None)


def _client(monkeypatch: pytest.MonkeyPatch, *, allow_dev: bool, otp: FakeOtp | None = None):
    monkeypatch.setattr(settings, "allow_dev_auth", allow_dev)
    monkeypatch.setattr(settings, "cookie_name", "nastya_web")
    monkeypatch.setattr(settings, "cookie_secure", False)
    monkeypatch.setattr(settings, "session_ttl_sec", 3600)
    from web_bff.app import app

    fake_otp = otp or FakeOtp()

    async def fake_lifespan(_app):
        _app.state.otp = fake_otp
        _app.state.pool = object()
        yield

    app.router.lifespan_context = fake_lifespan
    client = TestClient(app)
    app.state.otp = fake_otp
    app.state.pool = object()
    return client, fake_otp


@pytest.mark.unit
def test_health_does_not_require_user(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql://aszh-bot:aszh-bot@localhost:5432/aszh-bot")
    monkeypatch.setattr(settings, "database_url", "postgresql://aszh-bot:aszh-bot@localhost:5432/aszh-bot")
    monkeypatch.setattr(settings, "allow_dev_auth", False)

    from web_bff.app import app

    async def fake_lifespan(_app):
        yield

    app.router.lifespan_context = fake_lifespan
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.unit
def test_me_unauthorized_without_cookie_or_dev_header(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, allow_dev=False)
    response = client.get("/me")
    assert response.status_code == 401
    assert response.json()["detail"] == "unauthorized"


@pytest.mark.unit
def test_me_dev_header_rejected_when_flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, allow_dev=False)
    response = client.get("/me", headers={"X-User-Id": "someone"})
    assert response.status_code == 401


@pytest.mark.unit
def test_otp_verify_sets_httponly_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, allow_dev=False)
    requested = client.post("/auth/otp/request", json={"phone": "+79161234567"})
    assert requested.status_code == 200
    assert requested.json()["dev_code"] == "123456"

    verified = client.post(
        "/auth/otp/verify",
        json={"phone": "+79161234567", "code": "123456"},
    )
    assert verified.status_code == 200
    cookie = verified.cookies.get("nastya_web")
    assert cookie == "session-token"
    set_cookie = verified.headers.get("set-cookie", "").lower()
    assert "httponly" in set_cookie
    assert "samesite=lax" in set_cookie


@pytest.mark.unit
def test_me_accepts_session_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, allow_dev=False)
    monkeypatch.setattr(
        "web_bff.auth.get_user_by_id",
        AsyncMock(
            return_value={
                "id": "user-1",
                "is_blocked": False,
                "first_name": "Ivan",
                "last_name": "",
                "phone_number": "+79161234567",
            }
        ),
    )
    response = client.get("/me", cookies={"nastya_web": "session-token"})
    assert response.status_code == 200
    assert response.json()["id"] == "user-1"
    assert response.json()["phone_masked"] == "***4567"


@pytest.mark.unit
def test_blocked_user_gets_403_on_dialog(monkeypatch: pytest.MonkeyPatch) -> None:
    client, _ = _client(monkeypatch, allow_dev=False)
    monkeypatch.setattr(
        "web_bff.auth.get_user_by_id",
        AsyncMock(
            return_value={
                "id": "user-1",
                "is_blocked": True,
                "first_name": "Ivan",
                "last_name": "",
                "phone_number": "+79161234567",
            }
        ),
    )
    response = client.get("/dialog", cookies={"nastya_web": "session-token"})
    assert response.status_code == 403
    assert response.json()["detail"] == "user_blocked"


@pytest.mark.unit
def test_logout_clears_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    client, otp = _client(monkeypatch, allow_dev=False)
    response = client.post("/auth/logout", cookies={"nastya_web": "session-token"})
    assert response.status_code == 200
    assert "session-token" not in otp.sessions
