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
