import importlib.util
import sys
import types
from datetime import date
from pathlib import Path

import pytest


def _load_tables_loader_module(monkeypatch):
    repo_root = Path(__file__).resolve().parents[3]
    services_dir = repo_root / "mcps" / "kb-manager" / "app" / "services"
    module_path = services_dir / "tables_loader_service.py"

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

    app_pkg = types.ModuleType("app")
    app_pkg.__path__ = [str(repo_root / "mcps" / "kb-manager" / "app")]
    services_pkg = types.ModuleType("app.services")
    services_pkg.__path__ = [str(services_dir)]

    monkeypatch.setitem(sys.modules, "asyncpg", asyncpg_stub)
    monkeypatch.setitem(sys.modules, "pandas", pandas_stub)
    monkeypatch.setitem(sys.modules, "app", app_pkg)
    monkeypatch.setitem(sys.modules, "app.services", services_pkg)

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


@pytest.mark.unit
def test_enrich_products_with_kit_folders_rebuilds_columns(monkeypatch, tmp_path) -> None:
    module = _load_tables_loader_module(monkeypatch)
    (tmp_path / "Fort Knox (2832)").mkdir()
    monkeypatch.setenv("PRODUCT_KITS_ROOT", str(tmp_path))

    class FakeAt:
        def __init__(self, frame):
            self.frame = frame

        def __setitem__(self, key, value):
            idx, column = key
            self.frame.rows[idx][column] = value

    class FakeDataFrame:
        def __init__(self, rows):
            self.rows = rows
            self.columns = list(rows[0].keys())
            self.at = FakeAt(self)

        def copy(self):
            return FakeDataFrame([row.copy() for row in self.rows])

        def __len__(self):
            return len(self.rows)

        def __setitem__(self, column, value):
            if column not in self.columns:
                self.columns.append(column)
            for row in self.rows:
                row[column] = value

        def iterrows(self):
            for idx, row in enumerate(self.rows):
                yield idx, row

    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    df = FakeDataFrame([{"code": "2832", "name": "Fort Knox", "folder_kit": "old"}])

    result = service._enrich_products_with_kit_folders(df)

    assert result.rows[0]["folder_kit"] == "Fort Knox (2832)"
    assert "Fort Knox (2832)" in result.rows[0]["folder_kit_status"]
    assert service.product_kit_folders_found == 1
    assert service.product_kit_products_total == 1
    assert service.product_input_dates_from_table == 0
    assert service.product_input_dates_from_kits == 0
    assert service.product_input_dates_missing == 1


@pytest.mark.unit
def test_glossary_rows_accept_russian_and_english_columns(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)

    class FakeDataFrame:
        columns = ["сокращение", "определение", "синонимы", "category"]

        def iterrows(self):
            yield 0, {
                "сокращение": "НСЖ",
                "определение": "накопительное страхование жизни",
                "синонимы": "накопительное страхование; life",
                "category": "products",
            }
            yield 1, {
                "сокращение": "",
                "определение": "empty term",
                "синонимы": "",
                "category": "",
            }

    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")

    rows = service._glossary_rows_from_dataframe(FakeDataFrame())

    assert rows == [
        {
            "term": "НСЖ",
            "definition": "накопительное страхование жизни",
            "aliases": "накопительное страхование; life",
            "category": "products",
            "term_normalized": "нсж",
            "aliases_normalized": "накопительное страхование;life",
        }
    ]


@pytest.mark.unit
def test_glossary_rows_skip_when_required_columns_missing(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)

    class FakeDataFrame:
        columns = ["term", "comment"]

        def iterrows(self):
            yield 0, {"term": "НСЖ", "comment": "missing definition"}

    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")

    assert service._glossary_rows_from_dataframe(FakeDataFrame()) == []


@pytest.mark.unit
def test_deduplicate_glossary_rows_keeps_distinct_definitions(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    rows = [
        {"term_normalized": "нсж", "definition": "one", "term": "НСЖ"},
        {"term_normalized": "нсж", "definition": "one", "term": "НСЖ"},
        {"term_normalized": "нсж", "definition": "two", "term": "НСЖ"},
    ]

    assert module.TablesLoaderService._deduplicate_glossary_rows(rows) == [
        {"term_normalized": "нсж", "definition": "one", "term": "НСЖ"},
        {"term_normalized": "нсж", "definition": "two", "term": "НСЖ"},
    ]


@pytest.mark.unit
def test_read_glossary_files_uses_only_active_file(monkeypatch, tmp_path) -> None:
    module = _load_tables_loader_module(monkeypatch)
    glossary_dir = tmp_path / "glossary"
    glossary_dir.mkdir()
    active_file = glossary_dir / "glossary_active.xlsx"
    active_file.write_bytes(b"stub")
    (glossary_dir / "ignored.xlsx").write_bytes(b"stub")
    excel_files = []

    class FakeWorkbook:
        sheet_names = ["Лист1"]

        def __init__(self, path, engine=None):
            excel_files.append((path.name, engine))

    module.pd.ExcelFile = FakeWorkbook
    module.pd.DataFrame = lambda rows, columns: {"rows": rows, "columns": columns}

    service = module.TablesLoaderService(
        "postgresql://u:p@host:5432/db",
        ".",
        glossary_dir=glossary_dir,
    )
    service._read_glossary_sheet = lambda file_path, sheet_name: "fake_df"
    service._glossary_rows_from_dataframe = lambda df: [
        {
            "term": "НСЖ",
            "definition": "накопительное страхование жизни",
            "aliases": "",
            "category": "сокращение",
            "term_normalized": "нсж",
            "aliases_normalized": "",
        }
    ]

    result = service._read_glossary_files()

    assert excel_files == [("glossary_active.xlsx", "openpyxl")]
    assert result["rows"][0]["term"] == "НСЖ"


@pytest.mark.unit
def test_read_glossary_sheet_skips_second_description_row(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)

    class FakeIloc:
        def __init__(self, frame):
            self.frame = frame

        def __getitem__(self, item):
            assert item == slice(1, None, None)
            return FakeDataFrame(self.frame.rows[1:])

    class FakeDataFrame:
        def __init__(self, rows):
            self.rows = rows
            self.empty = not rows
            self.iloc = FakeIloc(self)

        def reset_index(self, drop=False):
            assert drop is True
            return self

    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    service._read_sheet = lambda file_path, sheet_name: FakeDataFrame(
        [
            {"term": "Аббревиатура", "definition": "Расшифровка или значение"},
            {"term": "НСЖ", "definition": "Накопительное страхование жизни"},
        ]
    )

    result = service._read_glossary_sheet(Path("glossary_active.xlsx"), "Лист1")

    assert result.rows == [
        {"term": "НСЖ", "definition": "Накопительное страхование жизни"}
    ]


class DateFakeAt:
    def __init__(self, frame):
        self.frame = frame

    def __setitem__(self, key, value):
        idx, column = key
        self.frame.rows[idx][column] = value


class DateFakeDataFrame:
    def __init__(self, rows):
        self.rows = rows
        self.columns = list(rows[0].keys())
        self.at = DateFakeAt(self)

    def copy(self):
        return DateFakeDataFrame([row.copy() for row in self.rows])

    def __len__(self):
        return len(self.rows)

    def __getitem__(self, column):
        return [row.get(column) for row in self.rows]

    def __setitem__(self, column, value):
        if column not in self.columns:
            self.columns.append(column)
        values = value if isinstance(value, list) else [value] * len(self.rows)
        for row, item in zip(self.rows, values):
            row[column] = item

    def iterrows(self):
        for idx, row in enumerate(self.rows):
            yield idx, row


@pytest.mark.unit
def test_enrich_products_preserves_existing_input_date(monkeypatch, tmp_path) -> None:
    module = _load_tables_loader_module(monkeypatch)
    (tmp_path / "Fort Knox (2832) 20.05.26").mkdir()
    monkeypatch.setenv("PRODUCT_KITS_ROOT", str(tmp_path))

    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    df = DateFakeDataFrame([{"code": "2832", "name": "Fort Knox", "input_date": date(2026, 4, 8)}])

    result = service._enrich_products_with_kit_folders(df)

    assert result.rows[0]["input_date"] == date(2026, 4, 8)
    assert result.rows[0]["folder_kit"] == "Fort Knox (2832) 20.05.26"
    assert service.product_input_dates_from_table == 1
    assert service.product_input_dates_from_kits == 0
    assert service.product_input_dates_missing == 0


@pytest.mark.unit
def test_enrich_products_infers_input_date_from_kit_files(monkeypatch, tmp_path) -> None:
    module = _load_tables_loader_module(monkeypatch)
    folder = tmp_path / "Fort Knox (2832)"
    nested = folder / "nested"
    nested.mkdir(parents=True)
    (folder / "presenter 08.04.26.pdf").write_text("x", encoding="utf-8")
    (nested / "presenter 20.05.26.pdf").write_text("x", encoding="utf-8")
    monkeypatch.setenv("PRODUCT_KITS_ROOT", str(tmp_path))

    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    df = DateFakeDataFrame([{"code": "2832", "name": "Fort Knox", "input_date": ""}])

    result = service._enrich_products_with_kit_folders(df)

    assert result.rows[0]["input_date"] == date(2026, 5, 20)
    assert result.rows[0]["folder_kit"] == "Fort Knox (2832)"
    assert service.product_input_dates_from_table == 0
    assert service.product_input_dates_from_kits == 1
    assert service.product_input_dates_missing == 0


@pytest.mark.unit
def test_enrich_products_counts_input_dates_without_kits_root(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    monkeypatch.delenv("PRODUCT_KITS_ROOT", raising=False)

    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    df = DateFakeDataFrame(
        [
            {"code": "1", "name": "A", "input_date": date(2026, 4, 8)},
            {"code": "2", "name": "B", "input_date": ""},
        ]
    )

    service._enrich_products_with_kit_folders(df)

    assert service.product_kit_folders_found == 0
    assert service.product_kit_products_total == 2
    assert service.product_input_dates_from_table == 1
    assert service.product_input_dates_from_kits == 0
    assert service.product_input_dates_missing == 1
