from __future__ import annotations

import asyncio
import os
import re
from dataclasses import asdict, dataclass, replace
from typing import Literal

import asyncpg

from utils.logger import setup_logger


logger = setup_logger("product_resolver_service", "agent.log")

PRODUCT_SEARCH_TABLE = "product_search_dictionary"
DEFAULT_DATABASE_URL = "postgresql://aszh-bot:aszh-bot@postgres:5432/nstya_data"
ACTIVE_PRODUCT_STATUS = "Действующий"
ARCHIVED_PRODUCT_STATUS = "Архивный"
StatusPreference = Literal["active", "archived", "all"] | None

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
PRODUCT_STATUS_MODIFIER_RE = re.compile(
    r"^(?:активн\w*|архивн\w*|действующ\w*|архив(?:а|е|ом|у)?)$",
    flags=re.IGNORECASE,
)
COMPARE_MENTION_ARCHIVE_RE = re.compile(
    r"\bархив(?:а|е|ом|у)\b",
    flags=re.IGNORECASE,
)
# «год» / «года» без числа вроде «на год» → «1 год»; «3 года» и «три года» не трогаем.
BARE_YEAR_UNIT_RE = re.compile(
    r"(?<!\d )(?<!\d)\bгод(?:а|у)?\b",
    flags=re.IGNORECASE,
)
DURATION_UNITS_RE = r"(?:год(?:а|у)?|лет|месяц(?:а|ев)?|мес|year|years|month|months)"
DURATION_NUMBER_WORDS = {
    "тридцать шесть": "36",
    "двадцать четыре": "24",
    "восемнадцать": "18",
    "двенадцать": "12",
    "одиннадцать": "11",
    "пятнадцать": "15",
    "тридцать": "30",
    "двадцать": "20",
    "десять": "10",
    "девять": "9",
    "восемь": "8",
    "семь": "7",
    "шесть": "6",
    "пять": "5",
    "четыре": "4",
    "три": "3",
    "два": "2",
    "один": "1"
}
DURATION_NUMBER_WORD_RE = re.compile(
    r"\b("
    + "|".join(
        re.escape(word)
        for word in sorted(DURATION_NUMBER_WORDS, key=len, reverse=True)
    )
    + rf")\b(?=\s+{DURATION_UNITS_RE}\b)",
    flags=re.IGNORECASE,
)

COMPARE_SPLIT_RE = re.compile(
    r"\s+(?:and|vs|versus|и|или)\s+|[,;/]+",
    flags=re.IGNORECASE,
)
RUSSIAN_COMPARE_WITH_RE = re.compile(
    r"^\s*сравн(?:и|ите|ить)\s+(.+?)\s+с\s+(.+?)\s*$",
    flags=re.IGNORECASE,
)

# Символы валюты вырезаются при нормализации текста,
# поэтому варианты с $ и ¥ иначе схлопываются в один неоднозначный набор.
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


# Модели результатов, передаваемых в состояние агента.

@dataclass(frozen=True)
class ProductCandidate:
    """Кандидат продукта, найденный в поисковом словаре продуктов."""
    product_code: str
    canonical_name: str
    alias: str
    is_active: str = ""
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
    is_active: str | None = None
    options: list[ProductCandidate] | None = None
    error: str | None = None
    requested_status: StatusPreference = None
    found_via_fallback: bool = False

    def to_dict(self) -> dict[str, object]:
        """Преобразует результат в JSON-совместимый словарь."""
        return {
            "status": self.status,
            "mention": self.mention,
            "product_code": self.product_code,
            "product_name": self.product_name,
            "is_active": self.is_active,
            "options": [candidate.to_dict() for candidate in self.options or []],
            "error": self.error,
            "requested_status": self.requested_status,
            "found_via_fallback": self.found_via_fallback,
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
    status: Literal["resolved", "partial", "not_found", "error"]
    query: str = ""
    product_codes: list[str] | None = None
    products: list[ProductCandidate] | None = None
    matched_terms: list[str] | None = None
    unmatched_terms: list[str] | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Преобразует результат фильтра в JSON-совместимый словарь для состояния агента."""
        return {
            "status": self.status,
            "query": self.query,
            "product_codes": self.product_codes or [],
            "products": [candidate.to_dict() for candidate in self.products or []],
            "matched_terms": self.matched_terms or [],
            "unmatched_terms": self.unmatched_terms or [],
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
        self._pool_lock = asyncio.Lock()

    # Управление ресурсами PostgreSQL.

    async def _get_pool(self) -> asyncpg.Pool:
        """Лениво создает и возвращает пул подключений к PostgreSQL."""
        if self._pool is None:
            async with self._pool_lock:
                # Повторная проверка нужна после ожидания блокировки другим запросом.
                if self._pool is None:
                    self._pool = await asyncpg.create_pool(self.database_url)
        return self._pool

    async def close(self) -> None:
        """Закрывает пул подключений при остановке приложения или теста."""
        async with self._pool_lock:
            pool = self._pool
            self._pool = None
            if pool is not None:
                await pool.close()

    # Низкоуровневый поиск кандидатов в справочнике продуктов.

    async def _search_exact(
        self,
        query: str,
        product_status: str | None = None,
    ) -> list[ProductCandidate]:
        """Ищет точные совпадения по коду, нормализованному алиасу или алиасу."""
        pool = await self._get_pool()
        normalized_query = self.normalize_product_text(query)
        status_condition = "AND is_active = $3" if product_status else ""
        query_args = (
            (query, normalized_query, product_status)
            if product_status
            else (query, normalized_query)
        )

        rows = await pool.fetch(
            f"""
            WITH matches AS (
                SELECT
                    product_code,
                    canonical_name,
                    is_active,
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
                WHERE (
                    product_code = $1
                    OR normalized_alias = $2
                    OR alias = $1
                )
                {status_condition}
            ),
            unique_products AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY product_code, canonical_name, is_active
                    ORDER BY score DESC, priority DESC, normalized_alias, alias
                ) AS product_rank
                FROM matches
            )
            SELECT
                product_code,
                canonical_name,
                is_active,
                alias,
                normalized_alias,
                match_type,
                priority,
                score
            FROM unique_products
            WHERE product_rank = 1
            ORDER BY score DESC, priority DESC, canonical_name, is_active
            LIMIT 30
            """,
            *query_args,
        )
        return self._rows_to_candidates(rows)

    async def _search_tokens(
        self,
        query: str,
        product_status: str | None = None,
    ) -> list[ProductCandidate]:
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
                group_conditions.append(
                    f"(' ' || search_tokens || ' ') ILIKE ${idx}"
                )
                values.append(f"% {token} %")
                idx += 1
            conditions.append(f"({' OR '.join(group_conditions)})")

        status_condition = ""
        if product_status:
            status_condition = f"AND is_active = ${idx}"
            values.append(product_status)

        sql = f"""
            WITH matches AS (
                SELECT
                    product_code,
                    canonical_name,
                    is_active,
                    alias,
                    normalized_alias,
                    match_type,
                    priority,
                    700 AS score
                FROM {PRODUCT_SEARCH_TABLE}
                WHERE {' AND '.join(conditions)}
                {status_condition}
            ),
            unique_products AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY product_code, canonical_name, is_active
                    ORDER BY priority DESC, normalized_alias, alias
                ) AS product_rank
                FROM matches
            )
            SELECT
                product_code,
                canonical_name,
                is_active,
                alias,
                normalized_alias,
                match_type,
                priority,
                score
            FROM unique_products
            WHERE product_rank = 1
            ORDER BY priority DESC, canonical_name, is_active, product_code
            LIMIT 30
        """

        rows = await pool.fetch(sql, *values)
        return self._rows_to_candidates(rows)

    async def _search_fuzzy(
        self,
        query: str,
        product_status: str | None = None,
    ) -> list[ProductCandidate]:
        """Ищет похожие алиасы через pg_trgm similarity после точного и token-поиска."""
        pool = await self._get_pool()
        normalized_query = self.normalize_product_text(query)
        status_condition = "AND is_active = $3" if product_status else ""
        query_args = (
            (normalized_query, self.fuzzy_threshold, product_status)
            if product_status
            else (normalized_query, self.fuzzy_threshold)
        )

        rows = await pool.fetch(
            f"""
            WITH matches AS (
                SELECT
                    product_code,
                    canonical_name,
                    is_active,
                    alias,
                    normalized_alias,
                    match_type,
                    priority,
                    similarity(normalized_alias, $1) AS score
                FROM {PRODUCT_SEARCH_TABLE}
                WHERE similarity(normalized_alias, $1) > $2
                {status_condition}
            ),
            unique_products AS (
                SELECT *, ROW_NUMBER() OVER (
                    PARTITION BY product_code, canonical_name, is_active
                    ORDER BY score DESC, priority DESC, normalized_alias, alias
                ) AS product_rank
                FROM matches
            )
            SELECT
                product_code,
                canonical_name,
                is_active,
                alias,
                normalized_alias,
                match_type,
                priority,
                score
            FROM unique_products
            WHERE product_rank = 1
            ORDER BY score DESC, priority DESC, canonical_name, is_active
            LIMIT 30
            """,
            *query_args,
        )
        return self._rows_to_candidates(rows)

    # Разрешение одиночных и множественных продуктовых запросов.

    async def resolve_product(self, query: str) -> ProductResolveResult:
        """Разрешает один пользовательский запрос или упоминание продукта."""
        mention = str(query or "").strip()
        if not mention:
            return ProductResolveResult(status="not_found", mention=mention)

        try:
            return await self._resolve_product_safe(
                mention,
                status_preference=self._detect_status_preference(mention),
            )
        except Exception as exc:
            logger.warning("Product resolve failed: %s", exc, exc_info=True)
            return ProductResolveResult(
                status="error",
                mention=mention,
                error=type(exc).__name__,
            )

    async def _resolve_product_safe(
        self,
        mention: str,
        *,
        status_preference: StatusPreference,
        announce_fallback: bool = False,
    ) -> ProductResolveResult:
        """Выполняет каскад поиска без общего обработчика ошибок публичного метода."""
        if not announce_fallback:
            for product_status in self._status_search_passes(status_preference):
                result = await self._resolve_product_for_status(mention, product_status)
                if result.status != "not_found":
                    return result
            return ProductResolveResult(status="not_found", mention=mention)

        primary_status, fallback_status = self._compare_search_catalogs(
            status_preference
        )
        requested_status = (
            "archived" if primary_status == ARCHIVED_PRODUCT_STATUS else "active"
        )
        if primary_status is None and fallback_status is None:
            result = await self._resolve_product_for_status(mention, None)
            return self._with_status_meta(
                result,
                requested_status=None,
                found_via_fallback=False,
            )

        primary = await self._resolve_product_for_status(mention, primary_status)
        if primary.status != "not_found" or fallback_status is None:
            return self._with_status_meta(
                primary,
                requested_status=requested_status,
                found_via_fallback=False,
            )

        fallback = await self._resolve_product_for_status(mention, fallback_status)
        return self._with_status_meta(
            fallback,
            requested_status=requested_status,
            found_via_fallback=fallback.status != "not_found",
        )

    @staticmethod
    def _compare_search_catalogs(
        preference: StatusPreference,
    ) -> tuple[str | None, str | None]:
        """Основной каталог и запасной (архив ↔ действующие)."""
        if preference == "all":
            return (None, None)
        if preference == "archived":
            return (ARCHIVED_PRODUCT_STATUS, ACTIVE_PRODUCT_STATUS)
        return (ACTIVE_PRODUCT_STATUS, ARCHIVED_PRODUCT_STATUS)

    @staticmethod
    def _with_status_meta(
        result: ProductResolveResult,
        *,
        requested_status: StatusPreference,
        found_via_fallback: bool,
    ) -> ProductResolveResult:
        return replace(
            result,
            requested_status=requested_status,
            found_via_fallback=found_via_fallback,
        )

    async def _resolve_product_for_status(
        self,
        mention: str,
        product_status: str | None,
    ) -> ProductResolveResult:
        """Ищет продукт во всех стадиях для одного выбранного статуса."""
        pass_preference = self._preference_for_product_status(product_status)
        for query in self._candidate_queries(mention):
            exact = await self._search_exact(query, product_status)
            exact_result = self._result_from_candidates(
                mention=mention,
                candidates=exact,
                allow_clear_top=False,
                status_preference=pass_preference,
            )
            # Точное совпадение имеет приоритет над расширяющим поиском по токенам.
            if exact_result.status != "not_found":
                return exact_result

            token_matches = await self._search_tokens(query, product_status)
            token_result = self._result_from_candidates(
                mention=mention,
                candidates=token_matches,
                allow_clear_top=False,
                status_preference=pass_preference,
            )
            if token_result.status != "not_found":
                return token_result

            fuzzy_matches = await self._search_fuzzy(query, product_status)
            fuzzy_result = self._result_from_candidates(
                mention=mention,
                candidates=fuzzy_matches,
                allow_clear_top=True,
                status_preference=pass_preference,
            )
            if fuzzy_result.status != "not_found":
                return fuzzy_result

        return ProductResolveResult(status="not_found", mention=mention)

    async def _resolve_mentions_with_preferences(
        self,
        mentions: list[str],
        preferences: list[StatusPreference],
        *,
        announce_fallback: bool = False,
    ) -> ProductMultiResolveResult:
        """Разрешает каждое упоминание со своим статусом, без общего прохода."""
        results: list[ProductResolveResult] = []
        for mention, preference in zip(mentions, preferences):
            try:
                results.append(
                    await self._resolve_product_safe(
                        mention,
                        status_preference=preference,
                        announce_fallback=announce_fallback,
                    )
                )
            except Exception as exc:
                logger.warning(
                    "Product mention resolve failed: %s",
                    exc,
                    exc_info=True,
                )
                results.append(
                    ProductResolveResult(
                        status="error",
                        mention=mention,
                        error=type(exc).__name__,
                    )
                )
                return ProductMultiResolveResult(status="error", items=results)

        results = self._exclude_resolved_from_ambiguous_mentions(results)
        return ProductMultiResolveResult(
            status=self._multi_status(results),
            items=results,
        )

    async def resolve_product_mentions(
        self,
        mentions: list[str],
        *,
        status_preference: StatusPreference = None,
        shared_status_preference: StatusPreference = None,
    ) -> ProductMultiResolveResult:
        """Разрешает уже выделенный список упоминаний продуктов."""
        normalized_mentions = self._deduplicate_mentions(mentions)
        mention_preferences = [
            self._detect_compare_mention_status_preference(mention)
            for mention in normalized_mentions
        ]
        shared_preference = shared_status_preference
        if shared_preference is None and any(
            self._has_plural_archived_modifier(mention)
            for mention in normalized_mentions
        ):
            shared_preference = "archived"
        if shared_preference is not None:
            mention_preferences = [
                preference or shared_preference
                for preference in mention_preferences
            ]
        elif (
            status_preference is not None
            and all(preference is None for preference in mention_preferences)
        ):
            mention_preferences = [status_preference] * len(mention_preferences)
        return await self._resolve_mentions_with_preferences(
            normalized_mentions,
            mention_preferences,
            announce_fallback=True,
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
        elif len(mentions) > 1:
            # Сначала проверяем, не является ли вся фраза единым названием с союзом.
            status_preference = self._detect_status_preference(query)
            try:
                for product_status in self._status_search_passes(status_preference):
                    whole_result = await self._resolve_exact_whole_phrase(
                        query,
                        product_status,
                    )
                    if whole_result.status != "not_found":
                        return ProductMultiResolveResult(
                            status=whole_result.status,
                            items=[whole_result],
                        )
            except Exception as exc:
                logger.warning(
                    "Whole product phrase resolve failed: %s",
                    exc,
                    exc_info=True,
                )
                return ProductMultiResolveResult(
                    status="error",
                    items=[
                        ProductResolveResult(
                            status="error",
                            mention=query,
                            error=type(exc).__name__,
                        )
                    ],
                )
        mention_has_status = any(
            self._detect_compare_mention_status_preference(mention) is not None
            for mention in mentions
        )
        shared_status_preference = (
            "archived"
            if self._has_plural_archived_modifier(query)
            else None
        )
        return await self.resolve_product_mentions(
            mentions,
            status_preference=(
                None
                if (
                    len(mentions) > 1
                    or mention_has_status
                    or shared_status_preference is not None
                )
                else self._detect_status_preference(query)
            ),
            shared_status_preference=shared_status_preference,
        )

    # Разрешение фильтров, которые могут вернуть несколько продуктов.

    async def resolve_product_filter(self, query: str) -> ProductFilterResolveResult:
        """Разрешает запрос фильтра в набор подходящих продуктов без требования единственности."""
        normalized_query = str(query or "").strip()
        logger.debug("resolve_product_filter input=%r", normalized_query)
        if not normalized_query:
            logger.debug("resolve_product_filter empty input -> not_found")
            return ProductFilterResolveResult(status="not_found", query=normalized_query)

        try:
            status_preference = self._detect_status_preference(normalized_query)
            result = ProductFilterResolveResult(
                status="not_found",
                query=normalized_query,
            )
            for product_status in self._status_search_passes(status_preference):
                result = await self._resolve_product_filter_safe(
                    normalized_query,
                    status_preference=self._preference_for_product_status(
                        product_status
                    ),
                    product_status=product_status,
                )
                if result.products:
                    break
            logger.debug(
                "resolve_product_filter result status=%s product_codes=%s matched_terms=%s "
                "unmatched_terms=%s products=%s error=%s",
                result.status,
                result.product_codes or [],
                result.matched_terms or [],
                result.unmatched_terms or [],
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

    async def _resolve_product_filter_safe(
        self,
        query: str,
        *,
        allow_multi: bool = True,
        status_preference: StatusPreference,
        product_status: str | None,
    ) -> ProductFilterResolveResult:
        """Выполняет каскад поиска для product_filter и возвращает набор кандидатов."""
        mentions = self.extract_product_mentions(query)
        if allow_multi and len(mentions) > 1:
            # Точное полное название с союзом не должно превращаться в список продуктов.
            whole_result = await self._resolve_exact_whole_phrase(
                query,
                product_status,
            )
            whole_products = whole_result.options or []
            if whole_products:
                return ProductFilterResolveResult(
                    status="resolved",
                    query=query,
                    product_codes=[item.product_code for item in whole_products],
                    products=whole_products,
                    matched_terms=[query],
                )
            return await self._resolve_product_filter_multi(
                query,
                mentions,
                status_preference=status_preference,
                product_status=product_status,
            )

        candidate_queries = self._candidate_queries(query)
        logger.debug("resolve_product_filter candidate_queries=%s", candidate_queries)
        for candidate_query in candidate_queries:
            result = await self._resolve_product_filter_for_query(
                query,
                candidate_query,
                status_preference=status_preference,
                product_status=product_status,
            )
            if result is not None:
                return result
        return ProductFilterResolveResult(status="not_found", query=query)

    async def _resolve_product_filter_multi(
        self,
        query: str,
        mentions: list[str],
        *,
        status_preference: StatusPreference,
        product_status: str | None,
    ) -> ProductFilterResolveResult:
        """Разрешает каждое упоминание отдельно и объединяет наборы кандидатов."""
        logger.debug("resolve_product_filter multi_mentions=%s", mentions)
        all_products: list[ProductCandidate] = []
        matched_terms: list[str] = []
        unmatched_terms: list[str] = []
        for mention in mentions:
            if not self._remove_filter_modifiers(mention):
                continue
            # Не даём упоминанию снова уйти в множественный разбор: иначе фраза с кодами
            # (например «бандлы 8965 7698») рекурсивно извлекает саму себя.
            mention_result = await self._resolve_product_filter_safe(
                mention,
                allow_multi=False,
                status_preference=status_preference,
                product_status=product_status,
            )
            if mention_result.products:
                all_products.extend(mention_result.products)
                matched_terms.extend(mention_result.matched_terms or [mention])
            else:
                unmatched_terms.append(mention)

        products = self._prepare_candidates(
            all_products,
            status_preference=status_preference,
        )
        if products:
            return ProductFilterResolveResult(
                status="partial" if unmatched_terms else "resolved",
                query=query,
                product_codes=[candidate.product_code for candidate in products],
                products=products,
                matched_terms=matched_terms,
                unmatched_terms=unmatched_terms,
            )
        return ProductFilterResolveResult(
            status="not_found",
            query=query,
            unmatched_terms=unmatched_terms,
        )

    async def _resolve_product_filter_for_query(
        self,
        original_query: str,
        candidate_query: str,
        *,
        status_preference: StatusPreference,
        product_status: str | None,
    ) -> ProductFilterResolveResult | None:
        """Ищет кандидатов для одной поисковой строки; None, если совпадений нет."""
        for stage, search in (
            ("exact", self._search_exact),
            ("tokens", self._search_tokens),
            ("fuzzy", self._search_fuzzy),
        ):
            products = self._prepare_candidates(
                await search(candidate_query, product_status),
                status_preference=status_preference,
            )
            logger.debug(
                "resolve_product_filter stage=%s query=%r count=%s candidates=%s",
                stage,
                candidate_query,
                len(products),
                self._candidate_summary(products),
            )
            if products and not self.has_strong_filter_identity_evidence(
                [candidate_query],
                products,
            ):
                logger.debug(
                    "resolve_product_filter ignored weak identity stage=%s query=%r",
                    stage,
                    candidate_query,
                )
                continue
            if products:
                return ProductFilterResolveResult(
                    status="resolved",
                    query=original_query,
                    product_codes=[candidate.product_code for candidate in products],
                    products=products,
                    matched_terms=[candidate_query],
                )
        return None

    async def _resolve_exact_whole_phrase(
        self,
        query: str,
        product_status: str | None,
    ) -> ProductResolveResult:
        """Проверяет полную фразу как одно название без разбиения по союзам."""
        status_preference = self._preference_for_product_status(product_status)
        for candidate_query in self._whole_phrase_queries(query):
            result = self._result_from_candidates(
                mention=query,
                candidates=await self._search_exact(candidate_query, product_status),
                allow_clear_top=False,
                status_preference=status_preference,
            )
            if result.status != "not_found":
                return result
        return ProductResolveResult(status="not_found", mention=query)

    # Подготовка кандидатов и служебных данных для журналирования.

    @staticmethod
    def _candidate_summary(candidates: list[ProductCandidate]) -> list[dict[str, object]]:
        """Возвращает компактное представление кандидатов для debug-логов."""
        return [
            {
                "code": candidate.product_code,
                "name": candidate.canonical_name,
                "is_active": candidate.is_active,
                "alias": candidate.alias,
                "match_type": candidate.match_type,
                "score": round(candidate.score, 4),
                "priority": candidate.priority,
            }
            for candidate in candidates[:10]
        ]

    @classmethod
    def has_strong_filter_identity_evidence(
        cls,
        matched_terms: list[str],
        products: list[ProductCandidate] | list[dict[str, object]],
    ) -> bool:
        """Проверяет, что resolver нашел идентификатор продукта, а не свойство."""
        normalized_terms = [
            cls.normalize_product_text(term)
            for term in matched_terms
            if cls.normalize_product_text(term)
        ]
        for term in normalized_terms:
            if len(term.split()) >= 2:
                return True
            for product in products:
                if isinstance(product, ProductCandidate):
                    code = product.product_code
                    names = (
                        product.canonical_name,
                        product.alias,
                        product.normalized_alias,
                    )
                else:
                    code = str(
                        product.get("product_code") or product.get("code") or ""
                    ).strip()
                    names = (
                        str(
                            product.get("canonical_name")
                            or product.get("name")
                            or ""
                        ),
                        str(product.get("alias") or ""),
                        str(product.get("normalized_alias") or ""),
                    )
                if term == cls.normalize_product_text(code) or any(
                    term == cls.normalize_product_text(name) for name in names if name
                ):
                    return True
        return False

    # Разбор пользовательского текста и выделение продуктовых упоминаний.

    @classmethod
    def extract_product_mentions(cls, query: str) -> list[str]:
        """Извлекает отдельные продуктовые упоминания из запроса, включая сравнение."""
        text = str(query or "").strip()
        if not text:
            return []

        code_mentions = re.findall(r"\b\d{3,}(?:\+\d{3,})?\b", text)
        compare_with_match = RUSSIAN_COMPARE_WITH_RE.match(text)
        if compare_with_match:
            raw_parts = list(compare_with_match.groups())
        else:
            raw_parts = COMPARE_SPLIT_RE.split(text)
        parts = [
            cls._remove_query_noise(part)
            for part in raw_parts
        ]
        mentions: list[str] = []
        for part in parts:
            if not part:
                continue
            # Коды уже идут отдельными упоминаниями — убираем их из текстовой части,
            # иначе исходная фраза рекурсивно возвращается в множественный разбор.
            if code_mentions:
                part_without_codes = re.sub(
                    r"\b\d{3,}(?:\+\d{3,})?\b",
                    " ",
                    part,
                )
                part = " ".join(part_without_codes.split()).strip()
            if part:
                mentions.append(part)
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
                    is_active=str(row["is_active"] or "").strip(),
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
        status_preference: StatusPreference,
    ) -> ProductResolveResult:
        """Определяет итоговый статус по найденным кандидатам."""
        candidates = self._prepare_candidates(
            candidates,
            status_preference=status_preference,
            currency_query=mention,
        )
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

    @classmethod
    def _has_plural_archived_modifier(cls, value: str) -> bool:
        """Множественное «архивные» — общий статус пары в сравнении."""
        return bool(
            re.search(r"\bархивные\b", cls.normalize_product_text(value))
        )

    @classmethod
    def _detect_compare_mention_status_preference(
        cls,
        value: str,
    ) -> StatusPreference:
        """Статус одного упоминания в сравнении, включая «из архива» / «в архиве»."""
        preference = cls._detect_status_preference(value)
        if preference is not None:
            return preference
        # После вырезания стоп-слов «из»/«в» остаётся «архива» / «архиве».
        if COMPARE_MENTION_ARCHIVE_RE.search(cls.normalize_product_text(value)):
            return "archived"
        return None

    @classmethod
    def _detect_status_preference(
        cls,
        value: str,
    ) -> StatusPreference:
        """Определяет явно запрошенный статус каталога, не путая его с названием."""
        normalized = cls.normalize_product_text(value)
        if re.search(r"\b(?:все|оба|любой)\s+(?:статус|статусы|статуса)\b", normalized):
            return "all"
        if re.search(r"\bархивн\w*\b", normalized) or re.search(
            r"\b(?:в|из)\s+архив(?:а|е)?\b",
            normalized,
        ):
            return "archived"
        if re.search(r"\bдействующ\w*\b", normalized):
            return "active"
        if re.search(
            r"\b(?:активн\w*\s+продукт\w*|продукт\w*\s+активн\w*)\b",
            normalized,
        ):
            return "active"

        # Отдельное указание статуса рядом с кодом тоже считается явным запросом.
        without_codes = re.sub(r"\b\d{3,}(?:\+\d{3,})?\b", " ", normalized)
        remaining = [
            token
            for token in without_codes.split()
            if token not in PRODUCT_QUERY_STOPWORDS
            and not re.fullmatch(r"активн\w*", token)
        ]
        if not remaining and re.search(r"\bактивн\w*\b", normalized):
            return "active"
        return None

    # Правила проходов по статусам и окончательного отбора кандидатов.

    @staticmethod
    def _status_search_passes(
        preference: StatusPreference,
    ) -> tuple[str | None, ...]:
        """Возвращает последовательность полных поисковых проходов по статусам."""
        if preference == "active":
            return (ACTIVE_PRODUCT_STATUS,)
        if preference == "archived":
            return (ARCHIVED_PRODUCT_STATUS,)
        if preference == "all":
            return (None,)
        return (ACTIVE_PRODUCT_STATUS, ARCHIVED_PRODUCT_STATUS)

    @staticmethod
    def _preference_for_product_status(
        product_status: str | None,
    ) -> StatusPreference:
        """Преобразует SQL-статус прохода в правило подготовки кандидатов."""
        if product_status == ACTIVE_PRODUCT_STATUS:
            return "active"
        if product_status == ARCHIVED_PRODUCT_STATUS:
            return "archived"
        return "all"

    @classmethod
    def _filter_candidates_by_status_preference(
        cls,
        candidates: list[ProductCandidate],
        preference: StatusPreference,
    ) -> list[ProductCandidate]:
        """Применяет явный статус или отдает приоритет действующим продуктам."""
        if not candidates:
            return candidates
        if preference == "all":
            return candidates

        candidates_with_status = [item for item in candidates if item.is_active]
        # В импортированном каталоге статус может быть пустым. В таком случае
        # сохраняем найденные совпадения, а не отбрасываем их без объяснения.
        if not candidates_with_status:
            return candidates

        if preference == "active":
            return [item for item in candidates if item.is_active == ACTIVE_PRODUCT_STATUS]
        if preference == "archived":
            return [item for item in candidates if item.is_active == ARCHIVED_PRODUCT_STATUS]

        active = [item for item in candidates if item.is_active == ACTIVE_PRODUCT_STATUS]
        return active or candidates

    @classmethod
    def _prepare_candidates(
        cls,
        candidates: list[ProductCandidate],
        *,
        status_preference: StatusPreference,
        currency_query: str | None = None,
    ) -> list[ProductCandidate]:
        """Удаляет дубли и применяет правила отбора из пользовательского запроса."""
        prepared = cls._unique_products(candidates)
        if currency_query is not None:
            prepared = cls._filter_candidates_by_currency_hint(
                currency_query,
                prepared,
            )
        return cls._filter_candidates_by_status_preference(
            prepared,
            status_preference,
        )

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
            is_active=candidate.is_active,
            options=[candidate],
        )

    # Нормализация запросов, удаление шума и подготовка токенов поиска.

    @staticmethod
    def _unique_products(candidates: list[ProductCandidate]) -> list[ProductCandidate]:
        """Удаляет дубли алиасов, сохраняя разные продукты с одинаковым кодом."""
        result = []
        seen = set()
        for candidate in candidates:
            identity = (
                candidate.product_code,
                " ".join(candidate.canonical_name.casefold().split()),
                candidate.is_active,
            )
            if not candidate.product_code or identity in seen:
                continue
            seen.add(identity)
            result.append(candidate)
        return result

    @classmethod
    def _candidate_queries(cls, query: str) -> list[str]:
        """Формирует варианты поискового запроса: очищенный, исходный и выделенные части."""
        query = cls._normalize_query_duration(str(query or "").strip())
        cleaned_query = cls._remove_query_noise(query)
        candidates = [
            cleaned_query,
            cls._remove_filter_modifiers(cleaned_query),
        ]
        candidates.append(query)
        candidates.extend(cls.extract_product_mentions(query))
        return cls._deduplicate_query_candidates(candidates)

    @classmethod
    def _remove_query_noise(
        cls,
        value: str,
        *,
        preserve_connectors: bool = False,
    ) -> str:
        """Удаляет русские служебные слова, не относящиеся к названию продукта."""
        normalized = cls.normalize_product_text(value)
        preserved = {"and", "и", "или"} if preserve_connectors else set()
        tokens = [
            token
            for token in normalized.split()
            if token not in PRODUCT_QUERY_STOPWORDS or token in preserved
        ]
        return cls._normalize_query_duration(" ".join(tokens).strip())

    @classmethod
    def _normalize_query_duration(cls, value: str) -> str:
        """Приводит срок к виду каталога: «три года» → «3 года», голое «год» → «1 год»."""
        text = str(value or "").strip().replace("ё", "е").replace("Ё", "Е")
        if not text:
            return text

        def _digit_for_word(match: re.Match[str]) -> str:
            word = match.group(1).casefold()
            return DURATION_NUMBER_WORDS.get(word, match.group(1))

        text = DURATION_NUMBER_WORD_RE.sub(_digit_for_word, text)
        return BARE_YEAR_UNIT_RE.sub("1 год", text)

    @classmethod
    def _whole_phrase_queries(cls, query: str) -> list[str]:
        """Формирует варианты целого названия, сохраняя союзы внутри него."""
        query = cls._normalize_query_duration(str(query or "").strip())
        cleaned = cls._remove_query_noise(query, preserve_connectors=True)
        return cls._deduplicate_query_candidates(
            [
                cls._remove_filter_modifiers(cleaned),
                query,
            ]
        )

    @staticmethod
    def _remove_filter_modifiers(value: str) -> str:
        """Убирает статус продукта, чтобы он не мешал разрешению названия."""
        tokens = [
            token
            for token in str(value or "").split()
            if not PRODUCT_STATUS_MODIFIER_RE.fullmatch(token)
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

    @classmethod
    def _product_identity(
        cls,
        *,
        product_code: str | None = "",
        product_name: str | None = "",
        is_active: str | None = "",
    ) -> tuple[str, str, str]:
        """Ключ экземпляра продукта: код, нормализованное имя и статус."""
        return (
            str(product_code or "").strip(),
            " ".join(str(product_name or "").casefold().split()),
            str(is_active or "").strip(),
        )

    @classmethod
    def _candidate_identity(cls, candidate: ProductCandidate) -> tuple[str, str, str]:
        """Ключ экземпляра из поискового кандидата."""
        return cls._product_identity(
            product_code=candidate.product_code,
            product_name=candidate.canonical_name,
            is_active=candidate.is_active,
        )

    @classmethod
    def _exclude_resolved_from_ambiguous_mentions(
        cls,
        results: list[ProductResolveResult],
    ) -> list[ProductResolveResult]:
        """Убирает уже разрешенные экземпляры из options соседних ambiguous-упоминаний."""
        resolved_identities = {
            cls._product_identity(
                product_code=item.product_code,
                product_name=item.product_name,
                is_active=item.is_active,
            )
            for item in results
            if item.status == "resolved" and (item.product_code or item.product_name)
        }
        if not resolved_identities:
            return results

        updated: list[ProductResolveResult] = []
        for item in results:
            if item.status != "ambiguous" or not item.options:
                updated.append(item)
                continue
            remaining = [
                option
                for option in item.options
                if cls._candidate_identity(option) not in resolved_identities
            ]
            if len(remaining) == len(item.options):
                updated.append(item)
                continue
            if len(remaining) == 1:
                updated.append(
                    replace(
                        cls._resolved_result(item.mention, remaining[0]),
                        requested_status=item.requested_status,
                        found_via_fallback=item.found_via_fallback,
                    )
                )
                continue
            if not remaining:
                updated.append(
                    replace(
                        item,
                        status="not_found",
                        product_code=None,
                        product_name=None,
                        is_active=None,
                        options=None,
                        found_via_fallback=False,
                    )
                )
                continue
            updated.append(replace(item, options=remaining))
        return updated

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
