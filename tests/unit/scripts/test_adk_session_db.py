import pytest

from scripts import adk_session_db


@pytest.mark.unit
def test_url_helpers_accept_sqlalchemy_asyncpg_scheme() -> None:
    database_url = "postgresql+asyncpg://adk:secret@postgres-write:5432/adk_sessions?sslmode=require"

    assert adk_session_db.database_name_from_url(database_url) == "adk_sessions"
    assert (
        adk_session_db.maintenance_database_url(database_url)
        == "postgresql://adk:secret@postgres-write:5432/postgres?sslmode=require"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_database_exists_creates_missing_database(monkeypatch) -> None:
    calls = []

    class FakeConnection:
        async def fetchval(self, query, database_name):
            calls.append(("fetchval", query, database_name))
            return None

        async def execute(self, query):
            calls.append(("execute", query))

        async def close(self):
            calls.append(("close",))

    async def fake_connect(url):
        calls.append(("connect", url))
        return FakeConnection()

    monkeypatch.setattr(adk_session_db.asyncpg, "connect", fake_connect)

    created = await adk_session_db.ensure_database_exists(
        "postgresql+asyncpg://adk:secret@postgres-write:5432/adk_sessions"
    )

    assert created is True
    assert calls == [
        ("connect", "postgresql://adk:secret@postgres-write:5432/postgres"),
        ("fetchval", "SELECT 1 FROM pg_database WHERE datname = $1", "adk_sessions"),
        ("execute", 'CREATE DATABASE "adk_sessions"'),
        ("close",),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prepare_database_clears_existing_database_only_with_reset(monkeypatch) -> None:
    calls = []

    async def fake_ensure_database_exists(database_url):
        calls.append(("ensure", database_url))
        return False

    async def fake_clear_public_schema(database_url):
        calls.append(("clear", database_url))

    monkeypatch.setattr(adk_session_db, "ensure_database_exists", fake_ensure_database_exists)
    monkeypatch.setattr(adk_session_db, "clear_public_schema", fake_clear_public_schema)

    await adk_session_db.prepare_database("postgresql://host/adk_sessions", reset_existing=False)
    await adk_session_db.prepare_database("postgresql://host/adk_sessions", reset_existing=True)

    assert calls == [
        ("ensure", "postgresql://host/adk_sessions"),
        ("ensure", "postgresql://host/adk_sessions"),
        ("clear", "postgresql://host/adk_sessions"),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_prepare_database_does_not_clear_new_database(monkeypatch) -> None:
    calls = []

    async def fake_ensure_database_exists(database_url):
        calls.append(("ensure", database_url))
        return True

    async def fake_clear_public_schema(database_url):
        calls.append(("clear", database_url))

    monkeypatch.setattr(adk_session_db, "ensure_database_exists", fake_ensure_database_exists)
    monkeypatch.setattr(adk_session_db, "clear_public_schema", fake_clear_public_schema)

    await adk_session_db.prepare_database("postgresql://host/adk_sessions", reset_existing=True)

    assert calls == [("ensure", "postgresql://host/adk_sessions")]
