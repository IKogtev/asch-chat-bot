from __future__ import annotations

import asyncio
import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import asyncpg
import pandas as pd


DATA_CATALOG_FILE = "business layer_active.xlsx"

DATA_CATALOG_SHEETS = {
    "business entities": "dc_entities",
    "columns": "dc_columns",
    "analytics": "dc_analytics",
    "semantic_templates": "dc_semantic_templates",
}

DATA_CATALOG_INDEXES = {
    "dc_entities": ["source_table"],
    "dc_columns": ["source_table", "business_name"],
    "dc_analytics": ["source_table", "column", "value"],
}


@dataclass(frozen=True)
class LoadedTable:
    table_name: str
    source_file: str
    source_sheet: str
    rows: int
    columns: int


@dataclass(frozen=True)
class TablesLoadResult:
    loaded_tables: list[LoadedTable]
    validation_errors: list[str]


class TablesLoaderService:
    def __init__(self, database_url: str, tables_dir: str | Path) -> None:
        self.database_url = database_url
        self.tables_dir = Path(tables_dir)

    def load_all(self) -> TablesLoadResult:
        return asyncio.run(self.load_all_async())

    async def load_all_async(self) -> TablesLoadResult:
        if not self.tables_dir.exists():
            raise FileNotFoundError(f"Tables directory not found: {self.tables_dir}")

        loaded_tables: list[LoadedTable] = []
        conn = await asyncpg.connect(self.database_url)
        try:
            await self._drop_public_tables(conn)
            loaded_tables.extend(await self._load_regular_tables(conn))
            loaded_tables.extend(await self._load_data_catalog(conn))
            validation_errors = await self._validate_data_catalog(conn)
        finally:
            await conn.close()

        return TablesLoadResult(
            loaded_tables=loaded_tables,
            validation_errors=validation_errors,
        )

    async def _drop_public_tables(self, conn: asyncpg.Connection) -> None:
        rows = await conn.fetch(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """
        )

        for row in rows:
            await conn.execute(f"DROP TABLE IF EXISTS {self._quote_ident(row['table_name'])} CASCADE")

    async def _load_regular_tables(self, conn: asyncpg.Connection) -> list[LoadedTable]:
        loaded_tables: list[LoadedTable] = []
        excel_files = sorted(
            path
            for path in self.tables_dir.glob("*.xlsx")
            if not path.name.startswith("~$") and path.name != DATA_CATALOG_FILE
        )

        for file_path in excel_files:
            workbook = pd.ExcelFile(file_path, engine="openpyxl")
            for sheet_name in workbook.sheet_names:
                df = self._read_sheet(file_path, sheet_name)
                table_name = self._regular_table_name(file_path, sheet_name, workbook.sheet_names)
                await self._replace_table(conn, table_name, df)
                loaded_tables.append(
                    LoadedTable(
                        table_name=table_name,
                        source_file=file_path.name,
                        source_sheet=sheet_name,
                        rows=len(df),
                        columns=len(df.columns),
                    )
                )

        return loaded_tables

    async def _load_data_catalog(self, conn: asyncpg.Connection) -> list[LoadedTable]:
        file_path = self.tables_dir / DATA_CATALOG_FILE
        if not file_path.exists():
            raise FileNotFoundError(f"Data catalog file not found: {file_path}")

        loaded_tables: list[LoadedTable] = []
        for sheet_name, table_name in DATA_CATALOG_SHEETS.items():
            df = self._read_sheet(file_path, sheet_name)
            await self._replace_table(conn, table_name, df)
            await self._create_indexes(conn, table_name, DATA_CATALOG_INDEXES.get(table_name, []))
            loaded_tables.append(
                LoadedTable(
                    table_name=table_name,
                    source_file=file_path.name,
                    source_sheet=sheet_name,
                    rows=len(df),
                    columns=len(df.columns),
                )
            )

        return loaded_tables

    def _read_sheet(self, file_path: Path, sheet_name: str) -> pd.DataFrame:
        df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
        df = df.dropna(how="all")
        df.columns = self._normalize_columns(df.columns)
        return df

    async def _replace_table(self, conn: asyncpg.Connection, table_name: str, df: pd.DataFrame) -> None:
        async with conn.transaction():
            await conn.execute(f"DROP TABLE IF EXISTS {self._quote_ident(table_name)} CASCADE")
            await conn.execute(self._create_table_sql(table_name, df))
            await self._insert_dataframe(conn, table_name, df)

    def _create_table_sql(self, table_name: str, df: pd.DataFrame) -> str:
        columns = [
            f"{self._quote_ident(column)} {self._sql_type(dtype)}"
            for column, dtype in df.dtypes.items()
        ]

        if not columns:
            columns = [f"{self._quote_ident('_empty')} TEXT"]

        return f"CREATE TABLE {self._quote_ident(table_name)} ({', '.join(columns)})"

    async def _insert_dataframe(self, conn: asyncpg.Connection, table_name: str, df: pd.DataFrame) -> None:
        if df.empty:
            return

        placeholders = ", ".join(f"${idx}" for idx in range(1, len(df.columns) + 1))
        columns = ", ".join(self._quote_ident(column) for column in df.columns)
        query = f"INSERT INTO {self._quote_ident(table_name)} ({columns}) VALUES ({placeholders})"
        dtypes = list(df.dtypes)
        rows = [
            tuple(self._to_db_value(value, dtypes[idx]) for idx, value in enumerate(row))
            for row in df.itertuples(index=False, name=None)
        ]

        await conn.executemany(query, rows)

    async def _create_indexes(self, conn: asyncpg.Connection, table_name: str, columns: list[str]) -> None:
        for column in columns:
            index_name = self._index_name(table_name, column)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                f"{self._quote_ident(index_name)} ON {self._quote_ident(table_name)} "
                f"({self._quote_ident(column)})"
            )

    async def _validate_data_catalog(self, conn: asyncpg.Connection) -> list[str]:
        errors: list[str] = []

        existing_tables = await self._fetch_single_column(
            conn,
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
            """,
        )

        entity_tables = await self._fetch_single_column(conn, "SELECT DISTINCT source_table FROM dc_entities")
        missing_entity_tables = entity_tables - existing_tables
        if missing_entity_tables:
            errors.append(f"Tables from dc_entities not found: {sorted(missing_entity_tables)}")

        columns_tables = await self._fetch_single_column(conn, "SELECT DISTINCT source_table FROM dc_columns")
        invalid_columns_tables = columns_tables - entity_tables
        if invalid_columns_tables:
            errors.append(f"Unknown dc_columns.source_table values: {sorted(invalid_columns_tables)}")

        analytics_tables = await self._fetch_single_column(conn, "SELECT DISTINCT source_table FROM dc_analytics")
        invalid_analytics_tables = analytics_tables - entity_tables
        if invalid_analytics_tables:
            errors.append(f"Unknown dc_analytics.source_table values: {sorted(invalid_analytics_tables)}")

        return errors

    async def _fetch_single_column(self, conn: asyncpg.Connection, query: str) -> set[str]:
        rows = await conn.fetch(query)
        return {str(row[0]) for row in rows if row[0] is not None}

    def _normalize_columns(self, columns: pd.Index) -> list[str]:
        normalized: list[str] = []
        used: set[str] = set()

        for idx, column in enumerate(columns):
            name = "" if column is None else str(column).strip()
            if not name or name.lower().startswith("unnamed:"):
                name = f"unnamed_{idx}"

            name = self._normalize_identifier(name)
            base_name = name[:55]
            candidate = base_name
            suffix = 2
            while candidate in used:
                tail = f"_{suffix}"
                candidate = f"{base_name[:63 - len(tail)]}{tail}"
                suffix += 1

            used.add(candidate)
            normalized.append(candidate)

        return normalized

    def _regular_table_name(self, file_path: Path, sheet_name: str, sheet_names: list[str]) -> str:
        if len(sheet_names) == 1:
            return self._normalize_identifier(file_path.stem)

        return self._normalize_identifier(f"{file_path.stem}__{sheet_name}")

    def _normalize_identifier(self, value: str) -> str:
        value = value.strip().lower()
        value = re.sub(r"[^0-9a-zа-яё_]+", "_", value, flags=re.IGNORECASE)
        value = re.sub(r"_+", "_", value).strip("_")
        if not value:
            value = "value"
        if value[0].isdigit():
            value = f"_{value}"
        return value[:63]

    def _index_name(self, table_name: str, column: str) -> str:
        return self._normalize_identifier(f"idx_{table_name}_{column}")[:63]

    def _quote_ident(self, value: str) -> str:
        return f'"{value.replace(chr(34), chr(34) + chr(34))}"'

    def _sql_type(self, dtype: Any) -> str:
        if pd.api.types.is_integer_dtype(dtype):
            return "BIGINT"
        if pd.api.types.is_float_dtype(dtype):
            return "NUMERIC"
        if pd.api.types.is_bool_dtype(dtype):
            return "BOOLEAN"
        if pd.api.types.is_datetime64_any_dtype(dtype):
            return "TIMESTAMP"
        return "TEXT"

    def _to_db_value(self, value: Any, dtype: Any) -> Any:
        if value is None:
            return None
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        if pd.isna(value):
            return None
        if isinstance(value, pd.Timestamp):
            return value.to_pydatetime()
        if pd.api.types.is_object_dtype(dtype):
            return str(value)
        if isinstance(value, (datetime, date, bool, int, float)):
            return value
        return str(value)
