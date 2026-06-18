from __future__ import annotations

import asyncio
import math
import os
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit, urlunsplit

import asyncpg
import pandas as pd

from app.services.product_kit_folder_resolver import (
    NOT_FOUND_VALUE,
    resolve_product_kit_folder,
)


DATA_CATALOG_FILE = "business layer_active.xlsx"
ACTIVE_TABLES_SUFFIX = "_active.xlsx"
PRODUCTS_TABLE_NAME = "products"
GLOSSARY_FILE_NAME = "glossary_active.xlsx"
GLOSSARY_TABLE_NAME = "glossary"
PRODUCT_KIT_FOLDER_COLUMN = "folder_kit"
PRODUCT_KIT_STATUS_COLUMN = "folder_kit_status"
PRODUCT_KITS_ROOT_ENV = "PRODUCT_KITS_ROOT"
PRODUCT_SEARCH_TABLE = "product_search_dictionary"

PRODUCT_SEARCH_COLUMNS = [
    "product_code",
    "canonical_name",
    "alias",
    "normalized_alias",
    "search_tokens",
    "match_type",
    "priority",
]
CYR_TO_LAT = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "y",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "ts",
    "ч": "ch",
    "ш": "sh",
    "щ": "sch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}

COMMON_PRODUCT_WORDS = {
    "kids": "кидс",
    "kid": "кид",
    "junior": "джуниор",
    "premium": "премиум",
    "life": "лайф",
    "smart": "смарт",
    "invest": "инвест",
    "plus": "плюс",
}

COMMON_PRODUCT_WORDS_REVERSE = {
    v: k
    for k, v in COMMON_PRODUCT_WORDS.items()
}

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

GLOSSARY_COLUMN_CANDIDATES = {
    "term": ("term", "сокращение"),
    "definition": ("definition", "определение"),
    "aliases": ("aliases", "синонимы"),
    "category": ("category", "категория"),
}


@dataclass(frozen=True)
class LoadedTable:
    """Описывает таблицу, загруженную из Excel-файла в базу данных.

    Атрибуты:
        table_name: Имя таблицы в базе данных.
        source_file: Имя исходного Excel-файла.
        source_sheet: Имя листа Excel, из которого загружены данные.
        rows: Количество загруженных строк.
        columns: Количество загруженных колонок.
    """

    table_name: str
    source_file: str
    source_sheet: str
    rows: int
    columns: int


@dataclass(frozen=True)
class TablesLoadResult:
    """Хранит результат загрузки Excel-таблиц в базу данных.

    Атрибуты:
        loaded_tables: Список успешно загруженных таблиц.
        validation_errors: Ошибки проверки каталога данных после загрузки.
    """

    loaded_tables: list[LoadedTable]
    validation_errors: list[str]
    product_kit_folders_found: int | None = None
    product_kit_products_total: int | None = None


class TablesLoaderService:
    """Загружает Excel-таблицы из директории в PostgreSQL."""

    def __init__(
        self,
        database_url: str,
        tables_dir: str | Path,
        glossary_dir: str | Path | None = None,
    ) -> None:
        """Инициализирует сервис загрузки таблиц.

        Аргументы:
            database_url: URL подключения к целевой базе PostgreSQL.
            tables_dir: Директория с Excel-файлами для загрузки.
        """
        self.database_url = database_url
        self.tables_dir = Path(tables_dir)
        self.glossary_dir = (
            Path(glossary_dir)
            if glossary_dir is not None
            else Path(os.getenv("GLOSSARY_SOURCE_DIR", self.tables_dir.parent / "glossary"))
        )
        self.product_kit_folders_found: int | None = None
        self.product_kit_products_total: int | None = None

    def load_all(self) -> TablesLoadResult:
        """Синхронно загружает все поддерживаемые Excel-таблицы.

        Возвращает:
            TablesLoadResult со списком загруженных таблиц и ошибками проверки.
        """
        return asyncio.run(self.load_all_async())

    async def load_all_async(self) -> TablesLoadResult:
        """Асинхронно загружает все поддерживаемые Excel-таблицы.

        Возвращает:
            TablesLoadResult со списком загруженных таблиц и ошибками проверки.

        Исключения:
            FileNotFoundError: Если директория с таблицами не существует.
        """
        if not self.tables_dir.exists():
            raise FileNotFoundError(f"Tables directory not found: {self.tables_dir}")

        loaded_tables: list[LoadedTable] = []
        self.product_kit_folders_found = None
        self.product_kit_products_total = None
        await self.ensure_database_exists()
        conn = await asyncpg.connect(self.database_url)
        try:
            await conn.execute("""
                CREATE EXTENSION IF NOT EXISTS pg_trgm
            """)
            await self._drop_public_tables(conn)
            loaded_tables.extend(await self._load_regular_tables(conn))
            loaded_tables.extend(await self._load_data_catalog(conn))
            loaded_tables.extend(await self._load_glossary(conn))
            validation_errors = await self._validate_data_catalog(conn)
        finally:
            await conn.close()

        return TablesLoadResult(
            loaded_tables=loaded_tables,
            validation_errors=validation_errors,
            product_kit_folders_found=self.product_kit_folders_found,
            product_kit_products_total=self.product_kit_products_total,
        )

    async def ensure_database_exists(self) -> None:
        """Создает целевую базу данных, если она указана в URL и еще не существует."""
        database_name = self._database_name_from_url(self.database_url)
        if not database_name:
            return

        maintenance_url = self._maintenance_database_url(self.database_url)
        maintenance_database_name = self._database_name_from_url(maintenance_url)
        if database_name == maintenance_database_name:
            return

        conn = await asyncpg.connect(maintenance_url)
        try:
            exists = await conn.fetchval(
                "SELECT 1 FROM pg_database WHERE datname = $1",
                database_name,
            )
            if not exists:
                await conn.execute(f"CREATE DATABASE {self._quote_ident(database_name)}")
        finally:
            await conn.close()

    def _build_product_search_dictionary(
        self,
        products_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """
        Строит поисковый справочник продуктов.

        Возвращает DataFrame:

        product_code
        canonical_name
        alias
        normalized_alias
        match_type
        priority
        """
        code_column = self._first_existing_column(
            products_df,
            ["code", "id", "product_id"],
        )

        name_column = self._first_existing_column(
            products_df,
            ["name", "product_name"],
        )

        if code_column is None or name_column is None:
            return pd.DataFrame(columns=PRODUCT_SEARCH_COLUMNS)

        rows = []
        for _, row in products_df.iterrows():

            product_code = self._cell_to_text(
                row.get(code_column)
            )
            product_name = self._cell_to_text(
                row.get(name_column)
            )
            if not product_code or not product_name:
                continue
            for alias, match_type, priority in self._generate_search_variants(
                product_name
            ):
                normalized_alias = (
                    self.normalize_product_text(alias)
                )
                search_tokens = " ".join(
                    self.tokenize_product_text(alias)
                )
                rows.append(
                    {
                        "product_code": product_code,
                        "canonical_name": product_name,
                        "alias": alias,
                        "normalized_alias": normalized_alias,
                        "search_tokens": search_tokens,
                        "match_type": match_type,
                        "priority": priority,
                    }
                )
        # логика дедупликации 
        deduped_rows = []
        seen = set()
        for row in rows:
            key = (
                row["product_code"],
                row["normalized_alias"],
            )
            if key in seen:
                continue
            seen.add(key)
            deduped_rows.append(row)
        return pd.DataFrame(
            deduped_rows,
            columns=PRODUCT_SEARCH_COLUMNS,
        )
    
    def _generate_product_aliases(
        self,
        product_name: str,
    ) -> list[tuple[str, str, int]]:
        
        aliases = []
        normalized = self.normalize_product_text(
            product_name
        )
        transliterated = self.transliterate_ru_to_en(
            normalized
        )
        aliases.append(
            (
                product_name,
                "canonical",
                100,
            )
        )
        aliases.append(
            (
                normalized,
                "normalized",
                90,
            )
        )
        if transliterated != normalized:
            aliases.append(
                (
                    transliterated,
                    "translit",
                    80,
                )
            )
        return aliases

    def _generate_search_variants(
        self,
        product_name: str,
    ) -> list[tuple[str, str, int]]:
        """
        Возвращает:
        (
            alias,
            match_type,
            priority
        )
        """
        variants = []
        normalized = self.normalize_product_text(
            product_name
        )
        # 1. Оригинал
        variants.append(
            (
                product_name,
                "canonical",
                100,
            )
        )
        # 2. Нормализованный
        variants.append(
            (
                normalized,
                "normalized",
                90,
            )
        )
        # 3. Полная транслитерация
        transliterated = self.transliterate_ru_to_en(
            normalized
        )
        if transliterated != normalized:
            variants.append(
                (
                    transliterated,
                    "translit",
                    80,
                )
            )
        tokens = normalized.split()
        # 4. Смешанный вариант
        mixed_tokens = self._replace_known_tokens(
            tokens
        )
        mixed_variant = " ".join(
            mixed_tokens
        )
        if mixed_variant != normalized:
            variants.append(
                (
                    mixed_variant,
                    "mixed",
                    85,
                )
            )
        # 5. Вариант для plus
        if "plus" in tokens:
            without_plus = " ".join(
                t for t in tokens
                if t != "plus"
            )
            if without_plus:
                variants.append(
                    (
                        without_plus,
                        "plus_token",
                        70,
                    )
                )
            variants.append(
                (
                    normalized.replace(
                        " plus",
                        "+"
                    ),
                    "plus_symbol",
                    75,
                )
            )
        return variants

    async def _drop_public_tables(self, conn: asyncpg.Connection) -> None:
        """Удаляет все пользовательские таблицы из схемы public.

        Аргументы:
            conn: Активное подключение к PostgreSQL.
        """
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
        """Загружает обычные активные Excel-таблицы, кроме каталога данных.

        Аргументы:
            conn: Активное подключение к PostgreSQL.

        Возвращает:
            Список описаний загруженных таблиц.
        """
        loaded_tables: list[LoadedTable] = []
        excel_files = sorted(
            path
            for path in self.tables_dir.glob("*.xlsx")
            if (
                not path.name.startswith("~$")
                and path.name != DATA_CATALOG_FILE
                and path.name.endswith(ACTIVE_TABLES_SUFFIX)
            )
        )

        for file_path in excel_files:
            workbook = pd.ExcelFile(file_path, engine="openpyxl")
            for sheet_name in workbook.sheet_names:
                df = self._read_sheet(file_path, sheet_name, skip_second_row_comment=True)
                table_name = self._regular_table_name(file_path, sheet_name, workbook.sheet_names)
                product_search_df = None
                if table_name == PRODUCTS_TABLE_NAME:
                    df = self._enrich_products_with_kit_folders(df)
                    product_search_df = self._build_product_search_dictionary(df)
                await self._replace_table(conn, table_name, df)
                if product_search_df is not None:
                    await self._replace_table(conn, PRODUCT_SEARCH_TABLE, product_search_df)
                    await self._create_indexes(conn, PRODUCT_SEARCH_TABLE, ["product_code", "normalized_alias",])
                    await self._create_product_search_indexes(conn)
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

    async def _load_glossary(self, conn: asyncpg.Connection) -> list[LoadedTable]:
        """Загружает клиентский глоссарий в таблицу PostgreSQL.

        Аргументы:
            conn: Активное подключение к PostgreSQL, в котором нужно создать
                или заменить таблицу глоссария.

        Возвращает:
            Список с одним описанием загруженной таблицы `glossary`. Если файл
            `glossary_active.xlsx` отсутствует, таблица всё равно создаётся
            пустой со стабильным набором колонок.
        """
        df = self._read_glossary_files()
        await self._replace_table(conn, GLOSSARY_TABLE_NAME, df)
        await self._create_indexes(
            conn,
            GLOSSARY_TABLE_NAME,
            ["term_normalized", "aliases_normalized"],
        )

        return [
            LoadedTable(
                table_name=GLOSSARY_TABLE_NAME,
                source_file=str(self.glossary_dir),
                source_sheet="*",
                rows=len(df),
                columns=len(df.columns),
            )
        ]

    def _read_glossary_files(self) -> pd.DataFrame:
        """Читает активный Excel-файл глоссария из директории `self.glossary_dir`.

        Аргументы:
            Нет явных аргументов. Метод использует `self.glossary_dir` как
            директорию-источник и читает один файл `glossary_active.xlsx`.
            Вторая строка файла считается русским пояснением к колонкам и
            игнорируется.

        Возвращает:
            DataFrame с колонками `term`, `definition`, `aliases`, `category`,
            `term_normalized`, `aliases_normalized`. При отсутствии директории
            или файла возвращается пустой DataFrame с теми же колонками.
        """
        rows: list[dict[str, str]] = []
        columns = [
            "term",
            "definition",
            "aliases",
            "category",
            "term_normalized",
            "aliases_normalized",
        ]

        file_path = self.glossary_dir / GLOSSARY_FILE_NAME
        if not file_path.exists():
            return pd.DataFrame(columns=columns)

        workbook = pd.ExcelFile(file_path, engine="openpyxl")
        for sheet_name in workbook.sheet_names:
            raw = self._read_glossary_sheet(file_path, sheet_name)
            rows.extend(self._glossary_rows_from_dataframe(raw))

        rows = self._deduplicate_glossary_rows(rows)
        return pd.DataFrame(rows, columns=columns)

    def _read_glossary_sheet(self, file_path: Path, sheet_name: str) -> pd.DataFrame:
        """Читает лист файла глоссария и удаляет строку с русскими пояснениями.

        Аргументы:
            file_path: Путь к файлу `glossary_active.xlsx`.
            sheet_name: Имя листа Excel, который нужно прочитать.

        Возвращает:
            DataFrame с техническими именами колонок из первой строки файла.
            Вторая строка Excel удаляется, потому что содержит пояснения для
            заказчика, а не данные глоссария.
        """
        df = self._read_sheet(file_path, sheet_name)
        if not df.empty:
            df = df.iloc[1:].reset_index(drop=True)
        return df

    @classmethod
    def tokenize_product_text(
        cls,
        value: str,
    ) -> list[str]:
        normalized = cls.normalize_product_text(value)
        tokens = [
            token
            for token in normalized.split()
            if token
        ]
        result = []
        for token in tokens:
            result.append(token)
            translit = cls.transliterate_ru_to_en(token)
            if translit != token:
                result.append(translit)
            if token in COMMON_PRODUCT_WORDS:
                result.append(
                    COMMON_PRODUCT_WORDS[token]
                )
            if token in COMMON_PRODUCT_WORDS_REVERSE:
                result.append(
                    COMMON_PRODUCT_WORDS_REVERSE[token]
                )
        return sorted(set(result))

    @classmethod
    def _replace_known_tokens(cls, tokens: list[str]) -> list[str]:
        result = []

        for token in tokens:
            if token in COMMON_PRODUCT_WORDS:
                result.append(COMMON_PRODUCT_WORDS[token])
            elif token in COMMON_PRODUCT_WORDS_REVERSE:
                result.append(COMMON_PRODUCT_WORDS_REVERSE[token])
            else:
                result.append(token)

        return result

    @staticmethod
    def _deduplicate_glossary_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
        """Удаляет полные дубли строк глоссария.

        Аргументы:
            rows: Список нормализованных строк глоссария. Каждая строка должна
                содержать ключи `term_normalized` и `definition`.

        Возвращает:
            Список строк без дублей по паре `term_normalized` + `definition`.
            Разные определения одного термина сохраняются как неоднозначность.
        """
        result: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            key = (row["term_normalized"], row["definition"])
            if key in seen:
                continue
            result.append(row)
            seen.add(key)
        return result

    def _glossary_rows_from_dataframe(self, df: pd.DataFrame) -> list[dict[str, str]]:
        """Преобразует лист Excel с глоссарием в нормализованные строки.

        Аргументы:
            df: DataFrame одного листа Excel. Метод ищет обязательные колонки
                `term`/`сокращение` и `definition`/`определение`, а также
                необязательные `aliases`/`синонимы` и `category`/`категория`.

        Возвращает:
            Список словарей для записи в таблицу `glossary`. Пустые строки,
            строки без термина или определения, а также дубли внутри листа
            пропускаются. Если обязательные колонки не найдены, возвращается
            пустой список.
        """
        term_column = self._first_existing_column(
            df,
            list(GLOSSARY_COLUMN_CANDIDATES["term"]),
        )
        definition_column = self._first_existing_column(
            df,
            list(GLOSSARY_COLUMN_CANDIDATES["definition"]),
        )
        aliases_column = self._first_existing_column(
            df,
            list(GLOSSARY_COLUMN_CANDIDATES["aliases"]),
        )
        category_column = self._first_existing_column(
            df,
            list(GLOSSARY_COLUMN_CANDIDATES["category"]),
        )

        if term_column is None or definition_column is None:
            return []

        rows: list[dict[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for _, row in df.iterrows():
            term = self._cell_to_text(row.get(term_column))
            definition = self._cell_to_text(row.get(definition_column))
            if not term or not definition:
                continue

            aliases = self._cell_to_text(row.get(aliases_column)) if aliases_column else ""
            category = self._cell_to_text(row.get(category_column)) if category_column else ""
            dedupe_key = (self.normalize_glossary_text(term), definition)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)

            alias_items = self._split_aliases(aliases)
            rows.append(
                {
                    "term": term,
                    "definition": definition,
                    "aliases": "; ".join(alias_items),
                    "category": category,
                    "term_normalized": self.normalize_glossary_text(term),
                    "aliases_normalized": ";".join(
                        self.normalize_glossary_text(alias) for alias in alias_items
                    ),
                }
            )

        return rows

    @staticmethod
    def normalize_glossary_text(value: str) -> str:
        """Нормализует термин или синоним для точного поиска.

        Аргументы:
            value: Исходный термин, синоним или другой текст из Excel.

        Возвращает:
            Строку в нижнем регистре, с заменой `ё` на `е`, схлопнутыми
            пробелами и удалённой внешней пунктуацией.
        """
        value = str(value or "").strip().lower().replace("ё", "е")
        value = re.sub(r"\s+", " ", value)
        value = re.sub(r"^[^\wа-яА-ЯёЁ]+|[^\wа-яА-ЯёЁ]+$", "", value)
        return value.strip()

    @staticmethod
    def normalize_product_text(value: str) -> str:
        """
        Нормализация текста продукта для поиска.
        """
        value = str(value or "").strip().lower()
        # ё -> е
        value = value.replace("ё", "е")
        # плюс превращаем в слово
        value = value.replace("+", " plus ")
        # любые разделители в пробел
        value = re.sub(r"[-_/.,;:()]+", " ", value)
        # удалить мусор
        value = re.sub(r"[^a-zа-я0-9\s]", " ", value)
        # схлопнуть пробелы
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    @classmethod
    def transliterate_ru_to_en(cls, value: str) -> str:
        value = str(value or "").lower()

        return "".join(
            CYR_TO_LAT.get(char, char)
            for char in value
        )

    @classmethod
    def _split_aliases(cls, value: str) -> list[str]:
        """Разбивает строку синонимов на уникальные элементы.

        Аргументы:
            value: Строка из колонки `aliases`/`синонимы`. В качестве
                разделителей поддерживаются запятая и точка с запятой.

        Возвращает:
            Список непустых синонимов в исходном написании без пробелов по
            краям. Повторы удаляются по нормализованному представлению.
        """
        items = re.split(r"[;,]", value or "")
        normalized: list[str] = []
        seen: set[str] = set()
        for item in items:
            text = item.strip()
            key = cls.normalize_glossary_text(text)
            if text and key and key not in seen:
                normalized.append(text)
                seen.add(key)
        return normalized

    @staticmethod
    def _cell_to_text(value: Any) -> str:
        """Преобразует значение ячейки Excel в безопасную текстовую строку.

        Аргументы:
            value: Значение ячейки, полученное из pandas DataFrame.

        Возвращает:
            Обрезанную строку. Для `None` и pandas-значений `NaN` возвращается
            пустая строка.
        """
        if value is None:
            return ""
        if pd.isna(value):
            return ""
        return str(value).strip()

    async def _load_data_catalog(self, conn: asyncpg.Connection) -> list[LoadedTable]:
        """Загружает листы каталога данных и создает индексы для них.

        Аргументы:
            conn: Активное подключение к PostgreSQL.

        Возвращает:
            Список описаний загруженных таблиц каталога данных.

        Исключения:
            FileNotFoundError: Если файл каталога данных не найден.
        """
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

    def _read_sheet(
        self,
        file_path: Path,
        sheet_name: str,
        *,
        skip_second_row_comment: bool = False,
    ) -> pd.DataFrame:
        """Читает лист Excel и нормализует его колонки.

        Аргументы:
            file_path: Путь к Excel-файлу.
            sheet_name: Имя листа для чтения.
            skip_second_row_comment: Удалять ли первую строку данных, если она
                помечена как комментарий.

        Возвращает:
            DataFrame без полностью пустых строк и с нормализованными колонками.
        """
        df = pd.read_excel(file_path, sheet_name=sheet_name, engine="openpyxl")
        if skip_second_row_comment and not df.empty and str(df.iloc[0, 0]).strip() == "#":
            df = df.iloc[1:].reset_index(drop=True)
        df = df.dropna(how="all")
        df.columns = self._normalize_columns(df.columns)
        return df

    def _enrich_products_with_kit_folders(self, df: pd.DataFrame) -> pd.DataFrame:
        """Добавляет к таблице продуктов сведения о папках продуктовых китов.

        Аргументы:
            df: DataFrame с продуктами.

        Возвращает:
            Копию DataFrame с колонками папки продуктового кита и статуса поиска.
        """
        df = df.copy()
        df[PRODUCT_KIT_FOLDER_COLUMN] = ""
        df[PRODUCT_KIT_STATUS_COLUMN] = ""

        code_column = self._first_existing_column(df, ["code", "id", "product_id"])
        name_column = self._first_existing_column(df, ["name", "product_name"])
        kits_root_value = os.getenv(PRODUCT_KITS_ROOT_ENV, "").strip()
        if not kits_root_value:
            df[PRODUCT_KIT_FOLDER_COLUMN] = NOT_FOUND_VALUE
            df[PRODUCT_KIT_STATUS_COLUMN] = f"{PRODUCT_KITS_ROOT_ENV} is empty"
            self.product_kit_folders_found = 0
            self.product_kit_products_total = self._dataframe_row_count(df)
            return df
        kits_root = Path(kits_root_value)

        if code_column is None:
            df[PRODUCT_KIT_FOLDER_COLUMN] = NOT_FOUND_VALUE
            df[PRODUCT_KIT_STATUS_COLUMN] = (
                "code column not found; expected one of ['code', 'id', 'product_id']"
            )
            self.product_kit_folders_found = 0
            self.product_kit_products_total = self._dataframe_row_count(df)
            return df

        found = 0
        total = 0
        for idx, row in df.iterrows():
            resolution = resolve_product_kit_folder(
                kits_root=kits_root,
                product_code=row.get(code_column),
                product_name=row.get(name_column) if name_column else "",
            )
            df.at[idx, PRODUCT_KIT_FOLDER_COLUMN] = resolution.folder_kit
            df.at[idx, PRODUCT_KIT_STATUS_COLUMN] = resolution.folder_kit_status
            total += 1
            if resolution.folder_kit != NOT_FOUND_VALUE:
                found += 1

        self.product_kit_folders_found = found
        self.product_kit_products_total = total

        return df

    def _dataframe_row_count(self, df: pd.DataFrame) -> int:
        try:
            return len(df)
        except TypeError:
            return sum(1 for _idx, _row in df.iterrows())

    def _first_existing_column(self, df: pd.DataFrame, candidates: list[str]) -> str | None:
        """Находит первую существующую колонку из списка кандидатов.

        Аргументы:
            df: DataFrame, в котором нужно найти колонку.
            candidates: Имена колонок в порядке приоритета.

        Возвращает:
            Имя первой найденной колонки или None.
        """
        columns = set(df.columns)
        for candidate in candidates:
            if candidate in columns:
                return candidate
        return None

    async def _replace_table(self, conn: asyncpg.Connection, table_name: str, df: pd.DataFrame) -> None:
        """Пересоздает таблицу в базе и вставляет в нее данные DataFrame.

        Аргументы:
            conn: Активное подключение к PostgreSQL.
            table_name: Имя таблицы для замены.
            df: DataFrame с данными для вставки.
        """
        async with conn.transaction():
            await conn.execute(f"DROP TABLE IF EXISTS {self._quote_ident(table_name)} CASCADE")
            await conn.execute(self._create_table_sql(table_name, df))
            await self._insert_dataframe(conn, table_name, df)

    def _create_table_sql(self, table_name: str, df: pd.DataFrame) -> str:
        """Формирует SQL для создания таблицы по типам колонок DataFrame.

        Аргументы:
            table_name: Имя создаваемой таблицы.
            df: DataFrame, по колонкам которого строится таблица.

        Возвращает:
            SQL-команду CREATE TABLE.
        """
        columns = [
            f"{self._quote_ident(column)} {self._sql_type(dtype)}"
            for column, dtype in df.dtypes.items()
        ]

        if not columns:
            columns = [f"{self._quote_ident('_empty')} TEXT"]

        return f"CREATE TABLE {self._quote_ident(table_name)} ({', '.join(columns)})"

    async def _insert_dataframe(self, conn: asyncpg.Connection, table_name: str, df: pd.DataFrame) -> None:
        """Вставляет строки DataFrame в существующую таблицу.

        Аргументы:
            conn: Активное подключение к PostgreSQL.
            table_name: Имя таблицы назначения.
            df: DataFrame с данными для вставки.
        """
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
        """Создает индексы для указанных колонок таблицы.

        Аргументы:
            conn: Активное подключение к PostgreSQL.
            table_name: Имя таблицы.
            columns: Колонки, для которых нужно создать индексы.
        """
        for column in columns:
            index_name = self._index_name(table_name, column)
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS "
                f"{self._quote_ident(index_name)} ON {self._quote_ident(table_name)} "
                f"({self._quote_ident(column)})"
            )

    async def _create_product_search_indexes(
        self,
        conn: asyncpg.Connection,
    ) -> None:
        """
        Индексы для быстрого поиска продуктов.
        """

        await conn.execute(
            f"""
            CREATE INDEX IF NOT EXISTS
            idx_product_search_trgm
            ON {PRODUCT_SEARCH_TABLE}
            USING gin (
                normalized_alias gin_trgm_ops
            )
            """
        )

    async def _validate_data_catalog(self, conn: asyncpg.Connection) -> list[str]:
        """Проверяет согласованность таблиц каталога данных.

        Аргументы:
            conn: Активное подключение к PostgreSQL.

        Возвращает:
            Список найденных ошибок валидации.
        """
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
        """Возвращает уникальные непустые значения первого столбца запроса.

        Аргументы:
            conn: Активное подключение к PostgreSQL.
            query: SQL-запрос, из которого читается первый столбец.

        Возвращает:
            Множество строковых значений первого столбца.
        """
        rows = await conn.fetch(query)
        return {str(row[0]) for row in rows if row[0] is not None}

    def _normalize_columns(self, columns: pd.Index) -> list[str]:
        """Нормализует имена колонок и делает их уникальными.

        Аргументы:
            columns: Исходные имена колонок из DataFrame.

        Возвращает:
            Список нормализованных имен колонок.
        """
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
        """Формирует имя обычной таблицы по имени файла и листа.

        Аргументы:
            file_path: Путь к исходному Excel-файлу.
            sheet_name: Имя текущего листа.
            sheet_names: Все имена листов в книге.

        Возвращает:
            Нормализованное имя таблицы.
        """
        file_stem = file_path.stem
        if file_stem.endswith("_active"):
            file_stem = file_stem[: -len("_active")]

        if len(sheet_names) == 1:
            return self._normalize_identifier(file_stem)

        return self._normalize_identifier(f"{file_stem}__{sheet_name}")

    def _normalize_identifier(self, value: str) -> str:
        """Нормализует строку в безопасный SQL-идентификатор.

        Аргументы:
            value: Исходная строка для нормализации.

        Возвращает:
            Идентификатор длиной не более 63 символов.
        """
        value = value.strip().lower()
        value = re.sub(r"[^0-9a-zа-яё_]+", "_", value, flags=re.IGNORECASE)
        value = re.sub(r"_+", "_", value).strip("_")
        if not value:
            value = "value"
        if value[0].isdigit():
            value = f"_{value}"
        return value[:63]

    def _index_name(self, table_name: str, column: str) -> str:
        """Формирует имя индекса для таблицы и колонки.

        Аргументы:
            table_name: Имя таблицы.
            column: Имя колонки.

        Возвращает:
            Нормализованное имя индекса.
        """
        return self._normalize_identifier(f"idx_{table_name}_{column}")[:63]

    def _database_name_from_url(self, database_url: str) -> str:
        """Извлекает имя базы данных из URL подключения.

        Аргументы:
            database_url: URL подключения к PostgreSQL.

        Возвращает:
            Имя базы данных или пустую строку.
        """
        parsed = urlsplit(database_url)
        return unquote(parsed.path.lstrip("/").split("/", 1)[0]).strip()

    def _maintenance_database_url(self, database_url: str) -> str:
        """Формирует URL подключения к служебной базе postgres.

        Аргументы:
            database_url: URL подключения к целевой базе.

        Возвращает:
            URL с теми же параметрами подключения и базой postgres.
        """
        parsed = urlsplit(database_url)
        return urlunsplit(
            (
                parsed.scheme,
                parsed.netloc,
                "/postgres",
                parsed.query,
                parsed.fragment,
            )
        )

    def _quote_ident(self, value: str) -> str:
        """Экранирует значение как SQL-идентификатор PostgreSQL.

        Аргументы:
            value: Имя таблицы, колонки или индекса.

        Возвращает:
            Экранированный SQL-идентификатор.
        """
        return f'"{value.replace(chr(34), chr(34) + chr(34))}"'

    def _sql_type(self, dtype: Any) -> str:
        """Преобразует тип pandas в тип колонки PostgreSQL.

        Аргументы:
            dtype: Тип данных pandas.

        Возвращает:
            Имя SQL-типа для создаваемой колонки.
        """
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
        """Преобразует значение DataFrame в значение для записи в PostgreSQL.

        Аргументы:
            value: Исходное значение ячейки DataFrame.
            dtype: Тип данных колонки pandas.

        Возвращает:
            Значение, совместимое с asyncpg, или None для пустых значений.
        """
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
