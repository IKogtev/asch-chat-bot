import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_tables_loader_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "mcps" / "kb-manager" / "app" / "services" / "tables_loader_service.py"

    asyncpg_stub = types.ModuleType("asyncpg")
    asyncpg_stub.Connection = type("Connection", (), {})

    pandas_stub = types.ModuleType("pandas")
    pandas_stub.Index = list
    pandas_stub.Timestamp = type("Timestamp", (), {})
    pandas_stub.ExcelFile = object
    pandas_stub.read_excel = lambda *args, **kwargs: None
    pandas_stub.isna = lambda value: value is None
    pandas_stub.api = types.SimpleNamespace(
        types=types.SimpleNamespace(
            is_integer_dtype=lambda dtype: False,
            is_float_dtype=lambda dtype: False,
            is_bool_dtype=lambda dtype: False,
            is_datetime64_any_dtype=lambda dtype: False,
            is_object_dtype=lambda dtype: True,
        )
    )

    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_stub)
    monkeypatch.setitem(sys.modules, "pandas", pandas_stub)

    spec = importlib.util.spec_from_file_location("tables_loader_service_under_test", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    monkeypatch.setitem(sys.modules, "tables_loader_service_under_test", module)
    spec.loader.exec_module(module)
    return module


@pytest.mark.unit
def test_database_url_helpers_use_postgres_maintenance_database(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    service = module.TablesLoaderService(
        "postgresql://aszh-bot:secret@postgres:5432/nstya_data?sslmode=disable",
        ".",
    )

    assert service._database_name_from_url(service.database_url) == "nstya_data"
    assert (
        service._maintenance_database_url(service.database_url)
        == "postgresql://aszh-bot:secret@postgres:5432/postgres?sslmode=disable"
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_database_exists_creates_missing_database(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
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

    module.asyncpg.connect = fake_connect
    service = module.TablesLoaderService(
        "postgresql://aszh-bot:secret@postgres:5432/nstya_data",
        ".",
    )

    await service.ensure_database_exists()

    assert calls == [
        ("connect", "postgresql://aszh-bot:secret@postgres:5432/postgres"),
        ("fetchval", "SELECT 1 FROM pg_database WHERE datname = $1", "nstya_data"),
        ("execute", 'CREATE DATABASE "nstya_data"'),
        ("close",),
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_ensure_database_exists_skips_create_when_database_exists(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    calls = []

    class FakeConnection:
        async def fetchval(self, query, database_name):
            calls.append(("fetchval", database_name))
            return 1

        async def execute(self, query):
            calls.append(("execute", query))

        async def close(self):
            calls.append(("close",))

    async def fake_connect(url):
        calls.append(("connect", url))
        return FakeConnection()

    module.asyncpg.connect = fake_connect
    service = module.TablesLoaderService(
        "postgresql://aszh-bot:secret@postgres:5432/nstya_data",
        ".",
    )

    await service.ensure_database_exists()

    assert calls == [
        ("connect", "postgresql://aszh-bot:secret@postgres:5432/postgres"),
        ("fetchval", "nstya_data"),
        ("close",),
    ]
