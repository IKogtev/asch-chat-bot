"""Парсинг ответа kb_search и хранение разрешённых document_id для doc_search."""
from __future__ import annotations

import re
from typing import Any

KB_SEARCH_EMPTY_MARKERS = (
    "Ничего не найдено",
    "База знаний не инициализирована",
    "Локальный FAQ не инициализирован",
    "USE_QDRANT=false",
    "не является hybrid",
)

_DOC_BLOCK_RE = re.compile(
    r"rank\s*\[(\d+)\]\s*FILE_NAME:\s*(.+?)\s*\n"
    r"RELATIVE_PATH:\s*(.+?)\s*\n+\s*"
    r"DOCUMENT_ID:\s*(.+?)\s*\n",
    re.DOTALL | re.IGNORECASE,
)


def is_kb_search_empty(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    return any(marker in text for marker in KB_SEARCH_EMPTY_MARKERS)


def parse_kb_search_hits(content: str) -> list[dict[str, Any]]:
    """Извлекает документы из CONTEXT ответа kb_search."""
    if is_kb_search_empty(content):
        return []

    hits: list[dict[str, Any]] = []
    for block in re.split(r"\n---\n", content):
        match = _DOC_BLOCK_RE.search(block)
        if not match:
            continue
        hits.append(
            {
                "rank": int(match.group(1)),
                "source_name": match.group(2).strip(),
                "source_path": match.group(3).strip(),
                "document_id": match.group(4).strip(),
            }
        )
    return hits


def allowed_document_ids(hits: list[dict[str, Any]]) -> set[str]:
    return {
        str(item.get("document_id") or "").strip()
        for item in hits
        if str(item.get("document_id") or "").strip()
    }


def format_kb_hits_summary(hits: list[dict[str, Any]], *, max_ids: int = 12) -> str:
    if not hits:
        return "count=0"
    ids = [
        str(item.get("document_id") or "").strip()
        for item in hits
        if str(item.get("document_id") or "").strip()
    ]
    if len(ids) <= max_ids:
        return f"count={len(ids)} ids={ids}"
    return f"count={len(ids)} ids={ids[:max_ids]}...+{len(ids) - max_ids}"
