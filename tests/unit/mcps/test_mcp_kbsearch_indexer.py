import ast
import types
from pathlib import Path
from typing import Dict, List, Optional

import pytest


def _load_class(file_path: Path, class_name: str, extra_globals: dict):
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace[class_name]


class FakeMatchValue:
    def __init__(self, value):
        self.value = value


class FakeFieldCondition:
    def __init__(self, key, match):
        self.key = key
        self.match = match


class FakeFilter:
    def __init__(self, must=None, must_not=None):
        self.must = must or []
        self.must_not = must_not or []


class FakeQdrantVectorStore:
    last = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.filters = kwargs.get("filters")
        FakeQdrantVectorStore.last = self


class FakeIndex:
    def as_retriever(self, **kwargs):
        return {"retriever_kwargs": kwargs}


class FakeVectorStoreIndex:
    @staticmethod
    def from_vector_store(vector_store, *args, **kwargs):
        return FakeIndex()


Indexer = _load_class(
    Path(__file__).resolve().parents[3] / "mcps" / "mcp-server-kbsearch" / "app" / "utils" / "indexer.py",
    "Indexer",
    {
        "Dict": Dict,
        "List": List,
        "Optional": Optional,
        "IndexerConfig": object,
        "Filter": FakeFilter,
        "FieldCondition": FakeFieldCondition,
        "MatchValue": FakeMatchValue,
        "QdrantVectorStore": FakeQdrantVectorStore,
        "VectorStoreIndex": FakeVectorStoreIndex,
    },
)


def _make_indexer(collection_exists=True):
    indexer = Indexer.__new__(Indexer)
    indexer.collection_meta_type = "collection_meta"
    indexer.cfg = types.SimpleNamespace(similarity_cutoff=0.25)
    indexer.logger = types.SimpleNamespace(info=lambda *args, **kwargs: None)
    indexer.get_active_collection = lambda: "active_collection"
    indexer._get_qdrant_client = lambda: types.SimpleNamespace(
        collection_exists=lambda collection_name: collection_exists
    )
    return indexer


@pytest.mark.unit
def test_get_retriever_for_collection_excludes_collection_meta_without_user_filters() -> None:
    indexer = _make_indexer()

    retriever = indexer.get_retriever_for_collection(None, top_k=7)

    qdrant_filter = FakeQdrantVectorStore.last.filters
    assert retriever == {"retriever_kwargs": {"similarity_top_k": 7, "similarity_cutoff": 0.25}}
    assert qdrant_filter.must == []
    assert len(qdrant_filter.must_not) == 1
    assert qdrant_filter.must_not[0].key == "__type__"
    assert qdrant_filter.must_not[0].match.value == "collection_meta"


@pytest.mark.unit
def test_get_retriever_for_collection_preserves_user_filters_and_excludes_collection_meta() -> None:
    indexer = _make_indexer()

    indexer.get_retriever_for_collection(
        "knowledge_base_collection",
        top_k=5,
        filters={"kb_id": "kb-1", "skip": None},
    )

    qdrant_filter = FakeQdrantVectorStore.last.filters
    assert len(qdrant_filter.must) == 1
    assert qdrant_filter.must[0].key == "kb_id"
    assert qdrant_filter.must[0].match.value == "kb-1"
    assert len(qdrant_filter.must_not) == 1
    assert qdrant_filter.must_not[0].key == "__type__"
    assert qdrant_filter.must_not[0].match.value == "collection_meta"
