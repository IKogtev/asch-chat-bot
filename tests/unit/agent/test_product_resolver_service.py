from __future__ import annotations

from typing import Dict, List

import pytest

from agent.product_resolver_service import (
    ProductCandidate,
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

    async def _search_exact(self, query: str) -> list[ProductCandidate]:
        return self.exact.get(query, [])

    async def _search_tokens(self, query: str) -> list[ProductCandidate]:
        return self.tokens.get(query, [])

    async def _search_fuzzy(self, query: str) -> list[ProductCandidate]:
        return self.fuzzy.get(query, [])


def candidate(code: str, name: str, *, score: float = 1.0, priority: int = 100) -> ProductCandidate:
    return ProductCandidate(
        product_code=code,
        canonical_name=name,
        alias=name,
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
        async def _search_exact(self, query: str) -> list[ProductCandidate]:
            raise RuntimeError("db down")

    resolver = BrokenResolver()

    result = await resolver.resolve_product_filter("Fort Knox")

    assert result.status == "error"
    assert result.error == "RuntimeError"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_returns_error_on_search_failure() -> None:
    class BrokenResolver(FakeProductResolver):
        async def _search_exact(self, query: str) -> list[ProductCandidate]:
            raise RuntimeError("db down")

    resolver = BrokenResolver()

    result = await resolver.resolve_product("Fort Knox")

    assert result.status == "error"
    assert result.error == "RuntimeError"


@pytest.mark.unit
def test_tokenize_product_text_adds_translit_and_known_word_variants() -> None:
    tokens = ProductResolverService.tokenize_product_text("life плюс")

    assert "life" in tokens
    assert "лайф" in tokens
    assert "plus" in tokens


@pytest.mark.unit
@pytest.mark.asyncio
async def test_resolve_product_combines_exact_and_token_matches() -> None:
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

    assert result.status == "ambiguous"
    assert [item.product_code for item in result.options or []] == ["8841", "8958", "2867"]


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


@pytest.mark.unit
def test_tokenize_product_text_adds_bundle_fort_knox_plural_variants() -> None:
    tokens = ProductResolverService.tokenize_product_text("Bundle Fort Knox 3+12 месяцев")

    assert "bundle" in tokens
    assert "бандл" in tokens
    assert "бандлы" in tokens
    assert "fort" in tokens
    assert "форт" in tokens
    assert "knox" in tokens
    assert "нокс" in tokens
    assert "ноксы" in tokens


@pytest.mark.unit
def test_tokenize_product_text_adds_alfa_kids_variants() -> None:
    tokens = ProductResolverService.tokenize_product_text("Альфа Kids+ 5 лет")

    assert "альфа" in tokens
    assert "alfa" in tokens
    assert "alpha" in tokens
    assert "kids" in tokens
    assert "кидс" in tokens
