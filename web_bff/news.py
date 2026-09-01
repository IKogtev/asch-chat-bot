"""Преобразование строк news из БД в ответы WebUI."""

from __future__ import annotations

import html
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, field_serializer

_TITLE_MAX_LEN = 120
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WHITESPACE_RE = re.compile(r"\s+")


class NewsFile(BaseModel):
    name: str


class NewsListItem(BaseModel):
    id: int
    published_at: datetime
    title: str

    @field_serializer("published_at")
    def _serialize_published_at(self, value: datetime) -> str:
        return _to_utc_z(value)


class NewsDetail(BaseModel):
    id: int
    published_at: datetime
    title: str
    content: str
    files: list[NewsFile]

    @field_serializer("published_at")
    def _serialize_published_at(self, value: datetime) -> str:
        return _to_utc_z(value)


class NewsListResponse(BaseModel):
    items: list[NewsListItem]
    limit: int
    offset: int
    has_more: bool


def to_news_list_item(row: dict) -> NewsListItem:
    return NewsListItem(
        id=int(row["id"]),
        published_at=_published_at(row),
        title=_title_from_text(row.get("text") or ""),
    )


def to_news_detail(row: dict) -> NewsDetail:
    return NewsDetail(
        id=int(row["id"]),
        published_at=_published_at(row),
        title=_title_from_text(row.get("text") or ""),
        content=str(row.get("text") or ""),
        files=_safe_files(row.get("files")),
    )


def _to_utc_z(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _published_at(row: dict) -> datetime:
    value = row.get("scheduled_at") or row.get("created_at")
    if value is None:
        return datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _title_from_text(text: str) -> str:
    first_line = ""
    for line in str(text).splitlines():
        stripped = line.strip()
        if stripped:
            first_line = stripped
            break
    without_tags = _HTML_TAG_RE.sub("", first_line)
    decoded = html.unescape(without_tags)
    collapsed = _WHITESPACE_RE.sub(" ", decoded).strip()
    if len(collapsed) <= _TITLE_MAX_LEN:
        return collapsed
    return collapsed[:_TITLE_MAX_LEN]


def _safe_files(raw) -> list[NewsFile]:
    if not isinstance(raw, list):
        return []
    files: list[NewsFile] = []
    for item in raw:
        name = _file_display_name(item)
        if name:
            files.append(NewsFile(name=name))
    return files


def _file_display_name(item) -> str | None:
    if isinstance(item, str):
        raw = item
    elif isinstance(item, dict):
        raw = item.get("name") or item.get("filename") or item.get("path") or ""
    else:
        return None
    name = Path(str(raw)).name
    return name or None
