"""Dense + sparse (BM25) hybrid Qdrant collection helpers."""

from __future__ import annotations

from typing import Any, List, Optional, Union

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    Modifier,
    SparseVector,
    SparseVectorParams,
    VectorParams,
)

DENSE_VECTOR_NAME = "dense"
SPARSE_VECTOR_NAME = "lexical"


def bm25_document_text(chunk_text: str, section_path: Optional[List[Any]]) -> str:
    """Text indexed for sparse BM25: chunk body plus folder path for lexical grounding."""
    text = (chunk_text or "").strip()
    if not section_path:
        return text
    path = " > ".join(str(p) for p in section_path if p is not None)
    if not path:
        return text
    if not text:
        return path
    return f"{text}\n\n{path}"


def sparse_embedding_to_vector(emb: Any) -> SparseVector:
    """Convert fastembed sparse output to Qdrant SparseVector."""
    indices = getattr(emb, "indices", None)
    values = getattr(emb, "values", None)
    if indices is None or values is None:
        raise ValueError("Sparse embedding must have indices and values")
    idx_list = indices.tolist() if hasattr(indices, "tolist") else list(indices)
    val_list = values.tolist() if hasattr(values, "tolist") else list(values)
    return SparseVector(indices=idx_list, values=val_list)


def hybrid_collection_create_kwargs(vector_size: int, distance: Distance = Distance.COSINE) -> dict:
    """Arguments for QdrantClient.create_collection (hybrid dense + BM25 sparse)."""
    return {
        "vectors_config": {
            DENSE_VECTOR_NAME: VectorParams(size=vector_size, distance=distance),
        },
        "sparse_vectors_config": {
            SPARSE_VECTOR_NAME: SparseVectorParams(modifier=Modifier.IDF),
        },
    }


def collection_hybrid_mode(client: QdrantClient, collection_name: str) -> str:
    """
    Return 'hybrid' if collection uses named dense + lexical sparse, else 'legacy'.
    """
    info = client.get_collection(collection_name=collection_name)
    params = info.config.params
    vecs = params.vectors
    sparse_vecs = getattr(params, "sparse_vectors", None) or {}
    if isinstance(vecs, dict) and DENSE_VECTOR_NAME in vecs:
        if isinstance(sparse_vecs, dict) and SPARSE_VECTOR_NAME in sparse_vecs:
            return "hybrid"
    return "legacy"


def meta_point_vectors(vector_size: int, mode: str) -> Union[List[float], dict]:
    """Zero dense (+ empty sparse) for collection meta point."""
    if mode == "hybrid":
        return {
            DENSE_VECTOR_NAME: [0.0] * vector_size,
            SPARSE_VECTOR_NAME: SparseVector(indices=[], values=[]),
        }
    return [0.0] * vector_size
