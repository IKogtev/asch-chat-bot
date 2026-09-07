"""Пошаговый обход файлового дерева KB, как «Дерево файлов» в kb-manager."""

from __future__ import annotations

from pathlib import Path

from bot.services.product_kits import _resolve_inside
from web_bff.files import FileTokenError

IGNORE_FOLDERS = {".git", "__pycache__", "_prepared"}


class KbTreeError(ValueError):
    pass


def normalize_rel_path(path: str | None) -> str:
    text = (path or "").replace("\\", "/").strip().strip("/")
    if not text:
        return ""
    parts: list[str] = []
    for part in text.split("/"):
        if part in {"", "."}:
            continue
        if part == ".." or part in IGNORE_FOLDERS:
            raise KbTreeError("invalid_path")
        parts.append(part)
    return "/".join(parts)


def resolve_kb_dir(root: Path, relative: str) -> Path:
    rel = normalize_rel_path(relative)
    target = root if not rel else root / Path(rel)
    try:
        root_resolved, resolved = _resolve_inside(root, target)
    except ValueError as exc:
        raise KbTreeError("invalid_path") from exc
    if not resolved.exists() or not resolved.is_dir():
        raise KbTreeError("not_found")
    if resolved != root_resolved and any(part in IGNORE_FOLDERS for part in resolved.relative_to(root_resolved).parts):
        raise KbTreeError("invalid_path")
    return resolved


def resolve_kb_file(root: Path, relative: str) -> Path:
    try:
        rel = normalize_rel_path(relative)
    except KbTreeError as exc:
        raise FileTokenError("not_found") from exc
    if not rel:
        raise FileTokenError("not_found")
    child = root / Path(rel)
    try:
        root_resolved, resolved = _resolve_inside(root, child)
    except ValueError as exc:
        raise FileTokenError("path_outside_kb") from exc
    if any(part in IGNORE_FOLDERS for part in resolved.relative_to(root_resolved).parts):
        raise FileTokenError("not_found")
    if not resolved.is_file():
        raise FileTokenError("not_found")
    return resolved


def list_kb_node(root: Path, relative: str) -> dict:
    current = resolve_kb_dir(root, relative)
    rel = normalize_rel_path(relative)
    parent = str(Path(rel).parent).replace("\\", "/") if rel else None
    if parent == ".":
        parent = ""

    folders: list[dict[str, str]] = []
    files: list[dict[str, str]] = []
    for item in sorted(current.iterdir(), key=lambda path: path.name.casefold()):
        if item.name in IGNORE_FOLDERS or item.name.startswith("."):
            continue
        child_path = f"{rel}/{item.name}" if rel else item.name
        if item.is_dir():
            folders.append({"name": item.name, "path": child_path})
        elif item.is_file() and item.stat().st_size > 0:
            files.append({"name": item.name, "path": child_path})
    return {
        "path": rel,
        "parent": parent,
        "folders": folders,
        "files": files,
    }
