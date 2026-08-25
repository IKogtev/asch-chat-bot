import pytest
from fastapi.testclient import TestClient

from web_bff.auth_dev import require_dev_user
from web_bff.config import settings


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
def test_me_requires_dev_auth_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_dev_auth", False)
    from web_bff.app import app

    async def fake_lifespan(_app):
        yield

    app.router.lifespan_context = fake_lifespan
    client = TestClient(app)
    response = client.get("/me", headers={"X-User-Id": "someone"})
    assert response.status_code == 401
    assert response.json()["detail"] == "dev_auth_disabled"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_require_dev_user_reads_header(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "allow_dev_auth", True)
    assert await require_dev_user(x_user_id="abc") == "abc"
