"""KB MCP indexer: read-only Qdrant access plus hybrid search helpers."""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from llama_index.core import VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from utils.qdrant_hybrid import (
    DENSE_VECTOR_NAME,
    SPARSE_VECTOR_NAME,
    collection_hybrid_mode,
    sparse_embedding_to_vector,
)
from utils.qdrant_indexer import QdrantReadIndexer, IndexerConfig, IndexRuntime, metadata_document_count
from utils.rrf import reciprocal_rank_fusion
from utils.search_profile import hybrid_rrf_params_for_profile
from utils.qdrant_search_filters import build_hybrid_qdrant_filter, describe_hybrid_qdrant_filter


class Indexer(QdrantReadIndexer):
    """Extends shared read-only indexer with Qdrant hybrid search (RRF / dense-only)."""

    def __init__(self, config: IndexerConfig):
        super().__init__(config)
        self._sparse_embedder = None

    def _get_sparse_embedder(self):
        if self._sparse_embedder is None:
            from fastembed import SparseTextEmbedding

            lang = os.getenv("SPARSE_BM25_LANGUAGE", "russian")
            local_only = os.getenv("FASTEMBED_LOCAL_FILES_ONLY", "").strip().lower() in (
                "1",
                "true",
                "yes",
                "on",
            )
            self._sparse_embedder = SparseTextEmbedding(
                model_name="Qdrant/bm25",
                language=lang,
                local_files_only=local_only,
            )
        return self._sparse_embedder

    def _resolve_search_collection_name(self, collection: Optional[str]) -> str:
        client = self._get_qdrant_client()
        collection_name = collection or self.get_active_collection() or self.cfg.qdrant_alias
        if not client.collection_exists(collection_name):
            collection_name = self.get_active_collection() or self.cfg.qdrant_collection
        return collection_name

    def hybrid_search_enabled(self, collection: Optional[str]) -> bool:
        if not self.cfg.use_qdrant:
            return False
        client = self._get_qdrant_client()
        name = self._resolve_search_collection_name(collection)
        return collection_hybrid_mode(client, name) == "hybrid"

    def _log_hybrid_qdrant_filter(
        self,
        *,
        method: str,
        collection: Optional[str],
        filters: Optional[Dict[str, Any]],
        search_profile: Optional[str],
    ) -> None:
        summary = describe_hybrid_qdrant_filter(filters, search_profile)
        self.logger.debug(
            "kb_search %s: collection=%s qdrant_filter=%s",
            method,
            collection,
            summary,
        )

    def hybrid_search_rrf(
        self,
        query: str,
        collection: Optional[str],
        filters: Optional[Dict[str, Any]],
        top_k: int,
        search_profile: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        client = self._get_qdrant_client()
        collection_name = self._resolve_search_collection_name(collection)
        if collection_hybrid_mode(client, collection_name) != "hybrid":
            raise ValueError("Collection is not hybrid-indexed")

        q_filter = build_hybrid_qdrant_filter(filters, search_profile)
        self._log_hybrid_qdrant_filter(
            method="hybrid_search_rrf",
            collection=collection,
            filters=filters,
            search_profile=search_profile,
        )
        rrf_k, candidate_mult = hybrid_rrf_params_for_profile(search_profile or "default")
        fetch = max(top_k * candidate_mult, 100)

        query_vector = self.embed_model.get_text_embedding_batch([query])[0]
        sparse_model = self._get_sparse_embedder()
        q_sparse = list(sparse_model.query_embed(query or " "))
        if not q_sparse:
            return []
        sparse_vec = sparse_embedding_to_vector(q_sparse[0])

        dresp = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            using=DENSE_VECTOR_NAME,
            limit=fetch,
            query_filter=q_filter,
            with_payload=True,
            with_vectors=False,
        )
        sresp = client.query_points(
            collection_name=collection_name,
            query=sparse_vec,
            using=SPARSE_VECTOR_NAME,
            limit=fetch,
            query_filter=q_filter,
            with_payload=True,
            with_vectors=False,
        )
        dense_ids = [hit.id for hit in (dresp.points or [])]
        sparse_ids = [hit.id for hit in (sresp.points or [])]
        fused = reciprocal_rank_fusion([dense_ids, sparse_ids], k=rrf_k)
        hit_map: Dict[Any, Any] = {}
        for hit in (dresp.points or []) + (sresp.points or []):
            if hit.id not in hit_map:
                hit_map[hit.id] = hit
        dense_score_by_id = {
            hit.id: float(hit.score) for hit in (dresp.points or []) if hit.score is not None
        }
        sparse_score_by_id = {
            hit.id: float(hit.score) for hit in (sresp.points or []) if hit.score is not None
        }

        out: List[Dict[str, Any]] = []
        for pid, rrf_score in fused[:top_k]:
            hit = hit_map.get(pid)
            if not hit:
                continue
            payload = hit.payload or {}
            out.append(
                {
                    "text": payload.get("text", ""),
                    "metadata": dict(payload),
                    "score": float(rrf_score),
                    "rrf_score": float(rrf_score),
                    "dense_score": dense_score_by_id.get(pid),
                    "sparse_score": sparse_score_by_id.get(pid),
                }
            )
        return out

    def hybrid_dense_search(
        self,
        query: str,
        collection: Optional[str],
        filters: Optional[Dict[str, Any]],
        top_k: int,
        search_profile: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        client = self._get_qdrant_client()
        collection_name = self._resolve_search_collection_name(collection)
        if collection_hybrid_mode(client, collection_name) != "hybrid":
            raise ValueError("Collection is not hybrid-indexed")

        q_filter = build_hybrid_qdrant_filter(filters, search_profile)
        self._log_hybrid_qdrant_filter(
            method="hybrid_dense_search",
            collection=collection,
            filters=filters,
            search_profile=search_profile,
        )
        query_vector = self.embed_model.get_text_embedding_batch([query])[0]
        resp = client.query_points(
            collection_name=collection_name,
            query=query_vector,
            using=DENSE_VECTOR_NAME,
            limit=top_k,
            query_filter=q_filter,
            with_payload=True,
            with_vectors=False,
        )
        out: List[Dict[str, Any]] = []
        for hit in resp.points or []:
            payload = hit.payload or {}
            score = float(hit.score) if hit.score is not None else 0.0
            out.append(
                {
                    "text": payload.get("text", ""),
                    "metadata": dict(payload),
                    "score": score,
                    "rrf_score": None,
                    "dense_score": score,
                    "sparse_score": None,
                }
            )
        return out

    def _qdrant_vector_store(
        self,
        client,
        collection_name: str,
        filters=None,
        mode_check_name: Optional[str] = None,
    ):
        kwargs: Dict[str, Any] = {"client": client, "collection_name": collection_name}
        if filters is not None:
            kwargs["filters"] = filters
        mode_name = mode_check_name or collection_name
        if collection_hybrid_mode(client, mode_name) == "hybrid":
            kwargs["vector_name"] = DENSE_VECTOR_NAME
        try:
            return QdrantVectorStore(**kwargs)
        except TypeError:
            kwargs.pop("vector_name", None)
            return QdrantVectorStore(**kwargs)

    def reload_runtime(self) -> bool:
        if not self.cfg.use_qdrant:
            return False
        try:
            active_collection = self.get_active_collection()
            if not active_collection:
                self.logger.warning("Alias %s not found or empty.", self.cfg.qdrant_alias)
                return False
            self.logger.info("Reloading runtime from active collection: %s", active_collection)
            client = self._get_qdrant_client()
            vector_store = self._qdrant_vector_store(
                client,
                self.cfg.qdrant_alias,
                mode_check_name=active_collection,
            )
            index = VectorStoreIndex.from_vector_store(
                vector_store=vector_store,
                embed_model=self.embed_model,
            )
            retriever = index.as_retriever(
                similarity_top_k=self.cfg.similarity_top_k,
                similarity_cutoff=self.cfg.similarity_cutoff,
            )
            self.index = index
            self.retriever = retriever
            self.metadata = self.get_active_metadata()
            self.logger.info("Runtime reloaded successfully")
            return True
        except Exception as exc:
            self.logger.error("Runtime reload failed: %s", exc, exc_info=True)
            self.reset_qdrant_client()
            return False

    def get_retriever_for_collection(
        self,
        collection: Optional[str],
        top_k: int,
        filters: Optional[dict] = None,
    ):
        collection_name = collection or self.get_active_collection()
        client = self._get_qdrant_client()
        self.logger.info("Name of collection: %s", collection_name)
        if collection_name and not client.collection_exists(collection_name):
            collection_name = self.get_active_collection()

        qdrant_filter = None
        if filters:
            conditions = []
            for key, value in filters.items():
                if value is None:
                    continue
                conditions.append(
                    FieldCondition(key=key, match=MatchValue(value=value))
                )
            if conditions:
                qdrant_filter = Filter(must=conditions)

        meta_exclusion = [
            FieldCondition(
                key="__type__",
                match=MatchValue(value=self.collection_meta_type),
            )
        ]
        if qdrant_filter:
            qdrant_filter.must_not = (qdrant_filter.must_not or []) + meta_exclusion
        else:
            qdrant_filter = Filter(must_not=meta_exclusion)

        vector_store = self._qdrant_vector_store(
            client,
            collection_name,
            filters=qdrant_filter,
            mode_check_name=collection_name,
        )
        index = VectorStoreIndex.from_vector_store(
            vector_store,
            embed_model=self.embed_model,
        )
        return index.as_retriever(
            similarity_top_k=top_k,
            similarity_cutoff=self.cfg.similarity_cutoff,
        )


__all__ = [
    "Indexer",
    "IndexerConfig",
    "IndexRuntime",
    "metadata_document_count",
]
