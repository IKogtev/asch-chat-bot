"""Unit tests for kb_search search_profile presets."""

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "search_profile", ROOT / "utils" / "search_profile.py"
)
assert _spec is not None and _spec.loader is not None
_search_profile = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = _search_profile
_spec.loader.exec_module(_search_profile)
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
        ("faq_search", "faq_search"),
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
        "KB_SEARCH_MODE_FAQ_SEARCH",
        "KB_RRF_K_FAQ_SEARCH",
        "KB_CANDIDATE_MULT_FAQ_SEARCH",
    ):
        monkeypatch.delenv(key, raising=False)

    default_cfg = search_profile_config("default")
    doc_cfg = search_profile_config("doc_search")
    answer_cfg = search_profile_config("kb_answer")
    faq_cfg = search_profile_config("faq_search")

    assert default_cfg.search_mode == "dense"
    assert default_cfg.rrf_k == 60
    assert default_cfg.candidate_mult == 100

    assert doc_cfg.search_mode == "hybrid"
    assert doc_cfg.rrf_k == 40
    assert doc_cfg.candidate_mult == 120

    assert answer_cfg.search_mode == "hybrid"
    assert answer_cfg.rrf_k == 60
    assert answer_cfg.candidate_mult == 10

    assert faq_cfg.search_mode == "dense"
    assert faq_cfg.rrf_k == 10
    assert faq_cfg.candidate_mult == 10


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

    assert search_mode_for_profile("kb_answer") == "hybrid"


@pytest.mark.unit
def test_hybrid_rrf_params_for_profile_matches_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KB_RRF_K_DOC_SEARCH", raising=False)
    monkeypatch.delenv("KB_CANDIDATE_MULT_DOC_SEARCH", raising=False)

    assert hybrid_rrf_params_for_profile("doc_search") == (40, 120)


@pytest.mark.unit
def test_faq_search_profile_uses_faq_search_preset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("KB_SEARCH_MODE_FAQ_SEARCH", raising=False)
    assert search_profile_config("faq_search").search_mode == "dense"

    monkeypatch.setenv("KB_SEARCH_MODE_FAQ_SEARCH", "hybrid")
    cfg = search_profile_config("faq_search")
    assert cfg.search_mode == "hybrid"
    assert cfg.rrf_k == 10
    assert cfg.candidate_mult == 10
