"""Re-export hybrid indexer from repo root (single source: utils/indexer.py)."""

from __future__ import annotations

from utils._shared_utils import load_shared_module

_mod = load_shared_module("indexer.py", "_shared_indexer")

Indexer = _mod.Indexer
IndexerConfig = _mod.IndexerConfig
IndexRuntime = _mod.IndexRuntime
metadata_document_count = _mod.metadata_document_count

__all__ = [
    "Indexer",
    "IndexerConfig",
    "IndexRuntime",
    "metadata_document_count",
]
