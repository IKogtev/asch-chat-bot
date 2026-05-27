"""
Пресеты вызова kb_search по аргументу `search_profile`.

Каждый профиль задаётся переменными окружения:
  - search_mode: hybrid | dense
  - rrf_k, candidate_mult — для гибридного RRF (читаются всегда, применяются при search_mode=hybrid)

Профиль default:
  KB_DEFAULT_SEARCH_MODE, KB_HYBRID_RRF_K, KB_HYBRID_CANDIDATE_MULT
Профиль doc_search:
  KB_SEARCH_MODE_DOC_SEARCH, KB_RRF_K_DOC_SEARCH, KB_CANDIDATE_MULT_DOC_SEARCH
Профиль kb_answer:
  KB_SEARCH_MODE_ANSWER, KB_RRF_K_ANSWER, KB_CANDIDATE_MULT_ANSWER
Профиль faq_search:
  KB_SEARCH_MODE_FAQ_SEARCH, KB_RRF_K_FAQ_SEARCH, KB_CANDIDATE_MULT_FAQ_SEARCH
"""

from __future__ import annotations

import os
from dataclasses import dataclass

ALLOWED_SEARCH_PROFILES = frozenset({"default", "doc_search", "kb_answer", "faq_search"})
_VALID_SEARCH_MODES = frozenset({"hybrid", "dense"})

PROFILE_SEARCH_MODE_ENV: dict[str, tuple[str, str]] = {
    "default": ("KB_DEFAULT_SEARCH_MODE", "dense"),
    "doc_search": ("KB_SEARCH_MODE_DOC_SEARCH", "hybrid"),
    "kb_answer": ("KB_SEARCH_MODE_ANSWER", "hybrid"),
    "faq_search": ("KB_SEARCH_MODE_FAQ_SEARCH", "dense"),
}

PROFILE_RRF_K_ENV: dict[str, tuple[str, str]] = {
    "default": ("KB_HYBRID_RRF_K", "60"),
    "doc_search": ("KB_RRF_K_DOC_SEARCH", "40"),
    "kb_answer": ("KB_RRF_K_ANSWER", "60"),
    "faq_search": ("KB_RRF_K_FAQ_SEARCH", "10"),
}

PROFILE_CANDIDATE_MULT_ENV: dict[str, tuple[str, str]] = {
    "default": ("KB_HYBRID_CANDIDATE_MULT", "100"),
    "doc_search": ("KB_CANDIDATE_MULT_DOC_SEARCH", "120"),
    "kb_answer": ("KB_CANDIDATE_MULT_ANSWER", "10"),
    "faq_search": ("KB_CANDIDATE_MULT_FAQ_SEARCH", "10"),
}


@dataclass(frozen=True)
class SearchProfileConfig:
    search_mode: str
    rrf_k: int
    candidate_mult: int


def normalize_search_profile(raw: str | None) -> str:
    key = (raw or "default").strip().lower()
    return key if key in ALLOWED_SEARCH_PROFILES else "default"


def _env_search_mode(env_name: str, default: str) -> str:
    fallback = default.strip().lower()
    if fallback not in _VALID_SEARCH_MODES:
        fallback = "hybrid"
    raw = os.getenv(env_name, default).strip().lower()
    return raw if raw in _VALID_SEARCH_MODES else fallback


def search_profile_config(profile: str) -> SearchProfileConfig:
    """search_mode, rrf_k и candidate_mult для профиля — все из env."""
    key = normalize_search_profile(profile)
    sm_env, sm_default = PROFILE_SEARCH_MODE_ENV.get(
        key, PROFILE_SEARCH_MODE_ENV["default"]
    )
    rrf_env, rrf_default = PROFILE_RRF_K_ENV.get(key, PROFILE_RRF_K_ENV["default"])
    mult_env, mult_default = PROFILE_CANDIDATE_MULT_ENV.get(
        key, PROFILE_CANDIDATE_MULT_ENV["default"]
    )
    return SearchProfileConfig(
        search_mode=_env_search_mode(sm_env, sm_default),
        rrf_k=int(os.getenv(rrf_env, rrf_default)),
        candidate_mult=int(os.getenv(mult_env, mult_default)),
    )


def search_mode_for_profile(profile: str) -> str:
    return search_profile_config(profile).search_mode


def hybrid_rrf_params_for_profile(profile: str) -> tuple[int, int]:
    cfg = search_profile_config(profile)
    return cfg.rrf_k, cfg.candidate_mult
