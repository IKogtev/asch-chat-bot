"""Unit tests for Indexer hybrid Qdrant search (RRF and dense-only)."""

import ast
import importlib.util
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest

from kbsearch_import_helper import load_kbsearch_module

ROOT = Path(__file__).resolve().parents[3]


def load_utils_module(relative_path: str, module_name: str):
    path = ROOT / "utils" / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


INDEXER_PATH = ROOT / "utils" / "indexer.py"

_rrf = load_utils_module("rrf.py", "kbsearch_rrf_for_indexer")
_search_profile = load_utils_module("search_profile.py", "kbsearch_sp_for_indexer")
_qdrant_hybrid = load_utils_module("qdrant_hybrid.py", "kbsearch_qh_for_indexer")
_qdrant_filters = load_utils_module("qdrant_search_filters.py", "kbsearch_qsf_for_indexer")

_hybrid_mode_stub = {"fn": _qdrant_hybrid.collection_hybrid_mode}
_rrf_params_stub = {"fn": _search_profile.hybrid_rrf_params_for_profile}


def collection_hybrid_mode(client, name: str) -> str:
    return _hybrid_mode_stub["fn"](client, name)


def hybrid_rrf_params_for_profile(profile: str) -> tuple[int, int]:
    return _rrf_params_stub["fn"](profile)


class _FakeMatchValue:
    def __init__(self, value):
        self.value = value


class _FakeFieldCondition:
    def __init__(self, key, match):
        self.key = key
        self.match = match


class _FakeFilter:
    def __init__(self, must=None, must_not=None):
        self.must = must or []
        self.must_not = must_not or []


def _load_indexer_class():
    tree = ast.parse(INDEXER_PATH.read_text(encoding="utf-8"), filename=str(INDEXER_PATH))
    selected = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "Indexer"]
    namespace = {
        "__builtins__": __builtins__,
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "Any": Any,
        "dataclass": lambda cls=None, **kw: (lambda c: c) if cls is None else cls,
        "field": lambda *a, **k: None,
        "os": types.SimpleNamespace(getenv=lambda *a, **k: k[-1] if k else ""),
        "Path": Path,
        "datetime": types.SimpleNamespace,
        "IndexerConfig": object,
        "QdrantReadIndexer": object,
        "VectorStoreIndex": object,
        "Settings": object,
        "QdrantVectorStore": object,
        "qdrant_client": object,
        "models": object,
        "Filter": _FakeFilter,
        "FieldCondition": _FakeFieldCondition,
        "MatchValue": _FakeMatchValue,
        "VectorParams": object,
        "Distance": object,
        "OptimizersConfigDiff": object,
        "PointStruct": object,
        "RemoteEmbedding": object,
        "chunk_id_to_uuid": lambda x: x,
        "meta_id_for_collection": lambda x: x,
        "setup_logger": lambda *a, **k: types.SimpleNamespace(info=lambda *x, **y: None),
        "reciprocal_rank_fusion": _rrf.reciprocal_rank_fusion,
        "hybrid_rrf_params_for_profile": hybrid_rrf_params_for_profile,
        "DENSE_VECTOR_NAME": _qdrant_hybrid.DENSE_VECTOR_NAME,
        "SPARSE_VECTOR_NAME": _qdrant_hybrid.SPARSE_VECTOR_NAME,
        "bm25_document_text": _qdrant_hybrid.bm25_document_text,
        "collection_hybrid_mode": collection_hybrid_mode,
        "hybrid_collection_create_kwargs": _qdrant_hybrid.hybrid_collection_create_kwargs,
        "meta_point_vectors": _qdrant_hybrid.meta_point_vectors,
        "sparse_embedding_to_vector": _qdrant_hybrid.sparse_embedding_to_vector,
        "build_hybrid_qdrant_filter": _qdrant_filters.build_hybrid_qdrant_filter,
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(INDEXER_PATH), "exec"), namespace)
    return namespace["Indexer"]


Indexer = _load_indexer_class()
DENSE_VECTOR_NAME = _qdrant_hybrid.DENSE_VECTOR_NAME
SPARSE_VECTOR_NAME = _qdrant_hybrid.SPARSE_VECTOR_NAME


def _fake_hit(point_id: str, score: float, text: str = "chunk"):
    return types.SimpleNamespace(
        id=point_id,
        score=score,
        payload={"text": text, "document_id": point_id, "source": f"{point_id}.pdf"},
    )


def _make_hybrid_indexer() -> Indexer:
    indexer = Indexer.__new__(Indexer)
    indexer.cfg = types.SimpleNamespace(use_qdrant=True, qdrant_alias="active", qdrant_collection="kb")
    indexer.logger = types.SimpleNamespace(info=lambda *a, **k: None, debug=lambda *a, **k: None)
    indexer.embed_model = types.SimpleNamespace(
        get_text_embedding_batch=lambda texts: [[0.1, 0.2, 0.3]],
    )
    sparse_emb = types.SimpleNamespace(indices=[1], values=[0.9])

    class FakeSparseModel:
        def query_embed(self, q: str):
            return [sparse_emb]

    indexer._sparse_embedder = FakeSparseModel()
    indexer._resolve_search_collection_name = lambda collection: collection or "kb_hybrid"
    return indexer


@pytest.mark.unit
def test_hybrid_search_enabled_true_for_hybrid_collection() -> None:
    _hybrid_mode_stub["fn"] = lambda client, name: "hybrid"
    indexer = _make_hybrid_indexer()
    indexer._get_qdrant_client = lambda: object()

    assert indexer.hybrid_search_enabled("kb_hybrid") is True


@pytest.mark.unit
def test_hybrid_search_enabled_false_when_qdrant_disabled() -> None:
    indexer = _make_hybrid_indexer()
    indexer.cfg.use_qdrant = False

    assert indexer.hybrid_search_enabled("kb_hybrid") is False


@pytest.mark.unit
def test_hybrid_search_rrf_merges_lists_with_profile_rrf_k() -> None:
    _hybrid_mode_stub["fn"] = lambda client, name: "hybrid"
    _rrf_params_stub["fn"] = lambda profile: (40, 10)

    indexer = _make_hybrid_indexer()
    calls: list[dict] = []

    def fake_query_points(**kwargs):
        calls.append(kwargs)
        if kwargs.get("using") == DENSE_VECTOR_NAME:
            points = [_fake_hit("a", 0.9), _fake_hit("b", 0.8)]
        else:
            points = [_fake_hit("b", 0.7), _fake_hit("c", 0.6)]
        return types.SimpleNamespace(points=points)

    indexer._get_qdrant_client = lambda: types.SimpleNamespace(query_points=fake_query_points)

    results = indexer.hybrid_search_rrf(
        "fort knox",
        "kb_hybrid",
        filters={"kb_id": "kb-1"},
        top_k=2,
        search_profile="doc_search",
    )

    assert len(results) == 2
    assert results[0]["metadata"]["document_id"] == "b"
    assert results[0]["dense_score"] == 0.8
    assert results[0]["sparse_score"] == 0.7
    assert results[0]["rrf_score"] == results[0]["score"]
    assert len(calls) == 2
    assert calls[0]["limit"] == max(2 * 10, 100)
    assert calls[0]["using"] == DENSE_VECTOR_NAME
    assert calls[1]["using"] == SPARSE_VECTOR_NAME


@pytest.mark.unit
def test_hybrid_search_rrf_doc_search_excludes_archive_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_SEARCH_ARCHIVE_SECTION", "5 Архив")
    _hybrid_mode_stub["fn"] = lambda client, name: "hybrid"
    _rrf_params_stub["fn"] = lambda profile: (40, 10)

    indexer = _make_hybrid_indexer()
    calls: list[dict] = []

    def fake_query_points(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(points=[_fake_hit("a", 0.9)])

    indexer._get_qdrant_client = lambda: types.SimpleNamespace(query_points=fake_query_points)

    indexer.hybrid_search_rrf("fort knox", "kb_hybrid", None, top_k=1, search_profile="doc_search")

    qf = calls[0]["query_filter"]
    archive_excludes = [c for c in qf.must_not if c.key == "section_path"]
    assert len(archive_excludes) == 1
    assert archive_excludes[0].match.value == "5 Архив"


@pytest.mark.unit
def test_hybrid_search_rrf_doc_search_archive_filter_no_must_not_archive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DOC_SEARCH_ARCHIVE_SECTION", "5 Архив")
    _hybrid_mode_stub["fn"] = lambda client, name: "hybrid"
    _rrf_params_stub["fn"] = lambda profile: (40, 10)

    indexer = _make_hybrid_indexer()
    calls: list[dict] = []

    def fake_query_points(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(points=[_fake_hit("a", 0.9)])

    indexer._get_qdrant_client = lambda: types.SimpleNamespace(query_points=fake_query_points)

    indexer.hybrid_search_rrf(
        "fort knox",
        "kb_hybrid",
        filters={"section_path": "5 Архив"},
        top_k=1,
        search_profile="doc_search",
    )

    qf = calls[0]["query_filter"]
    assert len([c for c in qf.must_not if c.key == "section_path"]) == 0
    assert qf.must[0].key == "section_path"


@pytest.mark.unit
def test_hybrid_search_rrf_raises_for_legacy_collection() -> None:
    _hybrid_mode_stub["fn"] = lambda client, name: "legacy"
    indexer = _make_hybrid_indexer()
    indexer._get_qdrant_client = lambda: object()

    with pytest.raises(ValueError, match="not hybrid-indexed"):
        indexer.hybrid_search_rrf("q", "legacy_coll", None, top_k=5)


@pytest.mark.unit
def test_hybrid_dense_search_uses_only_dense_vector() -> None:
    _hybrid_mode_stub["fn"] = lambda client, name: "hybrid"
    indexer = _make_hybrid_indexer()
    calls: list[dict] = []

    def fake_query_points(**kwargs):
        calls.append(kwargs)
        return types.SimpleNamespace(
            points=[_fake_hit("x", 0.95, text="answer fragment")],
        )

    indexer._get_qdrant_client = lambda: types.SimpleNamespace(query_points=fake_query_points)

    results = indexer.hybrid_dense_search("question", "kb_hybrid", None, top_k=3)

    assert len(results) == 1
    assert results[0]["score"] == 0.95
    assert results[0]["sparse_score"] is None
    assert len(calls) == 1
    assert calls[0]["using"] == DENSE_VECTOR_NAME
