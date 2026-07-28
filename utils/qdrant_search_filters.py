"""
Сборка Qdrant Filter для hybrid-поиска (kb_search).

Для search_profile=doc_search по умолчанию исключается папка архива (must_not по section_path).
Если в filters явно задан section_path на эту папку — исключение не добавляется, остаётся только must.
"""

from __future__ import annotations

import os
from typing import Any, Dict, Optional

from qdrant_client.http.models import FieldCondition, Filter, MatchValue

COLLECTION_META_TYPE = "collection_meta"
DOC_SEARCH_PROFILE = "doc_search"


def doc_search_archive_section() -> str:
    return os.getenv("DOC_SEARCH_ARCHIVE_SECTION", "5 Архив").strip()


def archive_explicitly_requested(
    filters: Optional[Dict[str, Any]],
    archive_section: Optional[str] = None,
) -> bool:
    """True, если filters ограничивают поиск папкой архива (section_path)."""
    section = (archive_section or doc_search_archive_section()).strip()
    if not section or not filters:
        return False

    value = filters.get("section_path")
    if value is None:
        return False
    if isinstance(value, (list, tuple)):
        return section in [str(v).strip() for v in value if v is not None]
    return str(value).strip() == section


def build_hybrid_qdrant_filter(
    filters: Optional[Dict[str, Any]],
    search_profile: Optional[str] = None,
    *,
    archive_section: Optional[str] = None,
) -> Filter:
    """
    must_not: служебные точки коллекции; для doc_search — архив, если он не запрошен явно.
    must: пары key/value из filters (как раньше).
    """
    must_not = [
        FieldCondition(
            key="type",
            match=MatchValue(value=COLLECTION_META_TYPE),
        )
    ]
    must: list[FieldCondition] = []

    profile = (search_profile or "default").strip().lower()
    section = (archive_section or doc_search_archive_section()).strip()
    archive_requested = archive_explicitly_requested(filters, section)

    if profile == DOC_SEARCH_PROFILE and section and not archive_requested:
        must_not.append(
            FieldCondition(
                key="section_path",
                match=MatchValue(value=section),
            )
        )

    if filters:
        for key, value in filters.items():
            if value is None:
                continue
            must.append(
                FieldCondition(
                    key=key,
                    match=MatchValue(value=value),
                )
            )

    return Filter(must=must, must_not=must_not)


def _field_conditions_for_log(
    conditions: Optional[list[FieldCondition]],
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for cond in conditions or []:
        match = cond.match
        value = getattr(match, "value", match)
        out.append({"key": cond.key, "match": value})
    return out


def describe_hybrid_qdrant_filter(
    filters: Optional[Dict[str, Any]],
    search_profile: Optional[str] = None,
    *,
    archive_section: Optional[str] = None,
) -> dict[str, Any]:
    """Краткое описание итогового Qdrant-фильтра для debug-логов kb_search/indexer."""
    profile = (search_profile or "default").strip().lower()
    section = (archive_section or doc_search_archive_section()).strip()
    archive_requested = archive_explicitly_requested(filters, section)
    archive_excluded = profile == DOC_SEARCH_PROFILE and bool(section) and not archive_requested

    q_filter = build_hybrid_qdrant_filter(
        filters,
        search_profile,
        archive_section=archive_section,
    )

    archive: dict[str, Any] = {
        "configured_section": section or None,
        "excluded_from_search": archive_excluded,
    }
    if archive_excluded:
        archive["must_not_section_path"] = section
    elif profile == DOC_SEARCH_PROFILE and archive_requested:
        archive["note"] = "archive requested in agent filters; must_not not applied"
    elif profile != DOC_SEARCH_PROFILE:
        archive["note"] = "archive exclusion applies only to doc_search profile"

    return {
        "search_profile": profile,
        "agent_filters": dict(filters) if filters else None,
        "archive": archive,
        "must": _field_conditions_for_log(q_filter.must),
        "must_not": _field_conditions_for_log(q_filter.must_not),
    }
