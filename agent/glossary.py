from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass
from typing import Any


GLOSSARY_TABLE_NAME = "glossary"
DEFAULT_DATABASE_URL = "postgresql://aszh-bot:aszh-bot@postgres:5432/nstya_data"
WORD_CHAR = r"0-9A-Za-z_А-Яа-яЁё"

CATEGORY_PRODUCT = "продукт"
CATEGORY_ABBREVIATION = "сокращение"
CATEGORY_TERM = "термин"
GLOSSARY_QUERY_EXPAND_CATEGORIES = frozenset({CATEGORY_PRODUCT, CATEGORY_ABBREVIATION})


@dataclass(frozen=True)
class GlossaryEntry:
    """Строка глоссария в памяти, подготовленная для точного поиска терминов."""

    term: str
    definition: str
    normalized_terms: tuple[str, ...]
    category: str = ""


@dataclass(frozen=True)
class _GlossarySpan:
    start: int
    end: int
    matched: str
    entry: GlossaryEntry


def normalize_glossary_text(value: str) -> str:
    """Нормализует текст пользователя или значение глоссария для точного поиска.

    Args:
        value: Исходный текст из пользовательского запроса, термина или синонима.

    Returns:
        Текст в нижнем регистре со схлопнутыми пробелами, заменой `ё` на `е`
        и удалённой внешней пунктуацией.
    """
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"\s+", " ", text)
    text = re.sub(rf"^[^{WORD_CHAR}]+|[^{WORD_CHAR}]+$", "", text)
    return text.strip()


def normalize_glossary_category(value: str) -> str:
    """Нормализует значение колонки ``category`` из глоссария."""
    return normalize_glossary_text(value)


def _split_aliases(value: str) -> list[str]:
    """Разбивает строку синонимов на отдельные значения.

    Args:
        value: Строка синонимов, разделённых точкой с запятой или запятой.

    Returns:
        Непустые синонимы без пробелов по краям.
    """
    return [
        item.strip()
        for item in re.split(r"[;,]", value or "")
        if item and item.strip()
    ]


def find_terms_in_text(
    text: str,
    entries: list[GlossaryEntry],
) -> list[list[str]]:
    """Находит термины и синонимы глоссария в пользовательском тексте.

    Args:
        text: Исходное сообщение пользователя.
        entries: Записи глоссария, загруженные из хранилища.

    Returns:
        Список троек ``[term, definition, category]`` для найденных записей.
        Более длинные термины проверяются первыми, дубли удаляются,
        совпадения ищутся по границам слов.
    """
    normalized_text = normalize_glossary_text(text)
    if not normalized_text:
        return []

    candidates: list[tuple[int, GlossaryEntry, str]] = []
    for entry in entries:
        for term in entry.normalized_terms:
            if term:
                candidates.append((len(term), entry, term))

    result: list[list[str]] = []
    seen: set[tuple[str, str]] = set()
    for _length, entry, term in sorted(candidates, key=lambda item: item[0], reverse=True):
        pattern = rf"(?<![{WORD_CHAR}]){re.escape(term)}(?![{WORD_CHAR}])"
        if not re.search(pattern, normalized_text):
            continue
        key = (entry.term, entry.definition)
        if key in seen:
            continue
        result.append([entry.term, entry.definition, entry.category])
        seen.add(key)

    return result


def _flexible_term_pattern(normalized_term: str) -> str:
    """Строит регэксп для поиска термина в исходном тексте с учётом регистра и «ё»."""
    parts: list[str] = []
    for ch in normalized_term:
        if ch == " ":
            parts.append(r"\s+")
        elif ch == "е":
            parts.append("[еёЕЁ]")
        elif "a" <= ch <= "z":
            parts.append(f"[{ch}{ch.upper()}]")
        elif "а" <= ch <= "я":
            parts.append(f"[{ch}{ch.upper()}]")
        elif ch.isdigit():
            parts.append(re.escape(ch))
        else:
            parts.append(re.escape(ch))
    return "".join(parts)


def _term_pattern(normalized_term: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?<![{WORD_CHAR}]){_flexible_term_pattern(normalized_term)}(?![{WORD_CHAR}])",
    )


def _definition_already_present(text: str, start: int, end: int, definition: str) -> bool:
    tail = text[end : end + len(definition) + 8].strip().lower()
    return tail.startswith(definition.strip().lower())


def _collect_glossary_spans(text: str, entries: list[GlossaryEntry]) -> list[_GlossarySpan]:
    spans: list[_GlossarySpan] = []
    for entry in entries:
        category = normalize_glossary_category(entry.category)
        if category not in GLOSSARY_QUERY_EXPAND_CATEGORIES:
            continue
        for normalized_term in entry.normalized_terms:
            if not normalized_term:
                continue
            for match in _term_pattern(normalized_term).finditer(text):
                spans.append(
                    _GlossarySpan(
                        start=match.start(),
                        end=match.end(),
                        matched=match.group(0),
                        entry=entry,
                    )
                )

    spans.sort(key=lambda item: (item.end - item.start, -item.start), reverse=True)
    chosen: list[_GlossarySpan] = []
    occupied: list[tuple[int, int]] = []
    for span in spans:
        if any(not (span.end <= start or span.start >= end) for start, end in occupied):
            continue
        chosen.append(span)
        occupied.append((span.start, span.end))
    return sorted(chosen, key=lambda item: item.start, reverse=True)


def build_glossary_expanded_query(text: str, entries: list[GlossaryEntry]) -> str:
    """Расширяет поисковый запрос с учётом категорий глоссария.

    - ``продукт``: сокращение/синоним заменяется на полное название;
    - ``сокращение``: после сокращения добавляется расшифровка;
    - ``термин`` и прочие категории для расширения запроса не используются.
    """
    source = str(text or "")
    if not source.strip() or not entries:
        return source

    result = source
    for span in _collect_glossary_spans(source, entries):
        category = normalize_glossary_category(span.entry.category)
        if category == CATEGORY_PRODUCT:
            replacement = span.entry.definition
        elif category == CATEGORY_ABBREVIATION:
            if _definition_already_present(result, span.start, span.end, span.entry.definition):
                continue
            replacement = f"{span.matched} {span.entry.definition}"
        else:
            continue
        result = result[: span.start] + replacement + result[span.end :]
    return result


build_doc_search_query = build_glossary_expanded_query


class GlossaryLookup:
    """Загружает глоссарий из PostgreSQL и ищет его термины в тексте."""

    def __init__(
        self,
        *,
        database_url: str | None = None,
        ttl_seconds: float | None = None,
    ) -> None:
        """Создаёт сервис поиска по глоссарию.

        Args:
            database_url: Необязательный PostgreSQL DSN. По умолчанию берётся
                из `NSTYA_DATA_URL` или из встроенного локального DSN.
            ttl_seconds: Необязательное время жизни кэша в памяти. По умолчанию
                берётся из
                `GLOSSARY_CACHE_TTL_SEC`.
        """
        self.database_url = database_url or os.getenv("NSTYA_DATA_URL", DEFAULT_DATABASE_URL)
        self.ttl_seconds = (
            float(ttl_seconds)
            if ttl_seconds is not None
            else float(os.getenv("GLOSSARY_CACHE_TTL_SEC", "300"))
        )
        self._entries: list[GlossaryEntry] = []
        self._loaded_at = 0.0

    async def find(self, text: str) -> list[list[str]]:
        """Ищет совпадения глоссария в пользовательском сообщении.

        Args:
            text: Исходное сообщение пользователя.

        Returns:
            Список троек ``[term, definition, category]``. Если глоссарий
            не удалось загрузить, возвращается пустой список.
        """
        try:
            entries = await self._get_entries()
        except Exception:
            return []
        return find_terms_in_text(text, entries)

    async def expand_search_query(self, text: str) -> str:
        """Возвращает запрос с расширениями глоссария по категориям."""
        try:
            entries = await self._get_entries()
        except Exception:
            return str(text or "")
        return build_glossary_expanded_query(text, entries)

    async def build_doc_search_query(self, text: str) -> str:
        """Возвращает запрос doc_search с расширениями по категориям глоссария."""
        return await self.expand_search_query(text)

    async def _get_entries(self) -> list[GlossaryEntry]:
        """Возвращает записи из кэша или перечитывает их после истечения TTL.

        Returns:
            Записи глоссария, готовые для поиска совпадений.
        """
        now = time.monotonic()
        if self._entries and now - self._loaded_at < self.ttl_seconds:
            return self._entries

        entries = await self._load_entries()
        self._entries = entries
        self._loaded_at = now
        return entries

    async def _load_entries(self) -> list[GlossaryEntry]:
        """Загружает строки глоссария из PostgreSQL.

        Returns:
            Записи глоссария, собранные из валидных строк БД. Если таблицы
            глоссария нет, возвращается пустой список.
        """
        import asyncpg

        conn = await asyncpg.connect(self._asyncpg_database_url())
        try:
            exists = await conn.fetchval(
                """
                SELECT 1
                FROM information_schema.tables
                WHERE table_schema = 'public' AND table_name = $1
                """,
                GLOSSARY_TABLE_NAME,
            )
            if not exists:
                return []

            rows = await conn.fetch(
                f"""
                SELECT term, definition, aliases_normalized, category
                FROM {self._quote_ident(GLOSSARY_TABLE_NAME)}
                WHERE term IS NOT NULL
                  AND definition IS NOT NULL
                  AND term_normalized IS NOT NULL
                """
            )
        finally:
            await conn.close()

        entries: list[GlossaryEntry] = []
        for row in rows:
            entry = self._row_to_entry(row)
            if entry is not None:
                entries.append(entry)
        return entries

    def _row_to_entry(self, row: Any) -> GlossaryEntry | None:
        """Преобразует строку PostgreSQL в запись глоссария.

        Args:
            row: Строка БД с полями `term`, `definition`, `aliases_normalized`
                и `category`.

        Returns:
            GlossaryEntry, если в строке достаточно данных для поиска, иначе
            None.
        """
        term = str(row["term"] or "").strip()
        definition = str(row["definition"] or "").strip()
        if not term or not definition:
            return None

        normalized = [normalize_glossary_text(term)]
        normalized.extend(
            normalize_glossary_text(alias)
            for alias in _split_aliases(str(row["aliases_normalized"] or ""))
        )
        normalized_terms = tuple(dict.fromkeys(item for item in normalized if item))
        if not normalized_terms:
            return None

        return GlossaryEntry(
            term=term,
            definition=definition,
            normalized_terms=normalized_terms,
            category=str(row.get("category") or "").strip(),
        )

    def _asyncpg_database_url(self) -> str:
        """Возвращает DSN, совместимый с asyncpg.

        Returns:
            URL БД, где `postgresql+asyncpg://` при необходимости заменён на
            `postgresql://`.
        """
        return self.database_url.replace("postgresql+asyncpg://", "postgresql://", 1)

    @staticmethod
    def _quote_ident(value: str) -> str:
        """Экранирует PostgreSQL-идентификатор.

        Args:
            value: Исходный идентификатор, например имя таблицы или колонки.

        Returns:
            Идентификатор в двойных кавычках с экранированными внутренними
            кавычками.
        """
        return f'"{value.replace(chr(34), chr(34) + chr(34))}"'
