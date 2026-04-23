"""Reciprocal Rank Fusion (RRF) for merging ranked lists of point ids."""

from __future__ import annotations

from typing import Hashable, List, MutableMapping, Sequence, Tuple, TypeVar

T = TypeVar("T", bound=Hashable)


def reciprocal_rank_fusion(
    ranked_lists: Sequence[Sequence[T]],
    k: int = 60,
) -> List[Tuple[T, float]]:
    """
    Merge multiple ordered lists of identifiers into a single ranking.

    score(d) = sum_i 1 / (k + rank_i(d)) for lists where d appears.
    """
    scores: MutableMapping[T, float] = {}
    for ranked in ranked_lists:
        for rank, item in enumerate(ranked, start=1):
            scores[item] = scores.get(item, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda x: -x[1])
