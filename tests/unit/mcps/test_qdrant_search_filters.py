"""Unit tests for doc_search archive exclusion in Qdrant filters."""
import sys
import types
import importlib.util
from pathlib import Path

import pytest

mock_models = types.ModuleType('qdrant_client.http.models')
class MockMatchValue:
    def __init__(self, value):
        self.value = value

class MockFieldCondition:
    def __init__(self, key, match):
        self.key = key
        self.match = match

class MockFilter:
    def __init__(self, must=None, must_not=None, should=None, min_should=None):
        # Сохраняем переданные списки как есть, чтобы тесты могли их проверять
        self.must = must if must is not None else []
        self.must_not = must_not if must_not is not None else []

mock_models.MatchValue = MockMatchValue
mock_models.FieldCondition = MockFieldCondition
mock_models.Filter = MockFilter

# Регистрируем наши заглушки в sys.modules ДО импорта целевого модуля
sys.modules['qdrant_client'] = types.ModuleType('qdrant_client')
sys.modules['qdrant_client.http'] = types.ModuleType('qdrant_client.http')
sys.modules['qdrant_client.http.models'] = mock_models

ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "qdrant_search_filters", ROOT / "utils" / "qdrant_search_filters.py"
)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

archive_explicitly_requested = _mod.archive_explicitly_requested
build_hybrid_qdrant_filter = _mod.build_hybrid_qdrant_filter
describe_hybrid_qdrant_filter = _mod.describe_hybrid_qdrant_filter
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
    assert qf.must[0].match.value == "kb-1"
    assert len(qf.must_not) == 2
    archive_excludes = [c for c in qf.must_not if c.key == "section_path"]
    assert len(archive_excludes) == 1
    assert archive_excludes[0].match.value == "5 Архив"

    type_excludes = [c for c in qf.must_not if c.key == "type"]
    assert len(type_excludes) == 1
    assert type_excludes[0].match.value == "collection_meta"


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
    assert qf.must_not[0].key == "type"
    assert qf.must_not[0].match.value == "collection_meta"


@pytest.mark.unit
def test_non_doc_search_profile_does_not_exclude_archive() -> None:
    qf = build_hybrid_qdrant_filter(None, "kb_answer")
    assert qf.must == []
    assert len(qf.must_not) == 1
    assert qf.must_not[0].key == "type"
    assert qf.must_not[0].match.value == "collection_meta"


@pytest.mark.unit
def test_doc_search_archive_section_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_SEARCH_ARCHIVE_SECTION", "Archive Root")
    assert doc_search_archive_section() == "Archive Root"


@pytest.mark.unit
def test_describe_hybrid_qdrant_filter_doc_search_archive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DOC_SEARCH_ARCHIVE_SECTION", "5 Архив")
    summary = describe_hybrid_qdrant_filter(None, "doc_search")

    assert summary["search_profile"] == "doc_search"
    assert summary["archive"]["configured_section"] == "5 Архив"
    assert summary["archive"]["excluded_from_search"] is True
    assert summary["archive"]["must_not_section_path"] == "5 Архив"
    must_not_items = summary.get("must_not", [])
    section_path_item = next((item for item in must_not_items if item.get("key") == "section_path"), None)
    
    assert section_path_item is not None, f"section_path not found in must_not: {must_not_items}"
    assert section_path_item["match"] == "5 Архив"
