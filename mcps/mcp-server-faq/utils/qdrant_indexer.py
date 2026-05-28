"""Re-export shared read-only indexer from repo root (single source: utils/qdrant_indexer.py)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SHARED = Path(__file__).resolve().parents[3] / "utils" / "qdrant_indexer.py"
_spec = importlib.util.spec_from_file_location("_shared_qdrant_indexer", _SHARED)
assert _spec is not None and _spec.loader is not None
_mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_mod)

Indexer = _mod.Indexer
IndexerConfig = _mod.IndexerConfig
IndexRuntime = _mod.IndexRuntime
QdrantReadIndexer = _mod.QdrantReadIndexer
metadata_document_count = _mod.metadata_document_count

__all__ = [
    "Indexer",
    "IndexerConfig",
    "IndexRuntime",
    "QdrantReadIndexer",
    "metadata_document_count",
]
