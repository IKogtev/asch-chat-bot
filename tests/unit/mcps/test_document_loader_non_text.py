"""Unit tests for indexing files without extractable text."""

import ast
import json
import types
import uuid
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pytest

DOC_LOADER_PATH = (
    Path(__file__).resolve().parents[3]
    / "mcps"
    / "mcp-server-kbsearch"
    / "app"
    / "utils"
    / "preprocessors"
    / "document_loader.py"
)


class _FakeSplitter:
    def __init__(self, **kwargs):
        pass

    def split_text(self, text: str) -> list[str]:
        return [text] if text else []


class _FakeFAQPreprocessor:
    def __init__(self, *args, **kwargs):
        pass


def _load_document_loader_module():
    tree = ast.parse(DOC_LOADER_PATH.read_text(encoding="utf-8"), filename=str(DOC_LOADER_PATH))
    selected = [
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == "DocumentLoader"
        or (
            isinstance(node, ast.Assign)
            and any(
                isinstance(t, ast.Name) and t.id == "IMAGE_NO_OCR_SUFFIXES"
                for t in node.targets
            )
        )
    ]
    namespace = {
        "__builtins__": __builtins__,
        "json": json,
        "uuid": uuid,
        "datetime": datetime,
        "Path": Path,
        "List": List,
        "Tuple": Tuple,
        "Dict": Dict,
        "Optional": Optional,
        "SentenceSplitter": _FakeSplitter,
        "hash_file": lambda path: "test-hash",
        "setup_logger": lambda *a, **k: types.SimpleNamespace(
            info=lambda *args, **kwargs: None,
            error=lambda *args, **kwargs: None,
        ),
        "FAQPreprocessor": _FakeFAQPreprocessor,
        "pd": types.SimpleNamespace(),
    }
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(DOC_LOADER_PATH), "exec"), namespace)
    return namespace


_ns = _load_document_loader_module()
IMAGE_NO_OCR_SUFFIXES = _ns["IMAGE_NO_OCR_SUFFIXES"]
DocumentLoader = _ns["DocumentLoader"]


@pytest.mark.unit
def test_image_suffixes_include_common_formats() -> None:
    assert ".png" in IMAGE_NO_OCR_SUFFIXES
    assert ".jpg" in IMAGE_NO_OCR_SUFFIXES
    assert ".webp" in IMAGE_NO_OCR_SUFFIXES


@pytest.mark.unit
def test_prepare_docs_texts_indexes_png_with_placeholder(tmp_path: Path) -> None:
    service_dir = tmp_path / "svc"
    service_dir.mkdir()
    (tmp_path / "cover.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    loader = DocumentLoader(documents_dir=tmp_path, service_dir=service_dir)
    docs, _, doc_count, point_count = loader.prepare_docs_texts(map_true=False)

    assert point_count == 1
    assert doc_count == 0
    assert docs[0]["text"] == "пусто"
    assert docs[0]["meta"]["source"] == "cover.png"
    assert docs[0]["meta"]["source_type"] == ".png"


@pytest.mark.unit
def test_prepare_docs_texts_indexes_empty_txt_with_placeholder(tmp_path: Path) -> None:
    service_dir = tmp_path / "svc"
    service_dir.mkdir()
    (tmp_path / "empty.md").write_text("", encoding="utf-8")

    loader = DocumentLoader(documents_dir=tmp_path, service_dir=service_dir)
    docs, _, _, point_count = loader.prepare_docs_texts(map_true=False)

    assert point_count == 1
    assert docs[0]["text"] == "пусто"
    assert docs[0]["meta"]["source"] == "empty.md"
