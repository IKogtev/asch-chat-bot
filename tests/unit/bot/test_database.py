import ast
import json
import sys
import types
from datetime import datetime
from pathlib import Path
from typing import Optional
from uuid import uuid4

import pytest


def _load_symbols(file_path: Path, names: list[str], extra_globals: dict):
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace


def _logger():
    return types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )


_symbols = _load_symbols(
    Path(__file__).resolve().parents[3] / "bot" / "services" / "database.py",
    ["PostgresChatStore", "NewsStore", "AdkApiClient"],
    {
        "asyncpg": types.SimpleNamespace(Pool=object, Record=dict),
        "aiohttp": types.SimpleNamespace(ClientSession=object, ClientError=Exception),
        "json": json,
        "Optional": Optional,
        "uuid4": uuid4,
        "setup_logger": lambda *args, **kwargs: _logger(),
        "logger": _logger(),
        "Settings": types.SimpleNamespace(SHOW_MAX=5),
        "datetime": datetime,
    },
)

PostgresChatStore = _symbols["PostgresChatStore"]
NewsStore = _symbols["NewsStore"]
AdkApiClient = _symbols["AdkApiClient"]


class _AcquireCtx:
    def __init__(self, conn):
        self.conn = conn

    async def __aenter__(self):
        return self.conn

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _TransactionCtx:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(self, *, fetch_result=None, fetchrow_result=None):
        self.fetch_result = fetch_result or []
        self.fetchrow_result = fetchrow_result
        self.executed = []

    async def execute(self, query, *args):
        self.executed.append((query, args))
        return "OK"

    async def fetch(self, query, *args):
        self.executed.append((query, args))
        return self.fetch_result

    async def fetchrow(self, query, *args):
        self.executed.append((query, args))
        return self.fetchrow_result

    def transaction(self):
        return _TransactionCtx()


class _FakePool:
    def __init__(self, conn):
        self.conn = conn
        self.closed = False

    def acquire(self):
        return _AcquireCtx(self.conn)

    async def close(self):
        self.closed = True


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_chat_store_get_history_returns_empty_without_pool() -> None:
    store = PostgresChatStore("postgres://dsn")

    result = await store.get_history(1)

    assert result == []


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_chat_store_save_search_results_uses_min_shown_count(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = {}

    async def fake_save_doc_search_results(pool, user_id, session_id, query, items, shown):
        captured.update(
            {
                "pool": pool,
                "user_id": user_id,
                "session_id": session_id,
                "query": query,
                "items": items,
                "shown": shown,
            }
        )
        return "search-id"

    sys.modules["utils.search_results_db"] = types.SimpleNamespace(
        save_doc_search_results=fake_save_doc_search_results
    )

    store = PostgresChatStore("postgres://dsn")
    store.pool = object()

    result = await store.save_search_results(10, "session-1", "query", [{"id": 1}], shown_count=5)

    assert result == "search-id"
    assert captured["shown"] == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_chat_store_get_last_search_meta_returns_dict() -> None:
    conn = _FakeConn(fetchrow_result={"search_id": "abc", "shown_count": 2})
    store = PostgresChatStore("postgres://dsn")
    store.pool = _FakePool(conn)

    result = await store.get_last_search_meta(1, "s1")

    assert result == {"search_id": "abc", "shown_count": 2}


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_chat_store_get_latest_search_session_id_returns_value() -> None:
    conn = _FakeConn(fetchrow_result={"session_id": "user_turn-abc"})
    store = PostgresChatStore("postgres://dsn")
    store.pool = _FakePool(conn)

    result = await store.get_latest_search_session_id("user-uuid")

    assert result == "user_turn-abc"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_chat_store_get_latest_search_session_id_returns_none_without_pool() -> None:
    store = PostgresChatStore("postgres://dsn")
    store.pool = None

    assert await store.get_latest_search_session_id("user-uuid") is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_postgres_chat_store_reset_search_state_executes_two_deletes() -> None:
    conn = _FakeConn()
    store = PostgresChatStore("postgres://dsn")
    store.pool = _FakePool(conn)

    await store.reset_search_state(1, "s1")

    assert len(conn.executed) == 2
    assert "DELETE FROM search_results" in conn.executed[0][0]
    assert "DELETE FROM search_meta" in conn.executed[1][0]


@pytest.mark.unit
def test_news_store_parse_news_row_parses_files_json_and_sets_default_group() -> None:
    store = NewsStore(pool=None)

    result = store._parse_news_row({"id": 1, "files": '[{"path":"a"}]'})

    assert result["files"] == [{"path": "a"}]
    assert result["target_group"] == "all"


@pytest.mark.unit
def test_news_store_parse_news_row_handles_invalid_files_json() -> None:
    store = NewsStore(pool=None)

    result = store._parse_news_row({"id": 1, "files": "{bad-json}"})

    assert result["files"] == []


@pytest.mark.unit
def test_adk_api_client_extract_model_text_returns_final_root_agent_text() -> None:
    events = [
        {"author": "dispatcher_agent", "actions": {"end_of_agent": True}, "content": {"parts": [{"text": "skip"}]}},
        {
            "author": "root_agent",
            "actions": {"end_of_agent": True},
            "content": {"parts": [{"thought": True, "text": "hidden"}, {"text": "Финальный ответ"}]},
        },
    ]

    result = AdkApiClient._extract_model_text(events)

    assert result == "Финальный ответ"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_adk_api_client_set_user_state_normalizes_and_merges_values() -> None:
    client = AdkApiClient("http://adk", "app")
    client._pending_state_delta = {("1", "session-1"): {"region": "Старый"}}

    await client.set_user_state(
        user_id="1",
        session_id="session-1",
        user_data={
            "first_name": "Иван",
            "last_name": "Иванов",
            "username": "ivanov",
            "region": "Москва",
            "manager_group": 1,
            "coach_group": 0,
        },
    )

    assert client._pending_state_delta[("1", "session-1")] == {
        "region": "Москва",
        "first_name": "Иван",
        "last_name": "Иванов",
        "full_name": "Иван Иванов",
        "username": "ivanov",
        "manager_group": True,
        "coach_group": False,
    }
