import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.rrf import reciprocal_rank_fusion  # noqa: E402


@pytest.mark.unit
def test_rrf_prefers_items_ranked_high_in_both_lists() -> None:
    fused = reciprocal_rank_fusion(
        [
            ["a", "b", "c"],
            ["b", "x", "y"],
        ],
        k=60,
    )
    # b appears in both lists with strong ranks; a only in the first list.
    assert fused[0][0] == "b"
    assert fused[0][1] > fused[1][1]


@pytest.mark.unit
def test_rrf_includes_single_list_only_id() -> None:
    fused = reciprocal_rank_fusion([["x", "y"]], k=60)
    assert [x for x, _ in fused] == ["x", "y"]
