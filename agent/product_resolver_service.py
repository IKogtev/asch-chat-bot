from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import asyncpg
import re

PRODUCT_SEARCH_TABLE = "product_search_dictionary"

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

@dataclass
class ProductCandidate:
    product_code: str
    canonical_name: str
    alias: str
    score: float


@dataclass
class ProductResolveResult:

    status: Literal[
        "resolved",
        "ambiguous",
        "not_found",
    ]

    product_code: str | None = None

    product_name: str | None = None

    options: list[ProductCandidate] | None = None


class ProductResolverService:

    def __init__(
        self,
        conn: asyncpg.Connection,
    ):
        self.conn = conn

    async def _search_exact(
        self,
        query: str,
    ) -> list[ProductCandidate]:

        normalized_query = (
            self.normalize_product_text(query)
        )

        rows = await self.conn.fetch(
            f"""
            SELECT
                product_code,
                canonical_name,
                alias,

                CASE

                    WHEN product_code = $1
                    THEN 1000

                    WHEN normalized_alias = $2
                    THEN 900

                    WHEN alias = $1
                    THEN 850

                    ELSE 0

                END AS score

            FROM {PRODUCT_SEARCH_TABLE}

            WHERE
                product_code = $1
                OR normalized_alias = $2
                OR alias = $1

            ORDER BY score DESC,
                    priority DESC

            LIMIT 20
            """,
            query,
            normalized_query,
        )

        return [
            ProductCandidate(
                product_code=row["product_code"],
                canonical_name=row["canonical_name"],
                alias=row["alias"],
                score=row["score"],
            )
            for row in rows
        ]
    
    async def _search_tokens(
        self,
        query: str,
    ) -> list[ProductCandidate]:

        tokens = (
            self.tokenize_product_text(query)
        )

        if not tokens:
            return []

        conditions = []
        values = []

        for idx, token in enumerate(tokens, start=1):
            conditions.append(
                f"search_tokens ILIKE ${idx}"
            )

            values.append(
                f"%{token}%"
            )

        sql = f"""
            SELECT
                product_code,
                canonical_name,
                alias,
                700 AS score

            FROM {PRODUCT_SEARCH_TABLE}

            WHERE {' AND '.join(conditions)}

            ORDER BY priority DESC

            LIMIT 20
        """

        rows = await self.conn.fetch(
            sql,
            *values,
        )

        return [
            ProductCandidate(
                product_code=row["product_code"],
                canonical_name=row["canonical_name"],
                alias=row["alias"],
                score=row["score"],
            )
            for row in rows
        ]
    

    async def _search_fuzzy(
        self,
        query: str,
    ) -> list[ProductCandidate]:

        normalized_query = (
            self.normalize_product_text(query)
        )

        rows = await self.conn.fetch(
            f"""
            SELECT
                product_code,
                canonical_name,
                alias,

                similarity(
                    normalized_alias,
                    $1
                ) AS score

            FROM {PRODUCT_SEARCH_TABLE}

            WHERE similarity(
                normalized_alias,
                $1
            ) > 0.45

            ORDER BY score DESC

            LIMIT 20
            """,
            normalized_query,
        )

        return [
            ProductCandidate(
                product_code=row["product_code"],
                canonical_name=row["canonical_name"],
                alias=row["alias"],
                score=row["score"],
            )
            for row in rows
        ]
    
    async def resolve_product(
        self,
        query: str,
    ) -> ProductResolveResult:

        exact = await self._search_exact(
            query
        )

        exact = self._unique_products(
            exact
        )

        if len(exact) == 1:

            candidate = exact[0]

            return ProductResolveResult(
                status="resolved",
                product_code=candidate.product_code,
                product_name=candidate.canonical_name,
            )

        if len(exact) > 1:

            return ProductResolveResult(
                status="ambiguous",
                options=exact,
            )

        token_matches = await self._search_tokens(
            query
        )

        token_matches = self._unique_products(
            token_matches
        )

        if len(token_matches) == 1:

            candidate = token_matches[0]

            return ProductResolveResult(
                status="resolved",
                product_code=candidate.product_code,
                product_name=candidate.canonical_name,
            )

        if len(token_matches) > 1:

            return ProductResolveResult(
                status="ambiguous",
                options=token_matches,
            )

        fuzzy_matches = await self._search_fuzzy(
            query
        )

        fuzzy_matches = self._unique_products(
            fuzzy_matches
        )

        if len(fuzzy_matches) == 1:

            candidate = fuzzy_matches[0]

            return ProductResolveResult(
                status="resolved",
                product_code=candidate.product_code,
                product_name=candidate.canonical_name,
            )

        if len(fuzzy_matches) > 1:

            return ProductResolveResult(
                status="ambiguous",
                options=fuzzy_matches,
            )

        return ProductResolveResult(
            status="not_found",
        )

    @staticmethod
    def _unique_products(
        candidates: list[ProductCandidate],
    ) -> list[ProductCandidate]:

        result = []
        seen = set()

        for candidate in candidates:

            if candidate.product_code in seen:
                continue

            seen.add(candidate.product_code)

            result.append(candidate)

        return result
    
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