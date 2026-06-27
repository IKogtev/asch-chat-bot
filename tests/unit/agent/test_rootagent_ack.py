import importlib.util
import sys
import types
from pathlib import Path

import pytest


def _load_helpers():
    repo_root = Path(__file__).resolve().parents[3]
    module_path = repo_root / "agent" / "helpers.py"
    spec = importlib.util.spec_from_file_location("agent.helpers", module_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    spec.loader.exec_module(module)
    return module


helpers = _load_helpers()


@pytest.mark.unit
@pytest.mark.parametrize(
    ("route", "intent", "expected"),
    [
        ("kb_answer", "kb_answer", "Проверю информацию в базе знаний."),
        ("doc_search", "doc_search", "Подберу документы по запросу."),
        ("product_selection", "product_filter", "Уточню параметры продукта."),
        ("kb_answer", "smalltalk", None),
        ("doc_search", "show_more", None),
    ],
)
def test_format_ack_message(route, intent, expected) -> None:
    assert helpers.format_ack_message(route, intent) == expected
