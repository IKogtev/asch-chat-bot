import importlib.util
import sys
import types
import warnings
from datetime import date
from pathlib import Path

import pytest
import pandas as real_pandas


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
def test_product_search_dictionary_keeps_same_code_products_and_status(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    module.pd = real_pandas
    service = module.TablesLoaderService(
        "postgresql://aszh-bot:secret@postgres:5432/nstya_data",
        ".",
    )
    products = real_pandas.DataFrame(
        [
            {
                "code": "8914",
                "name": "Фиксированный доход 1 год",
                "is_active": "Действующий",
            },
            {
                "code": "8914",
                "name": "Fort Knox 1 год",
                "is_active": "Архивный",
            },
        ]
    )

    dictionary = service._build_product_search_dictionary(products)
    canonical = dictionary[dictionary["match_type"] == "canonical"]

    assert list(canonical["product_code"]) == ["8914", "8914"]
    assert list(canonical["canonical_name"]) == [
        "Фиксированный доход 1 год",
        "Fort Knox 1 год",
    ]
    assert list(canonical["is_active"]) == ["Действующий", "Архивный"]

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

        def __getitem__(self, column):
            class FakeSeries(list):
                def astype(self, _dtype):
                    return list(self)

            return FakeSeries(row.get(column) for row in self.rows)

        def __setitem__(self, column, value):
            if column not in self.columns:
                self.columns.append(column)
            values = value if isinstance(value, list) else [value] * len(self.rows)
            for row, item in zip(self.rows, values):
                row[column] = item

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


@pytest.mark.unit
def test_normalize_products_dataframe_trims_strings_and_coerces_numeric_columns(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    module.pd = real_pandas
    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    df = real_pandas.DataFrame(
        {
            "code": real_pandas.Series([8958, " 001 "], dtype=object),
            "name": real_pandas.Series([" Bundle Fort Knox ", " Product "], dtype=object),
            "commission": real_pandas.Series([0.35, 1], dtype=object),
            "numeric_attribute": real_pandas.Series([" 10 ", "20.5"], dtype=object),
            "text_attribute": real_pandas.Series([" 10 years ", "20 years"], dtype=object),
            "empty_attribute": real_pandas.Series([" ", None], dtype=object),
            "empty_float_attribute": real_pandas.Series([float("nan"), float("nan")], dtype="float64"),
            "commission_condition": real_pandas.Series([" condition ", None], dtype=object),
        }
    )

    result = service._normalize_products_dataframe(df)

    assert result["name"].tolist() == ["Bundle Fort Knox", "Product"]
    assert result["commission_condition"].tolist()[0] == "condition"
    assert real_pandas.isna(result["commission_condition"].tolist()[1])
    assert result["code"].tolist() == [8958, "001"]
    assert real_pandas.api.types.is_float_dtype(result["commission"].dtype)
    assert real_pandas.api.types.is_float_dtype(result["numeric_attribute"].dtype)
    assert service._sql_type(result["commission"].dtype) == "NUMERIC"
    assert service._sql_type(result["numeric_attribute"].dtype) == "NUMERIC"
    assert service._sql_type(result["code"].dtype) == "TEXT"
    assert service._sql_type(result["text_attribute"].dtype) == "TEXT"
    assert service._sql_type(result["empty_attribute"].dtype) == "TEXT"
    assert service._sql_type(result["empty_float_attribute"].dtype) == "TEXT"


@pytest.mark.unit
def test_normalize_client_types_adds_stable_codes_and_removes_description_row(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    module.pd = real_pandas
    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    columns = list(module.CLIENT_TYPES_EXPECTED_COLUMNS)
    description = {column: f"Описание {column}" for column in columns}
    description["profile_name"] = "Тип профиля"
    conservative = {column: "" for column in columns}
    conservative.update(
        profile_name=" Консервативный ",
        required_properties="Статус: Действующий; уровень риска: Низкий",
    )
    moderate = {column: "" for column in columns}
    moderate.update(
        profile_name="Умеренный",
        preferred_properties="Уровень риска: Средний или Высокий",
    )
    source = real_pandas.DataFrame(
        [description, conservative, moderate],
        columns=columns,
    )

    result = service._normalize_client_types_dataframe(source)

    assert len(columns) == 12
    assert list(result.columns) == ["client_type_code", *columns]
    assert len(result.columns) == 13
    assert result["client_type_code"].tolist() == ["CT-001", "CT-002"]
    assert result["profile_name"].tolist() == ["Консервативный", "Умеренный"]


@pytest.mark.unit
def test_normalize_client_types_rejects_schema_mismatch(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    module.pd = real_pandas
    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    source = real_pandas.DataFrame([{"profile_name": "Консервативный"}])

    with pytest.raises(ValueError, match="Client Types schema mismatch"):
        service._normalize_client_types_dataframe(source)


@pytest.mark.unit
def test_parse_client_type_rule_cell_maps_properties_and_alternatives(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)

    parsed = module.TablesLoaderService.parse_client_type_rule_cell(
        "Статус: Действующий; Уровень риска: Средний или Высокий",
        profile_name="Умеренный",
        rule_column="preferred_properties",
    )

    assert parsed == [
        ("is_active", ["Действующий"]),
        ("product_risk_level", ["Средний", "Высокий"]),
    ]


@pytest.mark.unit
def test_parse_client_type_rule_cell_rejects_unknown_property(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)

    with pytest.raises(ValueError, match="Unknown Client Types product property"):
        module.TablesLoaderService.parse_client_type_rule_cell(
            "Неизвестное свойство: Значение",
            profile_name="Умеренный",
            rule_column="required_properties",
        )


@pytest.mark.unit
def test_validate_client_type_rules_uses_product_catalog_values(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    module.pd = real_pandas
    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    client_types = real_pandas.DataFrame(
        [
            {
                "profile_name": "Умеренный",
                "required_properties": "Статус: Действующий",
                "preferred_properties": "Уровень риска: Средний или Высокий",
                "acceptable_compromises": "Срок: Среднесрочный",
                "contraindications": "Ликвидность: Низкая",
            }
        ]
    )
    products = real_pandas.DataFrame(
        [
            {
                "is_active": "Действующий",
                "product_type": "Unit Linked",
                "term": "Среднесрочный",
                "capital_loss_risk": "Есть риск",
                "product_risk_level": "Средний",
                "income": "Не гарантирован",
                "contribution_type": "Единоразовый",
                "payout_type": "Без выплат",
                "liquidity": "Низкая",
                "currency": "Рубли",
            },
            {
                "is_active": "Действующий",
                "product_type": "Unit Linked",
                "term": "Среднесрочный",
                "capital_loss_risk": "Есть риск",
                "product_risk_level": "Высокий",
                "income": "Не гарантирован",
                "contribution_type": "Единоразовый",
                "payout_type": "Без выплат",
                "liquidity": "Высокая",
                "currency": "Рубли",
            },
        ]
    )

    service._validate_client_type_rules_against_products(client_types, products)


@pytest.mark.unit
def test_validate_client_type_rules_rejects_value_missing_from_products(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)
    module.pd = real_pandas
    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    client_types = real_pandas.DataFrame(
        [
            {
                "profile_name": "Умеренный",
                "required_properties": "Валюта: Евро",
                "preferred_properties": "",
                "acceptable_compromises": "",
                "contraindications": "",
            }
        ]
    )
    products = real_pandas.DataFrame(
        [
            {
                "is_active": "Действующий",
                "product_type": "Unit Linked",
                "term": "Среднесрочный",
                "capital_loss_risk": "Есть риск",
                "product_risk_level": "Средний",
                "income": "Не гарантирован",
                "contribution_type": "Единоразовый",
                "payout_type": "Без выплат",
                "liquidity": "Высокая",
                "currency": "Рубли",
            }
        ]
    )

    with pytest.raises(ValueError, match="currency='Евро' not found in products"):
        service._validate_client_type_rules_against_products(client_types, products)


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
        class DateFakeSeries(list):
            def astype(self, _dtype):
                return list(self)

        return DateFakeSeries(row.get(column) for row in self.rows)

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
def test_enrich_products_infers_input_date_without_incompatible_dtype_warning(
    monkeypatch,
    tmp_path,
) -> None:
    module = _load_tables_loader_module(monkeypatch)
    monkeypatch.setitem(sys.modules, "pandas", real_pandas)
    module.pd = real_pandas
    folder = tmp_path / "Fort Knox (2832)"
    folder.mkdir()
    (folder / "presenter 20.05.26.pdf").write_text("x", encoding="utf-8")
    monkeypatch.setenv("PRODUCT_KITS_ROOT", str(tmp_path))

    service = module.TablesLoaderService("postgresql://u:p@host:5432/db", ".")
    df = real_pandas.DataFrame(
        {
            "code": ["2832"],
            "name": ["Fort Knox"],
            "input_date": real_pandas.Series([float("nan")], dtype="float64"),
        }
    )

    with warnings.catch_warnings():
        warnings.simplefilter("error", FutureWarning)
        result = service._enrich_products_with_kit_folders(df)

    assert result.loc[0, "input_date"] == real_pandas.Timestamp("2026-05-20")
    assert real_pandas.api.types.is_datetime64_any_dtype(result["input_date"].dtype)


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


@pytest.mark.unit
def test_product_search_tokens_include_bundle_fort_knox_variants(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)

    tokens = module.TablesLoaderService.tokenize_product_text("Bundle Fort Knox 3+12 месяцев")

    assert "bundle" in tokens
    assert "бандл" in tokens
    assert "бандлы" in tokens
    assert "fort" in tokens
    assert "форт" in tokens
    assert "knox" in tokens
    assert "нокс" in tokens
    assert "ноксы" in tokens


@pytest.mark.unit
def test_product_search_tokens_include_alfa_kids_variants(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)

    tokens = module.TablesLoaderService.tokenize_product_text("Альфа Kids+ 5 лет")

    assert "альфа" in tokens
    assert "alfa" in tokens
    assert "alpha" in tokens
    assert "kids" in tokens
    assert "кидс" in tokens


@pytest.mark.unit
def test_product_search_tokens_include_russian_bundle_variants(monkeypatch) -> None:
    module = _load_tables_loader_module(monkeypatch)

    tokens = module.TablesLoaderService.tokenize_product_text("Бандл Защищенный капитал 18+36 мес.")

    assert "бандл" in tokens
    assert "бандлы" in tokens
    assert "bundle" in tokens
    assert "bundl" in tokens
