"""
Read-only Qdrant indexer for MCP search services.

Indexing is performed by kb-manager; MCP loads vectors from Qdrant at runtime.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

import qdrant_client
from llama_index.core import Settings, VectorStoreIndex
from llama_index.vector_stores.qdrant import QdrantVectorStore
from qdrant_client.http.models import FieldCondition, Filter, MatchValue

from utils.mcp_embedding import RemoteEmbedding, meta_id_for_collection


def metadata_document_count(metadata: Optional[Dict[str, Any]]) -> int:
    if not metadata:
        return 0
    for key in ("document_count", "documents_count"):
        value = metadata.get(key)
        if value is not None:
            return int(value)
    return 0


@dataclass
class IndexerConfig:
    """Configuration for read-only Qdrant access from MCP."""

    service_dir: Path
    embed_api_url: str
    embed_api_key: str
    embed_model_name: str
    similarity_top_k: int = 5
    similarity_cutoff: float = 0.0
    use_qdrant: bool = True
    qdrant_host: str = "localhost"
    qdrant_port: int = 6333
    qdrant_collection: str = "kb_collection"
    qdrant_alias: Optional[str] = None
    qdrant_timeout: int = 10
    logger_name: str = "qdrant_indexer"
    documents_dir: Path = field(default_factory=lambda: Path("."))

    def validate(self) -> None:
        if not self.embed_api_key or not self.embed_api_url:
            raise ValueError("Embedding API configuration is missing")


class IndexRuntime:
    def __init__(self) -> None:
        self.initialized = False
        self.last_update: Optional[str] = None


class QdrantReadIndexer:
    """Load retriever state and metadata from Qdrant (no write operations)."""

    collection_meta_type = "collection_meta"

    def __init__(self, config: IndexerConfig):
        self.cfg = config
        self.cfg.validate()
        self.logger = logging.getLogger(self.cfg.logger_name)
        Settings.embed_model = RemoteEmbedding(
            api_url=str(self.cfg.embed_api_url),
            api_key=str(self.cfg.embed_api_key),
            model_name=str(self.cfg.embed_model_name),
        )
        self.embed_model = RemoteEmbedding(
            api_url=str(self.cfg.embed_api_url),
            api_key=str(self.cfg.embed_api_key),
            model_name=str(self.cfg.embed_model_name),
        )
        self._qdrant_client_instance = None
        self.index = None
        self.retriever = None
        self.metadata: Dict[str, Any] = {}

    def _get_qdrant_client(self):
        if self._qdrant_client_instance is None:
            self._qdrant_client_instance = qdrant_client.QdrantClient(
                host=self.cfg.qdrant_host,
                port=self.cfg.qdrant_port,
                timeout=self.cfg.qdrant_timeout,
            )
        return self._qdrant_client_instance

    def reset_qdrant_client(self) -> None:
        self._qdrant_client_instance = None

    def is_qdrant_reachable(self) -> bool:
        if not self.cfg.use_qdrant:
            return False
        try:
            self._get_qdrant_client().get_collections()
            return True
        except Exception as exc:
            self.logger.warning("Qdrant недоступен: %s", exc)
            self.reset_qdrant_client()
            return False

    def get_active_collection(self) -> Optional[str]:
        client = self._get_qdrant_client()
        for alias in client.get_aliases().aliases:
            if alias.alias_name == self.cfg.qdrant_alias:
                return alias.collection_name
        return None

    def load_metadata_for_collection(self, collection_name: str) -> Dict[str, Any]:
        default_meta = self._get_default_metadata()
        if not self.cfg.use_qdrant:
            return default_meta
        try:
            client = self._get_qdrant_client()
            meta_id = meta_id_for_collection(collection_name)
            res = client.retrieve(collection_name, ids=[meta_id])
            if res and res[0].payload:
                return res[0].payload
            self.logger.info(
                "Metadata point not found in collection '%s'. Using defaults.",
                collection_name,
            )
            return default_meta
        except Exception as exc:
            self.logger.warning(
                "Failed to load metadata from Qdrant collection '%s': %s. Using defaults",
                collection_name,
                exc,
            )
            return default_meta

    def _get_default_metadata(self) -> Dict[str, Any]:
        return {
            "last_updated": None,
            "document_count": 0,
            "documents_count": 0,
            "llm_using": self.cfg.embed_model_name,
            "index_status": "not_initialized",
            "storage_type": "qdrant" if self.cfg.use_qdrant else "local_folder",
            "source": None,
            "__type__": self.collection_meta_type,
        }

    def get_active_metadata(self) -> Dict[str, Any]:
        if not self.cfg.use_qdrant:
            return self.metadata

        target_alias = self.cfg.qdrant_alias
        try:
            active_collection = self.get_active_collection()
            if not active_collection:
                self.logger.info("Alias '%s' not found yet.", target_alias)
                return {}
            metadata = self.load_metadata_for_collection(active_collection)
            if metadata.get("__type__") == self.collection_meta_type:
                return metadata
            self.logger.warning(
                "Alias '%s' exists, but metadata point is missing.",
                target_alias,
            )
            return {}
        except Exception as exc:
            self.logger.error("Failed to load active metadata: %s", exc, exc_info=True)
            return {}

    def filter_documents(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        if not self.cfg.use_qdrant:
            self.logger.warning("Qdrant disabled, filter_documents skipped")
            return {"items": [], "next_offset": None}

        category = payload.get("category")
        kb_id = payload.get("kb_id")
        limit = int(payload.get("limit", 10))
        offset = payload.get("offset")
        conditions = []
        must_not_conditions = [
            FieldCondition(
                key="__type__",
                match=MatchValue(value=self.collection_meta_type),
            )
        ]
        if category:
            conditions.append(
                FieldCondition(key="category", match=MatchValue(value=category))
            )
        if kb_id:
            conditions.append(
                FieldCondition(key="kb_id", match=MatchValue(value=kb_id))
            )
        q_filter = Filter(must=conditions, must_not=must_not_conditions)
        try:
            client = self._get_qdrant_client()
            points, next_offset = client.scroll(
                collection_name=self.cfg.qdrant_alias,
                scroll_filter=q_filter,
                limit=limit,
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            results = [
                {"id": point.id, "payload": point.payload, "score": None}
                for point in points
            ]
            return {"items": results, "next_offset": next_offset}
        except Exception as exc:
            self.logger.error("Error filtering documents: %s", exc)
            return {"items": [], "next_offset": None}

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
            vector_store = QdrantVectorStore(
                client=client,
                collection_name=self.cfg.qdrant_alias,
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

        vector_store = QdrantVectorStore(
            client=client,
            collection_name=collection_name,
            filters=qdrant_filter,
        )
        index = VectorStoreIndex.from_vector_store(vector_store)
        return index.as_retriever(
            similarity_top_k=top_k,
            similarity_cutoff=self.cfg.similarity_cutoff,
        )


# FAQ MCP uses hybrid Indexer (dense-only search); KB MCP subclasses the same class.
Indexer = QdrantReadIndexer
