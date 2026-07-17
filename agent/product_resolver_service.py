from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Literal

import asyncpg

from utils.logger import setup_logger


logger = setup_logger("product_resolver_service", "agent.log")

PRODUCT_SEARCH_TABLE = "product_search_dictionary"
PRODUCT_TABLE = "products"
DEFAULT_DATABASE_URL = "postgresql://aszh-bot:aszh-bot@postgres:5432/nstya_data"

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
    "alfa": "альфа",
    "alpha": "альфа",
    "альфа": "alfa alpha",
    "alfainvest": "альфаинвестиции",
    "альфаинвестиции": "alfa invest investments",
    "invest": "инвест инвестиции",
    "investment": "инвестиции инвест",
    "investments": "инвестиции инвест",
    "инвест": "invest investment investments",
    "инвестиции": "invest investment investments",
    "balance": "баланс",
    "баланс": "balance",
    "health": "здоровье",
    "здоровье": "health",
    "kids": "кидс детский",
    "kid": "кид кидс детский",
    "кидс": "kids kid",
    "кид": "kids kid",
    "детский": "kids kid",
    "plus": "плюс",
    "плюс": "plus",
    "bundle": "бандл бандлы бандлов",
    "bundl": "бандл бандлы бандлов",
    "бандл": "bundle bundl бандлы бандлов",
    "бандлы": "bundle bundl бандл",
    "бандлов": "bundle bundl бандл",
    "fort": "форт",
    "форт": "fort",
    "knox": "нокс ноксы ноксов",
    "нокс": "knox ноксы ноксов",
    "ноксы": "knox нокс",
    "ноксов": "knox нокс",
    "unit": "юнит",
    "юнит": "unit",
    "linked": "линкед линкд",
    "link": "линкед линкд",
    "линкед": "linked link",
    "линкд": "linked link",
    "life": "лайф",
    "лайф": "life",
    "smart": "смарт",
    "смарт": "smart",
    "premium": "премиум",
    "премиум": "premium",
    "junior": "джуниор",
    "джуниор": "junior",
    "protected": "защищенный защищенные",
    "protection": "защита защищенный защищенные",
    "защищенный": "protected protection",
    "защищенные": "protected protection",
    "защита": "protection protected",
    "capital": "капитал",
    "капитал": "capital",
    "shares": "акции",
    "stocks": "акции",
    "stock": "акции",
    "акции": "shares stocks stock",
    "active": "активные",
    "активные": "active",
    "bond": "облигации",
    "bonds": "облигации",
    "облигации": "bond bonds",
    "daily": "ежедневный",
    "ежедневный": "daily",
    "monthly": "ежемесячный",
    "ежемесячный": "monthly",
    "income": "доход",
    "доход": "income",
    "double": "двойной",
    "двойной": "double",
    "strategy": "стратегия",
    "стратегия": "strategy",
    "growth": "рост",
    "рост": "growth",
    "currency": "валютный валюта",
    "валютный": "currency",
    "валюта": "currency",
    "money": "деньги",
    "деньги": "money",
    "reserve": "резерв",
    "резерв": "reserve",
    "success": "успех",
    "успех": "success",
    "path": "путь",
    "путь": "path",
    "month": "месяц месяца месяцев мес",
    "months": "месяц месяца месяцев мес",
    "месяц": "month months",
    "месяца": "month months",
    "месяцев": "month months",
    "мес": "month months месяц месяца месяцев",
    "year": "год года лет",
    "years": "год года лет",
    "год": "year years",
    "года": "year years",
    "лет": "year years",
}

PRODUCT_QUERY_STOPWORDS = frozenset(
    {
        "and",
        "vs",
        "versus",
        "а",
        "без",
        "в",
        "во",
        "все",
        "всех",
        "выгрузи",
        "выгрузить",
        "где",
        "дай",
        "дайте",
        "дать",
        "документ",
        "документа",
        "документам",
        "документах",
        "документом",
        "документов",
        "документы",
        "для",
        "есть",
        "и",
        "из",
        "инфо",
        "информация",
        "к",
        "как",
        "какая",
        "какие",
        "какой",
        "карточка",
        "карточке",
        "карточки",
        "карточку",
        "комплект",
        "комплекта",
        "комплекту",
        "материал",
        "материала",
        "материалов",
        "материалы",
        "между",
        "мне",
        "можно",
        "на",
        "найди",
        "найти",
        "нужен",
        "нужна",
        "нужно",
        "нужны",
        "о",
        "об",
        "от",
        "отличается",
        "отличаются",
        "отличие",
        "отличия",
        "отправить",
        "отправь",
        "параметр",
        "параметра",
        "параметрам",
        "параметры",
        "по",
        "покажи",
        "показать",
        "покажите",
        "получить",
        "помоги",
        "пожалуйста",
        "пришли",
        "пришлите",
        "про",
        "продукт",
        "продукта",
        "продуктам",
        "продукте",
        "продуктов",
        "продуктом",
        "продукту",
        "продукты",
        "разница",
        "разницу",
        "расскажи",
        "рассказать",
        "с",
        "скачай",
        "скачать",
        "сравнение",
        "сравни",
        "сравнить",
        "сравните",
        "сравню",
        "списка",
        "списке",
        "список",
        "свойства",
        "свойство",
        "у",
        "чем",
        "что",
        "это",
    }
)

COMPARE_SPLIT_RE = re.compile(
    r"\s+(?:and|vs|versus|и|с|или)\s+|[,;/]+",
    flags=re.IGNORECASE,
)

# Символы валюты вырезаются normalize_product_text ([^a-zа-я0-9]),
# поэтому $ / ¥ варианты иначе схлопываются в один ambiguous-набор.
CURRENCY_HINT_MARKERS: dict[str, tuple[str, ...]] = {
    "usd": (
        "$",
        "usd",
        "dollar",
        "dollars",
        "доллар",
        "доллара",
        "доллары",
        "долларов",
        "долларах",
    ),
    "cny": (
        "¥",
        "￥",
        "cny",
        "cnh",
        "yuan",
        "юань",
        "юаня",
        "юани",
        "юаней",
        "юанях",
    ),
    "eur": (
        "€",
        "eur",
        "euro",
        "евро",
    ),
    "rub": (
        "₽",
        "rub",
        "rur",
        "руб",
        "рубль",
        "рубля",
        "рубли",
        "рублей",
        "рублях",
    ),
}


@dataclass(frozen=True)
class ProductCandidate:
    """Кандидат продукта, найденный в поисковом словаре продуктов."""
    product_code: str
    canonical_name: str
    alias: str
    normalized_alias: str = ""
    match_type: str = ""
    score: float = 0.0
    priority: int = 0

    def to_dict(self) -> dict[str, str | float | int]:
        """Преобразует кандидата в словарь для сохранения в состоянии агента."""
        return asdict(self)


@dataclass(frozen=True)
class ProductResolveResult:
    """Результат разрешения одного пользовательского упоминания продукта."""
    status: Literal["resolved", "ambiguous", "not_found", "error"]
    mention: str = ""
    product_code: str | None = None
    product_name: str | None = None
    options: list[ProductCandidate] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Преобразует результат в JSON-совместимый словарь."""
        return {
            "status": self.status,
            "mention": self.mention,
            "product_code": self.product_code,
            "product_name": self.product_name,
            "options": [candidate.to_dict() for candidate in self.options or []],
            "error": self.error,
        }


@dataclass(frozen=True)
class ProductMultiResolveResult:
    """Сводный результат разрешения нескольких продуктов, например для сравнения."""
    status: Literal["resolved", "partial", "ambiguous", "not_found", "error"]
    items: list[ProductResolveResult]

    def to_dict(self) -> dict[str, object]:
        """Преобразует сводный результат нескольких продуктов в JSON-совместимый словарь."""
        return {
            "status": self.status,
            "items": [item.to_dict() for item in self.items],
        }




@dataclass(frozen=True)
class ProductFilterResolveResult:
    """Результат предварительного разрешения продуктового фильтра в набор кандидатов."""
    status: Literal["resolved", "not_found", "error"]
    query: str = ""
    product_codes: list[str] | None = None
    products: list[ProductCandidate] | None = None
    matched_terms: list[str] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Преобразует результат фильтра в JSON-совместимый словарь для состояния агента."""
        return {
            "status": self.status,
            "query": self.query,
            "product_codes": self.product_codes or [],
            "products": [candidate.to_dict() for candidate in self.products or []],
            "matched_terms": self.matched_terms or [],
            "error": self.error,
        }

class ProductResolverService:
    """
    Разрешает пользовательские названия продуктов в канонический product_code.

    Сервис очищает запрос от служебных слов, выделяет отдельные упоминания
    продуктов, ищет кандидатов в product_search_dictionary и возвращает
    структурированный результат для runtime-состояния агента.
    """

    def __init__(
        self,
        database_url: str | None = None,
        *,
        fuzzy_threshold: float = 0.45,
        fuzzy_score_gap: float = 0.08,
    ) -> None:
        """Создает resolver с настройками подключения к БД и порогами fuzzy-поиска."""
        self.database_url = database_url or os.getenv("NSTYA_DATA_URL", DEFAULT_DATABASE_URL)
        self.fuzzy_threshold = fuzzy_threshold
        self.fuzzy_score_gap = fuzzy_score_gap
        self._pool: asyncpg.Pool | None = None

    async def _get_pool(self) -> asyncpg.Pool:
        """Лениво создает и возвращает пул подключений к PostgreSQL."""
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self.database_url)
        return self._pool

    async def close(self) -> None:
        """Закрывает пул подключений при остановке приложения или теста."""
        if self._pool:
            await self._pool.close()
            self._pool = None

    async def _search_exact(self, query: str) -> list[ProductCandidate]:
        """Ищет точные совпадения по коду, нормализованному алиасу или алиасу."""
        pool = await self._get_pool()
        normalized_query = self.normalize_product_text(query)

        rows = await pool.fetch(
            f"""
            SELECT
                product_code,
                canonical_name,
                alias,
                normalized_alias,
                match_type,
                priority,
                CASE
                    WHEN product_code = $1 THEN 1000
                    WHEN normalized_alias = $2 THEN 900
                    WHEN alias = $1 THEN 850
                    ELSE 0
                END AS score
            FROM {PRODUCT_SEARCH_TABLE}
            WHERE product_code = $1
               OR normalized_alias = $2
               OR alias = $1
            ORDER BY score DESC, priority DESC
            LIMIT 20
            """,
            query,
            normalized_query,
        )
        return self._rows_to_candidates(rows)

    async def _search_tokens(self, query: str) -> list[ProductCandidate]:
        """Ищет продукты, у которых search_tokens содержит все смысловые токены запроса."""
        pool = await self._get_pool()
        token_groups = self._token_alternative_groups(query)
        if not token_groups:
            return []

        conditions = []
        values = []
        idx = 1
        for group in token_groups:
            group_conditions = []
            for token in group:
                group_conditions.append(f"search_tokens ILIKE ${idx}")
                values.append(f"%{token}%")
                idx += 1
            conditions.append(f"({' OR '.join(group_conditions)})")

        sql = f"""
            SELECT
                product_code,
                canonical_name,
                alias,
                normalized_alias,
                match_type,
                priority,
                700 AS score
            FROM {PRODUCT_SEARCH_TABLE}
            WHERE {' AND '.join(conditions)}
            ORDER BY priority DESC, canonical_name, product_code
            LIMIT 20
        """

        rows = await pool.fetch(sql, *values)
        return self._rows_to_candidates(rows)

    async def _search_fuzzy(self, query: str) -> list[ProductCandidate]:
        """Ищет похожие алиасы через pg_trgm similarity после точного и token-поиска."""
        pool = await self._get_pool()
        normalized_query = self.normalize_product_text(query)

        rows = await pool.fetch(
            f"""
            SELECT
                product_code,
                canonical_name,
                alias,
                normalized_alias,
                match_type,
                priority,
                similarity(normalized_alias, $1) AS score
            FROM {PRODUCT_SEARCH_TABLE}
            WHERE similarity(normalized_alias, $1) > $2
            ORDER BY score DESC, priority DESC
            LIMIT 20
            """,
            normalized_query,
            self.fuzzy_threshold,
        )
        return self._rows_to_candidates(rows)

    async def resolve_product(self, query: str) -> ProductResolveResult:
        """Разрешает один пользовательский запрос или упоминание продукта."""
        mention = str(query or "").strip()
        if not mention:
            return ProductResolveResult(status="not_found", mention=mention)

        try:
            return await self._resolve_product_safe(mention)
        except Exception as exc:
            logger.warning("Product resolve failed: %s", exc, exc_info=True)
            return ProductResolveResult(
                status="error",
                mention=mention,
                error=type(exc).__name__,
            )

    async def _resolve_product_safe(self, mention: str) -> ProductResolveResult:
        """Выполняет каскад поиска без общего обработчика ошибок публичного метода."""
        for query in self._candidate_queries(mention):
            exact = self._unique_products(await self._search_exact(query))
            token_matches = self._unique_products(await self._search_tokens(query))
            exact_and_tokens = self._unique_products([*exact, *token_matches])
            exact_token_result = self._result_from_candidates(
                mention=mention,
                candidates=exact_and_tokens,
                allow_clear_top=False,
            )
            if exact_token_result.status != "not_found":
                return exact_token_result

            fuzzy_matches = self._unique_products(await self._search_fuzzy(query))
            fuzzy_result = self._result_from_candidates(
                mention=mention,
                candidates=fuzzy_matches,
                allow_clear_top=True,
            )
            if fuzzy_result.status != "not_found":
                return fuzzy_result

        return ProductResolveResult(status="not_found", mention=mention)

    async def resolve_product_mentions(
        self,
        mentions: list[str],
    ) -> ProductMultiResolveResult:
        """Разрешает уже выделенный список упоминаний продуктов."""
        results = [
            await self.resolve_product(mention)
            for mention in self._deduplicate_mentions(mentions)
        ]
        return ProductMultiResolveResult(
            status=self._multi_status(results),
            items=results,
        )

    async def resolve_products(
        self,
        query: str,
        *,
        expected_count: int | None = None,
    ) -> ProductMultiResolveResult:
        """Выделяет продукты из пользовательского запроса и разрешает каждый из них."""
        mentions = self.extract_product_mentions(query)
        if expected_count == 1 and len(mentions) != 1:
            mentions = [query]
        return await self.resolve_product_mentions(mentions)


    async def resolve_product_filter(self, query: str) -> ProductFilterResolveResult:
        """Разрешает запрос фильтра в набор подходящих продуктов без требования единственности."""
        normalized_query = str(query or "").strip()
        logger.debug("resolve_product_filter input=%r", normalized_query)
        if not normalized_query:
            logger.debug("resolve_product_filter empty input -> not_found")
            return ProductFilterResolveResult(status="not_found", query=normalized_query)

        try:
            result = await self._resolve_product_filter_safe(normalized_query)
            logger.debug(
                "resolve_product_filter result status=%s product_codes=%s matched_terms=%s products=%s error=%s",
                result.status,
                result.product_codes or [],
                result.matched_terms or [],
                self._candidate_summary(result.products or []),
                result.error,
            )
            return result
        except Exception as exc:
            logger.warning("Product filter resolve failed: %s", exc, exc_info=True)
            return ProductFilterResolveResult(
                status="error",
                query=normalized_query,
                error=type(exc).__name__,
            )

    async def _resolve_product_filter_safe(self, query: str) -> ProductFilterResolveResult:
        """Выполняет каскад поиска для product_filter и возвращает набор кандидатов."""
        mentions = self.extract_product_mentions(query)
        if len(mentions) > 1:
            return await self._resolve_product_filter_multi(query, mentions)

        candidate_queries = self._candidate_queries(query)
        logger.debug("resolve_product_filter candidate_queries=%s", candidate_queries)
        for candidate_query in candidate_queries:
            result = await self._resolve_product_filter_for_query(query, candidate_query)
            if result is not None:
                return result
        return ProductFilterResolveResult(status="not_found", query=query)

    async def _resolve_product_filter_multi(
        self,
        query: str,
        mentions: list[str],
    ) -> ProductFilterResolveResult:
        """Разрешает каждое упоминание отдельно и объединяет наборы кандидатов."""
        logger.debug("resolve_product_filter multi_mentions=%s", mentions)
        all_products: list[ProductCandidate] = []
        matched_terms: list[str] = []
        for mention in mentions:
            mention_result = await self._resolve_product_filter_safe(mention)
            if mention_result.products:
                all_products.extend(mention_result.products)
                matched_terms.extend(mention_result.matched_terms or [mention])

        products = self._unique_products(all_products)
        if products:
            return ProductFilterResolveResult(
                status="resolved",
                query=query,
                product_codes=[candidate.product_code for candidate in products],
                products=products,
                matched_terms=matched_terms,
            )
        return ProductFilterResolveResult(status="not_found", query=query)

    async def _resolve_product_filter_for_query(
        self,
        original_query: str,
        candidate_query: str,
    ) -> ProductFilterResolveResult | None:
        """Ищет кандидатов для одной поисковой строки; None, если совпадений нет."""
        for stage, candidates in (
            ("exact", await self._search_exact(candidate_query)),
            ("tokens", await self._search_tokens(candidate_query)),
            ("fuzzy", await self._search_fuzzy(candidate_query)),
        ):
            products = self._unique_products(candidates)
            logger.debug(
                "resolve_product_filter stage=%s query=%r count=%s candidates=%s",
                stage,
                candidate_query,
                len(products),
                self._candidate_summary(products),
            )
            if products:
                return ProductFilterResolveResult(
                    status="resolved",
                    query=original_query,
                    product_codes=[candidate.product_code for candidate in products],
                    products=products,
                    matched_terms=[candidate_query],
                )
        return None

    @staticmethod
    def _candidate_summary(candidates: list[ProductCandidate]) -> list[dict[str, object]]:
        """Возвращает компактное представление кандидатов для debug-логов."""
        return [
            {
                "code": candidate.product_code,
                "name": candidate.canonical_name,
                "alias": candidate.alias,
                "match_type": candidate.match_type,
                "score": round(candidate.score, 4),
                "priority": candidate.priority,
            }
            for candidate in candidates[:10]
        ]

    @classmethod
    def extract_product_mentions(cls, query: str) -> list[str]:
        """Извлекает отдельные продуктовые упоминания из запроса, включая сравнение."""
        text = str(query or "").strip()
        if not text:
            return []

        code_mentions = re.findall(r"\b\d{3,}(?:\+\d{3,})?\b", text)
        parts = [
            cls._remove_query_noise(part)
            for part in COMPARE_SPLIT_RE.split(text)
        ]
        mentions = [part for part in parts if part]
        mentions.extend(code_mentions)
        return cls._deduplicate_mentions(mentions)

    @staticmethod
    def _rows_to_candidates(rows: object) -> list[ProductCandidate]:
        """Преобразует строки asyncpg в список ProductCandidate."""
        candidates = []
        for row in rows:
            candidates.append(
                ProductCandidate(
                    product_code=str(row["product_code"] or "").strip(),
                    canonical_name=str(row["canonical_name"] or "").strip(),
                    alias=str(row["alias"] or "").strip(),
                    normalized_alias=str(row["normalized_alias"] or "").strip(),
                    match_type=str(row["match_type"] or "").strip(),
                    score=float(row["score"] or 0.0),
                    priority=int(row["priority"] or 0),
                )
            )
        return candidates

    def _result_from_candidates(
        self,
        *,
        mention: str,
        candidates: list[ProductCandidate],
        allow_clear_top: bool,
    ) -> ProductResolveResult:
        """Определяет итоговый статус по найденным кандидатам."""
        candidates = self._filter_candidates_by_currency_hint(mention, candidates)
        if not candidates:
            return ProductResolveResult(status="not_found", mention=mention)
        if len(candidates) == 1:
            return self._resolved_result(mention, candidates[0])
        if allow_clear_top and self._has_clear_top_candidate(candidates):
            return self._resolved_result(mention, candidates[0])
        return ProductResolveResult(
            status="ambiguous",
            mention=mention,
            options=candidates,
        )

    @classmethod
    def _detect_currency_hints(cls, value: str) -> set[str]:
        """Достаёт ключи валют из сырого текста (до вырезания символов нормализацией)."""
        text = str(value or "")
        if not text:
            return set()
        lowered = text.casefold()
        found: set[str] = set()
        for key, markers in CURRENCY_HINT_MARKERS.items():
            for marker in markers:
                if marker.isascii() and marker.isalpha():
                    needle = marker.casefold()
                    if needle in lowered:
                        found.add(key)
                        break
                elif marker in text or marker.casefold() in lowered:
                    found.add(key)
                    break
        return found

    @classmethod
    def _filter_candidates_by_currency_hint(
        cls,
        mention: str,
        candidates: list[ProductCandidate],
    ) -> list[ProductCandidate]:
        """Сужает ambiguous-набор, если в запросе явно указана валюта ($ / ¥ / доллары)."""
        if len(candidates) < 2:
            return candidates
        query_hints = cls._detect_currency_hints(mention)
        if not query_hints:
            return candidates

        filtered = [
            candidate
            for candidate in candidates
            if query_hints
            & cls._detect_currency_hints(
                f"{candidate.canonical_name} {candidate.alias}"
            )
        ]
        return filtered or candidates

    def _has_clear_top_candidate(self, candidates: list[ProductCandidate]) -> bool:
        """Проверяет, достаточно ли top fuzzy-кандидат оторвался от следующего."""
        if len(candidates) < 2:
            return True
        return candidates[0].score - candidates[1].score >= self.fuzzy_score_gap

    @staticmethod
    def _resolved_result(
        mention: str,
        candidate: ProductCandidate,
    ) -> ProductResolveResult:
        """Создает успешный результат разрешения из выбранного кандидата."""
        return ProductResolveResult(
            status="resolved",
            mention=mention,
            product_code=candidate.product_code,
            product_name=candidate.canonical_name,
            options=[candidate],
        )

    @staticmethod
    def _unique_products(candidates: list[ProductCandidate]) -> list[ProductCandidate]:
        """Удаляет дубли кандидатов по product_code, сохраняя порядок ранжирования."""
        result = []
        seen = set()
        for candidate in candidates:
            if not candidate.product_code or candidate.product_code in seen:
                continue
            seen.add(candidate.product_code)
            result.append(candidate)
        return result

    @classmethod
    def _candidate_queries(cls, query: str) -> list[str]:
        """Формирует варианты поискового запроса: очищенный, исходный и выделенные части."""
        candidates = [cls._remove_query_noise(query)]
        candidates.append(str(query or "").strip())
        candidates.extend(cls.extract_product_mentions(query))
        return cls._deduplicate_query_candidates(candidates)

    @classmethod
    def _remove_query_noise(cls, value: str) -> str:
        """Удаляет русские служебные слова, не относящиеся к названию продукта."""
        normalized = cls.normalize_product_text(value)
        tokens = [
            token
            for token in normalized.split()
            if token not in PRODUCT_QUERY_STOPWORDS
        ]
        return " ".join(tokens).strip()

    @staticmethod
    def _deduplicate_mentions(mentions: list[str]) -> list[str]:
        """Убирает повторяющиеся упоминания продуктов после нормализации."""
        result = []
        seen = set()
        for mention in mentions:
            normalized = ProductResolverService.normalize_product_text(mention)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            result.append(str(mention).strip())
        return result

    @staticmethod
    def _deduplicate_query_candidates(candidates: list[str]) -> list[str]:
        """Убирает повторяющиеся поисковые строки без изменения порядка."""
        result = []
        seen = set()
        for candidate in candidates:
            value = str(candidate or "").strip()
            key = value.casefold()
            if not value or key in seen:
                continue
            seen.add(key)
            result.append(value)
        return result

    @staticmethod
    def _multi_status(
        results: list[ProductResolveResult],
    ) -> Literal["resolved", "partial", "ambiguous", "not_found", "error"]:
        """Вычисляет общий статус для набора результатов разрешения."""
        if not results:
            return "not_found"
        statuses = {result.status for result in results}
        if "error" in statuses:
            return "error"
        if "ambiguous" in statuses:
            return "ambiguous"
        if statuses == {"resolved"}:
            return "resolved"
        if "resolved" in statuses:
            return "partial"
        return "not_found"

    @staticmethod
    def normalize_product_text(value: str) -> str:
        """Нормализует текст продукта для сравнения, токенизации и поиска."""
        value = str(value or "").strip().lower()
        value = value.replace("ё", "е")
        value = value.replace("+", " plus ")
        value = re.sub(r"[-_/.,;:()]+", " ", value)
        value = re.sub(r"[^a-zа-я0-9\s]", " ", value)
        value = re.sub(r"\s+", " ", value)
        return value.strip()

    @classmethod
    def transliterate_ru_to_en(cls, value: str) -> str:
        """Транслитерирует русские символы в латиницу для поиска по смешанным алиасам."""
        value = str(value or "").lower()
        return "".join(CYR_TO_LAT.get(char, char) for char in value)

    @classmethod
    def tokenize_product_text(cls, value: str) -> list[str]:
        """Возвращает уникальные поисковые токены с транслитерацией и алиасами продуктов."""
        normalized = cls.normalize_product_text(value)
        tokens = [token for token in normalized.split() if token]
        product_words = cls._normalized_common_product_words()
        reverse_product_words = {
            alias: key
            for key, value in product_words.items()
            for alias in value.split()
        }
        result = []

        if normalized in product_words:
            result.extend(product_words[normalized].split())
        if normalized in reverse_product_words:
            result.extend(reverse_product_words[normalized].split())

        for token in tokens:
            result.append(token)
            translit = cls.transliterate_ru_to_en(token)
            if translit != token:
                result.append(translit)
            if token in product_words:
                result.extend(product_words[token].split())
            if token in reverse_product_words:
                result.extend(reverse_product_words[token].split())
        return sorted(set(result))

    @classmethod
    def _token_alternative_groups(cls, value: str) -> list[list[str]]:
        """Группирует формы каждого токена запроса для поиска через OR внутри токена."""
        normalized = cls.normalize_product_text(value)
        tokens = [
            token
            for token in normalized.split()
            if token and token not in PRODUCT_QUERY_STOPWORDS
        ]
        product_words = cls._normalized_common_product_words()
        reverse_product_words = {
            alias: key
            for key, value in product_words.items()
            for alias in value.split()
        }
        groups = []

        for token in tokens:
            alternatives = [token]
            translit = cls.transliterate_ru_to_en(token)
            if translit != token:
                alternatives.append(translit)
            if token in product_words:
                alternatives.extend(product_words[token].split())
            if token in reverse_product_words:
                alternatives.extend(reverse_product_words[token].split())

            deduped = []
            seen = set()
            for alternative in alternatives:
                if alternative and alternative not in seen:
                    seen.add(alternative)
                    deduped.append(alternative)
            if deduped:
                groups.append(deduped)
        return groups

    @classmethod
    def _normalized_common_product_words(cls) -> dict[str, str]:
        """Нормализует словарь частых продуктовых слов перед применением к токенам."""
        return {
            cls.normalize_product_text(key): cls.normalize_product_text(value)
            for key, value in COMMON_PRODUCT_WORDS.items()
        }
    
    async def fetch_product_full_details(self, product_code: str) -> dict[str, str | None]:
        """
        получение деталей уточнения из таблицы products по product_code.
        """
        if not product_code:
            return {}
            
        pool = await self._get_pool()
        try:
            # Идем в главную таблицу products и забираем folder_kit если понадобяться ещё поля сможем взять отсюда
            row = await pool.fetchrow(
                f"""
                SELECT folder_kit 
                FROM {PRODUCT_TABLE} 
                WHERE code = $1 
                LIMIT 1
                """,
                str(product_code).strip()
            )
            if row:
                return {
                    "folder_kit": str(row.get("folder_kit") or "").strip() or None
                }
        except Exception as exc:
            logger.warning("Не удалось получить детали для code=%s: %s", product_code, exc)
            
        return {}
