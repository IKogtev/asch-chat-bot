"""Re-export search profiles from repo root (single source: utils/search_profile.py)."""

from __future__ import annotations

from utils._shared_utils import load_shared_module

_mod = load_shared_module("search_profile.py", "_shared_search_profile")

ALLOWED_SEARCH_PROFILES = _mod.ALLOWED_SEARCH_PROFILES
SearchProfileConfig = _mod.SearchProfileConfig
hybrid_rrf_params_for_profile = _mod.hybrid_rrf_params_for_profile
normalize_search_profile = _mod.normalize_search_profile
search_mode_for_profile = _mod.search_mode_for_profile
search_profile_config = _mod.search_profile_config

__all__ = [
    "ALLOWED_SEARCH_PROFILES",
    "SearchProfileConfig",
    "hybrid_rrf_params_for_profile",
    "normalize_search_profile",
    "search_mode_for_profile",
    "search_profile_config",
]
