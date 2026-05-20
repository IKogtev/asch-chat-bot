"""Unit tests for Qdrant hybrid collection helpers."""

import types

import pytest
from qdrant_client.models import Distance, SparseVector

from kbsearch_import_helper import ensure_kbsearch_app_on_path, load_kbsearch_module

ensure_kbsearch_app_on_path()
_qdrant_hybrid = load_kbsearch_module("utils/qdrant_hybrid.py", "kbsearch_qdrant_hybrid")
DENSE_VECTOR_NAME = _qdrant_hybrid.DENSE_VECTOR_NAME
SPARSE_VECTOR_NAME = _qdrant_hybrid.SPARSE_VECTOR_NAME
bm25_document_text = _qdrant_hybrid.bm25_document_text
collection_hybrid_mode = _qdrant_hybrid.collection_hybrid_mode
hybrid_collection_create_kwargs = _qdrant_hybrid.hybrid_collection_create_kwargs
meta_point_vectors = _qdrant_hybrid.meta_point_vectors
sparse_embedding_to_vector = _qdrant_hybrid.sparse_embedding_to_vector


@pytest.mark.unit
def test_bm25_document_text_combines_chunk_path_and_filename() -> None:
    text = bm25_document_text(
        "тело чанка",
        ["01_Маркетинг", "Fort Knox"],
        "storiz.pdf",
    )

    assert "тело чанка" in text
    assert "Fort Knox" in text
    assert "storiz.pdf" in text


@pytest.mark.unit
def test_bm25_document_text_path_only_for_empty_chunk() -> None:
    text = bm25_document_text(
        "",
        ["products", "Альфа Kids"],
        "presenter.pdf",
    )

    assert text == "products > Альфа Kids > presenter.pdf"


@pytest.mark.unit
def test_bm25_document_text_returns_space_when_no_content() -> None:
    assert bm25_document_text("", [], None) == " "


@pytest.mark.unit
def test_hybrid_collection_create_kwargs_defines_dense_and_sparse() -> None:
    kwargs = hybrid_collection_create_kwargs(128, distance=Distance.COSINE)

    assert DENSE_VECTOR_NAME in kwargs["vectors_config"]
    assert kwargs["vectors_config"][DENSE_VECTOR_NAME].size == 128
    assert SPARSE_VECTOR_NAME in kwargs["sparse_vectors_config"]


@pytest.mark.unit
def test_collection_hybrid_mode_detects_named_vectors() -> None:
    dense_params = types.SimpleNamespace()
    sparse_params = {SPARSE_VECTOR_NAME: object()}
    params = types.SimpleNamespace(
        vectors={DENSE_VECTOR_NAME: dense_params},
        sparse_vectors=sparse_params,
    )
    info = types.SimpleNamespace(config=types.SimpleNamespace(params=params))
    client = types.SimpleNamespace(
        get_collection=lambda collection_name: info,
    )

    assert collection_hybrid_mode(client, "kb_collection") == "hybrid"  # type: ignore[arg-type]


@pytest.mark.unit
def test_collection_hybrid_mode_returns_legacy_without_sparse() -> None:
    params = types.SimpleNamespace(
        vectors={DENSE_VECTOR_NAME: object()},
        sparse_vectors={},
    )
    info = types.SimpleNamespace(config=types.SimpleNamespace(params=params))
    client = types.SimpleNamespace(
        get_collection=lambda collection_name: info,
    )

    assert collection_hybrid_mode(client, "old_collection") == "legacy"  # type: ignore[arg-type]


@pytest.mark.unit
def test_sparse_embedding_to_vector_converts_fastembed_like_object() -> None:
    emb = types.SimpleNamespace(indices=[0, 2], values=[0.5, 0.3])
    vec = sparse_embedding_to_vector(emb)

    assert isinstance(vec, SparseVector)
    assert vec.indices == [0, 2]
    assert vec.values == [0.5, 0.3]


@pytest.mark.unit
def test_sparse_embedding_to_vector_requires_indices_and_values() -> None:
    with pytest.raises(ValueError, match="indices and values"):
        sparse_embedding_to_vector(types.SimpleNamespace())


@pytest.mark.unit
def test_meta_point_vectors_hybrid_has_empty_sparse() -> None:
    vectors = meta_point_vectors(4, "hybrid")

    assert isinstance(vectors, dict)
    assert len(vectors[DENSE_VECTOR_NAME]) == 4
    assert vectors[SPARSE_VECTOR_NAME].indices == []


@pytest.mark.unit
def test_meta_point_vectors_legacy_is_flat_dense() -> None:
    vectors = meta_point_vectors(3, "legacy")

    assert vectors == [0.0, 0.0, 0.0]
