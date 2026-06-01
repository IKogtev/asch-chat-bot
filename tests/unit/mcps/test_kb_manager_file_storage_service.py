import ast
import time
import types
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Dict, List

import pytest


def _load_class(file_path: Path, class_name: str, extra_globals: dict):
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace[class_name]


def _logger():
    return types.SimpleNamespace(
        info=lambda *a, **k: None,
        debug=lambda *a, **k: None,
        warning=lambda *a, **k: None,
        error=lambda *a, **k: None,
    )


class _DocumentLoader:
    def __init__(self, *args, **kwargs):
        pass

    def prepare_docs_texts(self, **kwargs):
        return ([{"text": "doc", "meta": {"chunk_id": "doc#0", "kb_id": kwargs["kb_id"], "content_hash": "h1"}}], None, 1, 1)


FileStorageService = _load_class(
    Path(__file__).resolve().parents[3] / "mcps" / "kb-manager" / "app" / "services" / "file_storage_service.py",
    "FileStorageService",
    {
        "Path": Path,
        "List": List,
        "Dict": Dict,
        "datetime": datetime,
        "hash_file": lambda path: f"hash:{path.name}",
        "DocumentLoader": _DocumentLoader,
        "setup_logger": lambda *args, **kwargs: _logger(),
        "time": time,
    },
)


class _FakeStat:
    def __init__(self, size: int, mtime: float = 1_700_000_000.0):
        self.st_size = size
        self.st_mtime = mtime


class _FakePath:
    def __init__(self, root, rel: tuple[str, ...], *, is_file: bool, size: int = 0):
        self._root = root
        self._rel = rel
        self._is_file = is_file
        self._size = size

    def __truediv__(self, child: str):
        return self._root._get(self._rel + (child,))

    def exists(self):
        return self._root.exists(self._rel)

    def rglob(self, pattern: str):
        return self._root.rglob(self._rel)

    def is_file(self):
        return self._is_file

    def relative_to(self, other):
        base = other._rel if isinstance(other, _FakePath) else tuple()
        rel = self._rel[len(base):]
        return PurePosixPath(*rel)

    def stat(self):
        return _FakeStat(self._size)

    @property
    def suffix(self):
        return PurePosixPath(*self._rel).suffix

    @property
    def name(self):
        return self._rel[-1]

    @property
    def parent(self):
        return self._root._get(self._rel[:-1])


class _FakeRoot(_FakePath):
    def __init__(self, entries: dict[tuple[str, ...], dict]):
        self.entries = entries
        super().__init__(self, tuple(), is_file=False)

    def _get(self, rel: tuple[str, ...]):
        entry = self.entries.get(rel, {"is_file": False, "size": 0})
        return _FakePath(self, rel, is_file=entry["is_file"], size=entry["size"])

    def exists(self, rel: tuple[str, ...]):
        if rel in self.entries:
            return True
        return any(path[: len(rel)] == rel for path in self.entries)

    def rglob(self, prefix):
        if isinstance(prefix, str):
            prefix = tuple()
        result = []
        for rel, entry in self.entries.items():
            if rel[: len(prefix)] == prefix:
                result.append(_FakePath(self, rel, is_file=entry["is_file"], size=entry["size"]))
        return result


class _FakeQdrant:
    def __init__(self, docs=None):
        self.docs = docs or []
        self.deleted = []

    def list_documents(self, **kwargs):
        return self.docs

    def delete_document(self, document_id, **kwargs):
        self.deleted.append(document_id)

    def upload_points_qdrant(self, documents, docs_count, points_count):
        return None


def _make_service(root, qdrant):
    return FileStorageService(
        root_path=root,
        qdrant_service=qdrant,
        chunk_size=100,
        chunk_overlap=10,
        service_dir=Path("."),
        ext_allowed={".txt"},
        qdrant_collection_name="kb_collection",
    )


@pytest.mark.unit
def test_scan_files_filters_small_unsupported_and_ignored_paths() -> None:
    root = _FakeRoot(
        {
            ("kb1",): {"is_file": False, "size": 0},
            ("kb1", "valid.txt"): {"is_file": True, "size": 1200},
            ("kb1", "small.txt"): {"is_file": True, "size": 10},
            ("kb1", "bad.exe"): {"is_file": True, "size": 1200},
            ("kb1", "__pycache__"): {"is_file": False, "size": 0},
            ("kb1", "__pycache__", "ignored.txt"): {"is_file": True, "size": 1200},
        }
    )
    service = _make_service(root, _FakeQdrant())

    result = service.scan_files("kb1")

    assert [item["filename"] for item in result] == ["valid.txt", "small.txt"]
    assert result[0]["relative_path"] == "kb1/valid.txt"


@pytest.mark.unit
def test_build_tree_creates_nested_structure() -> None:
    root = _FakeRoot(
        {
            ("kb1",): {"is_file": False, "size": 0},
            ("kb1", "folder"): {"is_file": False, "size": 0},
            ("kb1", "folder", "doc.txt"): {"is_file": True, "size": 1200},
        }
    )
    service = _make_service(root, _FakeQdrant())

    result = service.build_tree()

    def collect_files(node):
        found = []
        for key, value in node.items():
            if key == "files":
                found.extend(value)
            elif isinstance(value, dict):
                found.extend(collect_files(value))
        return found

    assert "doc.txt" in collect_files(result)


@pytest.mark.unit
def test_sync_indexes_new_files_and_deletes_missing_files(monkeypatch: pytest.MonkeyPatch) -> None:
    root = _FakeRoot(
        {
            ("kb1",): {"is_file": False, "size": 0},
            ("kb1", "new.txt"): {"is_file": True, "size": 1200},
        }
    )
    qdrant = _FakeQdrant(
        docs=[{"kb_id": "kb1", "source_name": "old.txt", "document_id": "doc-old", "doc_hash": "hash:old.txt"}]
    )
    service = _make_service(root, qdrant)

    indexed = []
    monkeypatch.setattr(service, "_index_file", lambda file_info, kb_id, collection_type: indexed.append((file_info["filename"], kb_id, collection_type)))

    service.sync("kb1", "kb")

    assert indexed == [("new.txt", "kb1", "kb")]
    assert qdrant.deleted == ["doc-old"]
