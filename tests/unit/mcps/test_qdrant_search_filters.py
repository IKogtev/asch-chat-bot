"""Unit tests for doc_search archive exclusion in Qdrant filters."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "qdrant_search_filters", ROOT / "utils" / "qdrant_search_filters.py"
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

archive_explicitly_requested = _mod.archive_explicitly_requested
build_hybrid_qdrant_filter = _mod.build_hybrid_qdrant_filter
doc_search_archive_section = _mod.doc_search_archive_section


@pytest.mark.unit
def test_archive_explicitly_requested_by_section_path() -> None:
    assert archive_explicitly_requested({"section_path": "5 Архив"}, "5 Архив") is True
    assert archive_explicitly_requested({"section_path": "2 В фокусе АСЖ"}, "5 Архив") is False
    assert archive_explicitly_requested(None, "5 Архив") is False
    assert archive_explicitly_requested({"section_path": ["5 Архив", "Fort Knox"]}, "5 Архив") is True


@pytest.mark.unit
def test_doc_search_default_excludes_archive_in_must_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_SEARCH_ARCHIVE_SECTION", "5 Архив")
    qf = build_hybrid_qdrant_filter({"kb_id": "kb-1"}, "doc_search")

    assert len(qf.must) == 1
    assert qf.must[0].key == "kb_id"
    assert len(qf.must_not) == 2
    archive_excludes = [c for c in qf.must_not if c.key == "section_path"]
    assert len(archive_excludes) == 1
    assert archive_excludes[0].match.value == "5 Архив"


@pytest.mark.unit
def test_doc_search_archive_filter_skips_archive_must_not(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_SEARCH_ARCHIVE_SECTION", "5 Архив")
    qf = build_hybrid_qdrant_filter(
        {"section_path": "5 Архив"},
        "doc_search",
    )

    assert len(qf.must) == 1
    assert qf.must[0].key == "section_path"
    assert qf.must[0].match.value == "5 Архив"
    assert len(qf.must_not) == 1
    assert qf.must_not[0].key == "__type__"


@pytest.mark.unit
def test_non_doc_search_profile_does_not_exclude_archive() -> None:
    qf = build_hybrid_qdrant_filter(None, "kb_answer")

    assert qf.must == []
    assert len(qf.must_not) == 1
    assert qf.must_not[0].key == "__type__"


@pytest.mark.unit
def test_doc_search_archive_section_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_SEARCH_ARCHIVE_SECTION", "Archive Root")
    assert doc_search_archive_section() == "Archive Root"
