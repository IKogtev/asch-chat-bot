"""
Пресеты вызова kb_search по аргументу `search_profile`:

- RRF и ширина выборки (KB_HYBRID_* при None в пресете);
- режим Qdrant hybrid-коллекции: `hybrid` vs `dense` (см. PROFILE_SEARCH_MODE).
"""

from __future__ import annotations

import os
from typing import Any, Mapping, Optional

ALLOWED_SEARCH_PROFILES = frozenset({"default", "doc_search", "kb_answer"})

PROFILE_HYBRID: dict[str, dict[str, Any | None]] = {
    "default": {"rrf_k": None, "candidate_mult": None},
    "doc_search": {"rrf_k": 40, "candidate_mult": 120},
    "kb_answer": {"rrf_k": 60, "candidate_mult": 10},
}

# hybrid = dense + sparse + RRF; dense = только dense-вектор.
# None для профиля = взять KB_DEFAULT_SEARCH_MODE из окружения.
PROFILE_SEARCH_MODE: dict[str, Optional[str]] = {
    "default": None,
    "doc_search": "hybrid",
    "kb_answer": "dense",
}


def normalize_search_profile(raw: str | None) -> str:
    key = (raw or "default").strip().lower()
    return key if key in ALLOWED_SEARCH_PROFILES else "default"


def search_mode_for_profile(profile: str) -> str:
    """hybrid | dense для hybrid-коллекций; для профиля default — из env KB_DEFAULT_SEARCH_MODE."""
    key = normalize_search_profile(profile)
    override = PROFILE_SEARCH_MODE.get(key)
    if override is not None:
        sm = str(override).strip().lower()
        return sm if sm in ("hybrid", "dense") else "hybrid"
    sm = os.getenv("KB_DEFAULT_SEARCH_MODE", "hybrid").strip().lower()
    return sm if sm in ("hybrid", "dense") else "hybrid"


def hybrid_rrf_params_for_profile(profile: str) -> tuple[int, int]:
    """Возвращает (rrf_k, candidate_mult) для hybrid_search_rrf."""
    key = normalize_search_profile(profile)
    preset: Mapping[str, Any] = PROFILE_HYBRID.get(key) or PROFILE_HYBRID["default"]
    rrf = preset.get("rrf_k")
    mult = preset.get("candidate_mult")
    rrf_k = int(rrf if rrf is not None else os.getenv("KB_HYBRID_RRF_K", "60"))
    candidate_mult = int(
        mult if mult is not None else os.getenv("KB_HYBRID_CANDIDATE_MULT", "100")
    )
    return rrf_k, candidate_mult
