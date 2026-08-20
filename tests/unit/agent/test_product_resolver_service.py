from __future__ import annotations

import asyncio
from typing import Dict, List

import pytest

import agent.product_resolver_service as product_resolver_module
from agent.product_resolver_service import (
    ACTIVE_PRODUCT_STATUS,
    ARCHIVED_PRODUCT_STATUS,
    ProductCandidate,
    ProductResolveResult,
    ProductResolverService,
)


class FakeProductResolver(ProductResolverService):
    def __init__(
        self,
        *,
        exact: Dict[str, List[ProductCandidate]] | None = None,
        tokens: Dict[str, List[ProductCandidate]] | None = None,
        fuzzy: Dict[str, List[ProductCandidate]] | None = None,
        **kwargs,
    ) -> None:
        super().__init__(database_url="postgresql://unused", **kwargs)
        self.exact = exact or {}
        self.tokens = tokens or {}
        self.fuzzy = fuzzy or {}
        self.search_calls: list[tuple[str, str]] = []

    @staticmethod
    def _filter_status(
        candidates: List[ProductCandidate],
        product_status: str | None,
    ) -> List[ProductCandidate]:
        if not product_status or not any(item.is_active for item in candidates):
            return candidates
        return [item for item in candidates if item.is_active == product_status]

    async def _search_exact(
        self,
        query: str,
        product_status: str | None = None,
    ) -> list[ProductCandidate]:
        self.search_calls.append(("exact", query))
        return self._filter_status(self.exact.get(query, []), product_status)

    async def _search_tokens(
        self,
        query: str,
        product_status: str | None = None,
    ) -> list[ProductCandidate]:
        self.search_calls.append(("tokens", query))
        return self._filter_status(self.tokens.get(query, []), product_status)

    async def _search_fuzzy(
        self,
        query: str,
        product_status: str | None = None,
    ) -> list[ProductCandidate]:
        self.search_calls.append(("fuzzy", query))
        return self._filter_status(self.fuzzy.get(query, []), product_status)


def candidate(
    code: str,
    name: str,
    *,
    score: float = 1.0,
    priority: int = 100,
    is_active: str = "",
) -> ProductCandidate:
    return ProductCandidate(
        product_code=code,
        canonical_name=name,
        alias=name,
        is_active=is_active,
        normalized_alias=ProductResolverService.normalize_product_text(name),
        match_type="test",
        score=score,
        priority=priority,
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_returns_exact_single_match() -> None:
    resolver = FakeProductResolver(
        exact={"2832": [candidate("2832", "Fort Knox")]},
    )

    result = await resolver.resolve_product("2832")

    assert result.status == "resolved"
    assert result.product_code == "2832"
    assert result.product_name == "Fort Knox"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_returns_ambiguous_for_multiple_token_matches() -> None:
    resolver = FakeProductResolver(
        tokens={
            "fort knox": [
                candidate("2832", "Fort Knox"),
                candidate("2867", "Bundle Fort Knox"),
            ]
        },
    )

    result = await resolver.resolve_product("Fort Knox")

    assert result.status == "ambiguous"
    assert [item.product_code for item in result.options or []] == ["2832", "2867"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_prefers_active_product_for_duplicate_code() -> None:
    resolver = FakeProductResolver(
        exact={
            "8914": [
                candidate("8914", "Fort Knox 1 год", is_active="Архивный"),
                candidate(
                    "8914",
                    "Фиксированный доход 1 год",
                    is_active="Действующий",
                ),
            ]
        },
    )

    result = await resolver.resolve_product("8914")

    assert result.status == "resolved"
    assert result.product_name == "Фиксированный доход 1 год"
    assert result.is_active == "Действующий"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_honors_explicit_archived_status() -> None:
    candidates = [
        candidate("8914", "Fort Knox 1 год", is_active="Архивный"),
        candidate(
            "8914",
            "Фиксированный доход 1 год",
            is_active="Действующий",
        ),
    ]
    resolver = FakeProductResolver(exact={"8914": candidates})

    result = await resolver.resolve_product("архивный 8914")

    assert result.status == "resolved"
    assert result.product_name == "Fort Knox 1 год"
    assert result.is_active == "Архивный"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_checks_all_active_stages_before_archive_fallback() -> None:
    resolver = FakeProductResolver(
        exact={
            "fort knox": [
                candidate("2832", "Fort Knox", is_active=ARCHIVED_PRODUCT_STATUS)
            ]
        },
        fuzzy={
            "fort knox": [
                candidate(
                    "8914",
                    "Фиксированный доход 1 год",
                    score=0.9,
                    is_active=ACTIVE_PRODUCT_STATUS,
                )
            ]
        },
    )

    result = await resolver.resolve_product("Fort Knox")

    assert result.status == "resolved"
    assert result.product_code == "8914"
    assert result.is_active == ACTIVE_PRODUCT_STATUS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_falls_back_to_archive_after_empty_active_pass() -> None:
    resolver = FakeProductResolver(
        exact={
            "unit linked": [
                candidate("7698", "Unit Linked", is_active=ARCHIVED_PRODUCT_STATUS)
            ]
        },
    )

    result = await resolver.resolve_product("Unit Linked")

    assert result.status == "resolved"
    assert result.product_code == "7698"
    assert result.is_active == ARCHIVED_PRODUCT_STATUS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_recognizes_archive_noun_phrase() -> None:
    resolver = FakeProductResolver(
        exact={
            "8914": [
                candidate(
                    "8914",
                    "Фиксированный доход 1 год",
                    is_active=ACTIVE_PRODUCT_STATUS,
                ),
                candidate(
                    "8914",
                    "Fort Knox 1 год",
                    is_active=ARCHIVED_PRODUCT_STATUS,
                ),
            ],
        },
    )

    result = await resolver.resolve_product("найди продукт 8914 в архиве")

    assert result.status == "resolved"
    assert result.product_name == "Fort Knox 1 год"
    assert result.is_active == ARCHIVED_PRODUCT_STATUS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_removes_inflected_archive_modifier() -> None:
    resolver = FakeProductResolver(
        exact={
            "fort knox": [
                candidate(
                    "8914",
                    "Fort Knox 1 год",
                    is_active=ARCHIVED_PRODUCT_STATUS,
                )
            ],
        },
    )

    result = await resolver.resolve_product("найди архивного Fort Knox")

    assert result.status == "resolved"
    assert result.product_code == "8914"
    assert result.is_active == ARCHIVED_PRODUCT_STATUS


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_products_does_not_mix_archive_into_partial_active_result() -> None:
    resolver = FakeProductResolver(
        exact={
            "product a": [
                candidate("1001", "Product A", is_active=ACTIVE_PRODUCT_STATUS)
            ],
            "product b": [
                candidate("1002", "Product B", is_active=ARCHIVED_PRODUCT_STATUS)
            ],
        },
    )

    result = await resolver.resolve_product_mentions(["Product A", "Product B"])

    assert result.status == "partial"
    assert result.items[0].status == "resolved"
    assert result.items[0].is_active == ACTIVE_PRODUCT_STATUS
    assert result.items[1].status == "not_found"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_mentions_preserves_all_not_found_items() -> None:
    resolver = FakeProductResolver()

    result = await resolver.resolve_product_mentions(["Unknown A", "Unknown B"])

    assert result.status == "not_found"
    assert [item.mention for item in result.items] == ["Unknown A", "Unknown B"]
    assert all(item.status == "not_found" for item in result.items)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_mentions_returns_structured_error() -> None:
    class BrokenResolver(FakeProductResolver):
        async def _search_exact(
            self,
            query: str,
            product_status: str | None = None,
        ) -> list[ProductCandidate]:
            raise RuntimeError("db down")

    result = await BrokenResolver().resolve_product_mentions(["Fort Knox"])

    assert result.status == "error"
    assert result.items[0].status == "error"
    assert result.items[0].error == "RuntimeError"


@pytest.mark.unit
def test_exclude_resolved_identity_from_other_mention_options() -> None:
    fd3 = candidate(
        "8941",
        "Фиксированный доход 3 года + Альфа-Вклад Актив",
        is_active=ACTIVE_PRODUCT_STATUS,
    )
    fd1 = candidate(
        "8914",
        "Фиксированный доход 1 год",
        is_active=ACTIVE_PRODUCT_STATUS,
    )
    results = ProductResolverService._exclude_resolved_from_ambiguous_mentions(
        [
            ProductResolverService._resolved_result("Product A", fd3),
            ProductResolveResult(
                status="ambiguous",
                mention="Product B",
                options=[
                    candidate(
                        "8941",
                        "Фиксированный доход 3 года + Альфа-Вклад Актив",
                        is_active=ACTIVE_PRODUCT_STATUS,
                    ),
                    fd1,
                ],
            ),
        ]
    )

    assert results[0].status == "resolved"
    assert results[1].status == "resolved"
    assert results[1].product_code == "8914"
    assert results[1].product_name == "Фиксированный доход 1 год"


@pytest.mark.unit
def test_exclude_resolved_keeps_same_code_with_different_identity() -> None:
    active_fd1 = candidate(
        "8914",
        "Фиксированный доход 1 год",
        is_active=ACTIVE_PRODUCT_STATUS,
    )
    archived_fort_knox = candidate(
        "8914",
        "Fort Knox 1 год",
        is_active=ARCHIVED_PRODUCT_STATUS,
    )
    results = ProductResolverService._exclude_resolved_from_ambiguous_mentions(
        [
            ProductResolverService._resolved_result("Product A", active_fd1),
            ProductResolveResult(
                status="ambiguous",
                mention="Product B",
                options=[
                    candidate(
                        "8914",
                        "Фиксированный доход 1 год",
                        is_active=ACTIVE_PRODUCT_STATUS,
                    ),
                    archived_fort_knox,
                ],
            ),
        ]
    )

    assert results[1].status == "resolved"
    assert results[1].product_code == "8914"
    assert results[1].product_name == "Fort Knox 1 год"
    assert results[1].is_active == ARCHIVED_PRODUCT_STATUS


@pytest.mark.unit
def test_exclude_resolved_marks_mention_not_found_when_only_duplicate_remains() -> None:
    fd3 = candidate(
        "8941",
        "Фиксированный доход 3 года + Альфа-Вклад Актив",
        is_active=ACTIVE_PRODUCT_STATUS,
    )
    results = ProductResolverService._exclude_resolved_from_ambiguous_mentions(
        [
            ProductResolverService._resolved_result("Product A", fd3),
            ProductResolveResult(
                status="ambiguous",
                mention="Product B",
                options=[
                    candidate(
                        "8941",
                        "Фиксированный доход 3 года + Альфа-Вклад Актив",
                        is_active=ACTIVE_PRODUCT_STATUS,
                    )
                ],
            ),
        ]
    )

    assert results[0].status == "resolved"
    assert results[1].status == "not_found"
    assert results[1].mention == "Product B"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_mentions_excludes_resolved_identity_from_other_options() -> None:
    fd3 = candidate(
        "8941",
        "Фиксированный доход 3 года + Альфа-Вклад Актив",
        is_active=ACTIVE_PRODUCT_STATUS,
    )
    fd1 = candidate(
        "8914",
        "Фиксированный доход 1 год",
        is_active=ACTIVE_PRODUCT_STATUS,
    )
    resolver = FakeProductResolver(
        exact={"product a": [fd3]},
        tokens={"product b": [fd3, fd1]},
    )

    result = await resolver.resolve_product_mentions(["Product A", "Product B"])

    assert result.status == "resolved"
    assert result.items[0].product_code == "8941"
    assert result.items[1].status == "resolved"
    assert result.items[1].product_code == "8914"
    assert result.items[1].product_name == "Фиксированный доход 1 год"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_keeps_same_code_active_names_ambiguous() -> None:
    resolver = FakeProductResolver(
        exact={
            "7695": [
                candidate("7695", "Юнит Линк Активные облигации", is_active="Действующий"),
                candidate("7695", "Юнит Линк Стратегия роста", is_active="Действующий"),
            ]
        },
    )

    result = await resolver.resolve_product("7695")

    assert result.status == "ambiguous"
    assert [item.canonical_name for item in result.options or []] == [
        "Юнит Линк Активные облигации",
        "Юнит Линк Стратегия роста",
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_disambiguates_by_currency_symbol_in_query() -> None:
    """$ / ¥ вырезаются нормализацией, но сырой mention должен выбрать валютный вариант."""
    usd = candidate("8957", "Защищенный капитал $ 3 года")
    cny = candidate("8962", "Защищенный капитал ¥ 3 года")
    mention = "Защищенный капитал $ 3 года"
    cleaned = ProductResolverService._remove_query_noise(mention)
    resolver = FakeProductResolver(
        tokens={cleaned: [usd, cny]},
    )

    result = await resolver.resolve_product(mention)

    assert result.status == "resolved"
    assert result.product_code == "8957"
    assert result.product_name == "Защищенный капитал $ 3 года"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_disambiguates_by_currency_word_in_query() -> None:
    usd = candidate("8957", "Защищенный капитал $ 3 года")
    cny = candidate("8962", "Защищенный капитал ¥ 3 года")
    mention = "Защищенный капитал 3 года в юанях"
    cleaned = ProductResolverService._remove_query_noise(mention)
    resolver = FakeProductResolver(
        tokens={cleaned: [usd, cny]},
    )

    result = await resolver.resolve_product(mention)

    assert result.status == "resolved"
    assert result.product_code == "8962"


@pytest.mark.unit
def test_filter_candidates_by_currency_hint_keeps_matching_symbol() -> None:
    usd = candidate("8957", "Защищенный капитал $ 3 года")
    cny = candidate("8962", "Защищенный капитал ¥ 3 года")

    filtered = ProductResolverService._filter_candidates_by_currency_hint(
        "Дай комплект по Защищенному капиталу $ 3 года",
        [usd, cny],
    )

    assert [item.product_code for item in filtered] == ["8957"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_uses_clear_fuzzy_top_match() -> None:
    resolver = FakeProductResolver(
        fuzzy={
            "fort nox": [
                candidate("2832", "Fort Knox", score=0.82),
                candidate("2867", "Bundle Fort Knox", score=0.70),
            ]
        },
        fuzzy_score_gap=0.08,
    )

    result = await resolver.resolve_product("Fort Nox")

    assert result.status == "resolved"
    assert result.product_code == "2832"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_keeps_close_fuzzy_matches_ambiguous() -> None:
    resolver = FakeProductResolver(
        fuzzy={
            "fort nox": [
                candidate("2832", "Fort Knox", score=0.82),
                candidate("2867", "Bundle Fort Knox", score=0.78),
            ]
        },
        fuzzy_score_gap=0.08,
    )

    result = await resolver.resolve_product("Fort Nox")

    assert result.status == "ambiguous"
    assert len(result.options or []) == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_strips_query_noise_before_search() -> None:
    resolver = FakeProductResolver(
        exact={"fort knox": [candidate("2832", "Fort Knox")]},
    )

    result = await resolver.resolve_product("покажи карточку продукта Fort Knox")

    assert result.status == "resolved"
    assert result.product_code == "2832"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_products_resolves_compare_mentions() -> None:
    resolver = FakeProductResolver(
        exact={
            "fort knox": [candidate("2832", "Fort Knox")],
            "unit linked": [candidate("7698", "Unit Linked")],
        },
    )

    result = await resolver.resolve_products("сравни Fort Knox и Unit Linked")

    assert result.status == "resolved"
    assert [item.product_code for item in result.items] == ["2832", "7698"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_products_resolves_russian_compare_with_separator() -> None:
    resolver = FakeProductResolver(
        exact={
            "fort knox": [candidate("2832", "Fort Knox")],
            "unit linked": [candidate("7698", "Unit Linked")],
        },
    )

    result = await resolver.resolve_products("сравни Fort Knox с Unit Linked")

    assert result.status == "resolved"
    assert [item.product_code for item in result.items] == ["2832", "7698"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_products_keeps_exact_name_with_conjunction_whole() -> None:
    resolver = FakeProductResolver(
        exact={
            "жизнь и здоровье": [candidate("1001", "Жизнь и здоровье")],
        },
    )

    result = await resolver.resolve_products("покажи Жизнь и здоровье")

    assert result.status == "resolved"
    assert len(result.items) == 1
    assert result.items[0].product_code == "1001"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_keeps_exact_name_with_conjunction_whole() -> None:
    resolver = FakeProductResolver(
        exact={
            "жизнь и здоровье": [candidate("1001", "Жизнь и здоровье")],
        },
    )

    result = await resolver.resolve_product_filter("покажи Жизнь и здоровье")

    assert result.status == "resolved"
    assert result.product_codes == ["1001"]
    assert result.unmatched_terms == [] or result.unmatched_terms is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_returns_multiple_candidates() -> None:
    resolver = FakeProductResolver(
        tokens={
            "fort knox": [
                candidate("2832", "Fort Knox 3 месяца"),
                candidate("2867", "Fort Knox 6 месяцев"),
            ]
        },
    )

    result = await resolver.resolve_product_filter("покажи список продуктов Fort Knox")

    assert result.status == "resolved"
    assert result.product_codes == ["2832", "2867"]
    assert [item.product_code for item in result.products or []] == ["2832", "2867"]
    assert result.matched_terms == ["fort knox"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_rejects_single_property_token_match() -> None:
    resolver = FakeProductResolver(
        tokens={
            "активные": [
                candidate(
                    "7695",
                    "Юнит Линк Активные облигации",
                    is_active=ACTIVE_PRODUCT_STATUS,
                )
            ]
        },
    )

    result = await resolver.resolve_product_filter("активные продукты")

    assert result.status == "not_found"
    assert result.product_codes == [] or result.product_codes is None
    assert result.products == [] or result.products is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_stops_after_successful_exact_stage() -> None:
    resolver = FakeProductResolver(
        exact={"8914": [candidate("8914", "Fort Knox 1 год")]},
    )

    result = await resolver.resolve_product_filter("8914")

    assert result.status == "resolved"
    assert resolver.search_calls == [("exact", "8914")]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_applies_duplicate_code_status_preference() -> None:
    duplicate_code_candidates = [
        candidate("8914", "Fort Knox 1 год", is_active="Архивный"),
        candidate(
            "8914",
            "Фиксированный доход 1 год",
            is_active="Действующий",
        ),
    ]
    resolver = FakeProductResolver(exact={"8914": duplicate_code_candidates})

    default_result = await resolver.resolve_product_filter("8914")
    archived_result = await resolver.resolve_product_filter("архивный 8914")

    assert [item.canonical_name for item in default_result.products or []] == [
        "Фиксированный доход 1 год"
    ]
    assert [item.canonical_name for item in archived_result.products or []] == [
        "Fort Knox 1 год"
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_checks_active_fuzzy_before_archive_fallback() -> None:
    resolver = FakeProductResolver(
        exact={
            "unit linked": [
                candidate("7698", "Unit Linked", is_active=ARCHIVED_PRODUCT_STATUS)
            ]
        },
        fuzzy={
            "unit linked": [
                candidate(
                    "9001",
                    "Unit Linked Active",
                    score=0.9,
                    is_active=ACTIVE_PRODUCT_STATUS,
                )
            ]
        },
    )

    result = await resolver.resolve_product_filter("Unit Linked")

    assert result.status == "resolved"
    assert [item.product_code for item in result.products or []] == ["9001"]
    assert [item.is_active for item in result.products or []] == [
        ACTIVE_PRODUCT_STATUS
    ]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_unions_multiple_product_mentions() -> None:
    resolver = FakeProductResolver(
        tokens={
            "fort knox": [
                candidate("8914", "Fort Knox 1 год"),
                candidate("8837", "Fort Knox 3 года"),
            ],
            "защищенный капитал": [
                candidate("8885", "Защищенный капитал 5 лет"),
                candidate("8916", "Защищенный капитал 2 года"),
            ],
        },
    )

    result = await resolver.resolve_product_filter("Fort Knox и Защищенный капитал")

    assert result.status == "resolved"
    assert result.product_codes == ["8914", "8837", "8885", "8916"]
    assert result.matched_terms == ["fort knox", "защищенный капитал"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_reports_partial_multi_match() -> None:
    resolver = FakeProductResolver(
        tokens={
            "защищенный капитал": [
                candidate("8885", "Защищенный капитал 5 лет"),
            ],
        },
    )

    result = await resolver.resolve_product_filter("Fort Knox и Защищенный капитал")

    assert result.status == "partial"
    assert result.product_codes == ["8885"]
    assert result.matched_terms == ["защищенный капитал"]
    assert result.unmatched_terms == ["fort knox"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_ignores_status_modifier_for_name_match() -> None:
    resolver = FakeProductResolver(
        tokens={
            "fort knox": [
                candidate("8914", "Fort Knox 1 год"),
            ],
        },
    )

    result = await resolver.resolve_product_filter("архивные Fort Knox")

    assert result.status == "resolved"
    assert result.product_codes == ["8914"]
    assert result.matched_terms == ["fort knox"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_unions_text_and_product_codes() -> None:
    """Фраза с кодами не должна рекурсивно извлекать саму себя (RecursionError)."""
    resolver = FakeProductResolver(
        exact={
            "8965": [candidate("8965", "Product A")],
            "7698": [candidate("7698", "Product B")],
        },
        tokens={
            "архивные бандлы": [candidate("1000", "Архивный бандл")],
        },
    )

    result = await resolver.resolve_product_filter("архивные бандлы 8965 7698")

    assert result.status == "resolved"
    assert result.product_codes == ["1000", "8965", "7698"]


@pytest.mark.unit
def test_extract_product_mentions_strips_codes_from_text_parts() -> None:
    mentions = ProductResolverService.extract_product_mentions(
        "архивные бандлы 8965 7698"
    )

    assert mentions == ["архивные бандлы", "8965", "7698"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_does_not_stop_on_combined_fuzzy_match() -> None:
    resolver = FakeProductResolver(
        fuzzy={
            "fort knox защищенный капитал": [
                candidate("8885", "Защищенный капитал 5 лет", score=0.54),
            ],
        },
        tokens={
            "fort knox": [
                candidate("8914", "Fort Knox 1 год"),
            ],
            "защищенный капитал": [
                candidate("8885", "Защищенный капитал 5 лет"),
            ],
        },
    )

    result = await resolver.resolve_product_filter("Fort Knox и Защищенный капитал")

    assert result.status == "resolved"
    assert result.product_codes == ["8914", "8885"]
    assert result.matched_terms == ["fort knox", "защищенный капитал"]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_returns_not_found_for_empty_matches() -> None:
    resolver = FakeProductResolver()

    result = await resolver.resolve_product_filter("неизвестный продукт")

    assert result.status == "not_found"
    assert result.product_codes == [] or result.product_codes is None


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_filter_returns_error_on_search_failure() -> None:
    class BrokenResolver(FakeProductResolver):
        async def _search_exact(
            self,
            query: str,
            product_status: str | None = None,
        ) -> list[ProductCandidate]:
            raise RuntimeError("db down")

    resolver = BrokenResolver()

    result = await resolver.resolve_product_filter("Fort Knox")

    assert result.status == "error"
    assert result.error == "RuntimeError"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_returns_error_on_search_failure() -> None:
    class BrokenResolver(FakeProductResolver):
        async def _search_exact(
            self,
            query: str,
            product_status: str | None = None,
        ) -> list[ProductCandidate]:
            raise RuntimeError("db down")

    resolver = BrokenResolver()

    result = await resolver.resolve_product("Fort Knox")

    assert result.status == "error"
    assert result.error == "RuntimeError"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_search_queries_filter_status_before_limit_30(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RecordingPool:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def fetch(self, sql: str, *args: object) -> list[object]:
            self.calls.append((sql, args))
            return []

    pool = RecordingPool()
    resolver = ProductResolverService(database_url="postgresql://unused")

    async def get_pool() -> RecordingPool:
        return pool

    monkeypatch.setattr(resolver, "_get_pool", get_pool)

    await resolver._search_exact("8914", ACTIVE_PRODUCT_STATUS)
    await resolver._search_tokens("fort knox", ACTIVE_PRODUCT_STATUS)
    await resolver._search_fuzzy("fort knox", ACTIVE_PRODUCT_STATUS)

    assert len(pool.calls) == 3
    for sql, args in pool.calls:
        assert "AND is_active = $" in sql
        assert "PARTITION BY product_code, canonical_name, is_active" in sql
        assert "LIMIT 30" in sql
        assert sql.index("AND is_active = $") < sql.index("LIMIT 30")
        assert args[-1] == ACTIVE_PRODUCT_STATUS
    assert pool.calls[1][1][0] == "% fort %"
    assert pool.calls[1][1][1] == "% форт %"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_get_pool_creates_only_one_pool_for_concurrent_calls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created_pools: list[object] = []
    pool = object()

    async def create_pool(database_url: str) -> object:
        assert database_url == "postgresql://unused"
        await asyncio.sleep(0)
        created_pools.append(pool)
        return pool

    monkeypatch.setattr(product_resolver_module.asyncpg, "create_pool", create_pool)
    resolver = ProductResolverService(database_url="postgresql://unused")

    first, second = await asyncio.gather(resolver._get_pool(), resolver._get_pool())

    assert first is pool
    assert second is pool
    assert created_pools == [pool]


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_prefers_exact_match_over_token_matches() -> None:
    resolver = FakeProductResolver(
        exact={"форт нокс": [candidate("8841", "Fort Knox 3 месяца")]},
        tokens={
            "форт нокс": [
                candidate("8841", "Fort Knox 3 месяца"),
                candidate("8958", "Bundle Fort Knox 3+12 месяцев"),
                candidate("2867", "Bundle Fort Knox 3+36 месяцев"),
            ]
        },
    )

    result = await resolver.resolve_product("покажи продукты форт нокс")

    assert result.status == "resolved"
    assert result.product_code == "8841"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "expected_groups"),
    [
        ("какие есть форт ноксы", [{"форт", "fort"}, {"ноксы", "knox"}]),
        ("какие есть форт ноксов", [{"форт", "fort"}, {"ноксов", "knox", "нокс"}]),
        ("покажи продукты альфа кидс", [{"альфа", "alfa", "alpha"}, {"кидс", "kids"}]),
        ("покажи все бандлы", [{"бандлы", "bundle", "bundl", "бандл"}]),
        ("покажи список продуктов бандлов", [{"бандлов", "bundle", "bundl", "бандл"}]),
    ],
)
def test_token_alternative_groups_keep_query_words_as_or_groups(
    query: str,
    expected_groups: list[set[str]],
) -> None:
    groups = [set(group) for group in ProductResolverService._token_alternative_groups(query)]

    assert len(groups) == len(expected_groups)
    for group, expected in zip(groups, expected_groups):
        assert expected <= group
