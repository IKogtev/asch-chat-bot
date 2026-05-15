"""Unit tests for kb_search search_profile presets."""

import pytest

from kbsearch_import_helper import ensure_kbsearch_app_on_path, load_kbsearch_module

ensure_kbsearch_app_on_path()
_search_profile = load_kbsearch_module("utils/search_profile.py", "kbsearch_search_profile")
hybrid_rrf_params_for_profile = _search_profile.hybrid_rrf_params_for_profile
normalize_search_profile = _search_profile.normalize_search_profile
search_mode_for_profile = _search_profile.search_mode_for_profile
search_profile_config = _search_profile.search_profile_config


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (None, "default"),
        ("", "default"),
        ("DOC_SEARCH", "doc_search"),
        ("kb_answer", "kb_answer"),
        ("unknown", "default"),
    ],
)
def test_normalize_search_profile(raw: str | None, expected: str) -> None:
    assert normalize_search_profile(raw) == expected


@pytest.mark.unit
def test_search_profile_config_defaults_without_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in (
        "KB_DEFAULT_SEARCH_MODE",
        "KB_HYBRID_RRF_K",
        "KB_HYBRID_CANDIDATE_MULT",
        "KB_SEARCH_MODE_DOC_SEARCH",
        "KB_RRF_K_DOC_SEARCH",
        "KB_CANDIDATE_MULT_DOC_SEARCH",
        "KB_SEARCH_MODE_ANSWER",
        "KB_RRF_K_ANSWER",
        "KB_CANDIDATE_MULT_ANSWER",
    ):
        monkeypatch.delenv(key, raising=False)

    default_cfg = search_profile_config("default")
    doc_cfg = search_profile_config("doc_search")
    answer_cfg = search_profile_config("kb_answer")

    assert default_cfg.search_mode == "hybrid"
    assert default_cfg.rrf_k == 60
    assert default_cfg.candidate_mult == 100

    assert doc_cfg.search_mode == "hybrid"
    assert doc_cfg.rrf_k == 40
    assert doc_cfg.candidate_mult == 120

    assert answer_cfg.search_mode == "dense"
    assert answer_cfg.rrf_k == 60
    assert answer_cfg.candidate_mult == 10


@pytest.mark.unit
def test_search_profile_config_reads_env_overrides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KB_SEARCH_MODE_DOC_SEARCH", "dense")
    monkeypatch.setenv("KB_RRF_K_DOC_SEARCH", "55")
    monkeypatch.setenv("KB_CANDIDATE_MULT_DOC_SEARCH", "80")

    cfg = search_profile_config("doc_search")

    assert cfg.search_mode == "dense"
    assert cfg.rrf_k == 55
    assert cfg.candidate_mult == 80


@pytest.mark.unit
def test_search_profile_config_invalid_mode_falls_back_to_hybrid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("KB_SEARCH_MODE_ANSWER", "invalid-mode")

    assert search_mode_for_profile("kb_answer") == "dense"


@pytest.mark.unit
def test_hybrid_rrf_params_for_profile_matches_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KB_RRF_K_DOC_SEARCH", raising=False)
    monkeypatch.delenv("KB_CANDIDATE_MULT_DOC_SEARCH", raising=False)

    assert hybrid_rrf_params_for_profile("doc_search") == (40, 120)
