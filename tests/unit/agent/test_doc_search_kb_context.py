import importlib.util
import sys
from pathlib import Path

import pytest


def _load_kb_context_module():
    module_path = (
        Path(__file__).resolve().parents[3] / "agent" / "doc_search_kb_context.py"
    )
    spec = importlib.util.spec_from_file_location(
        "agent.doc_search_kb_context",
        module_path,
    )
    module = importlib.util.module_from_spec(spec)
    assert spec is not None and spec.loader is not None
    sys.modules["agent.doc_search_kb_context"] = module
    spec.loader.exec_module(module)
    return module


kb_context_module = _load_kb_context_module()
is_kb_search_empty = kb_context_module.is_kb_search_empty
parse_kb_search_hits = kb_context_module.parse_kb_search_hits
format_kb_hits_summary = kb_context_module.format_kb_hits_summary


SAMPLE_CONTEXT = """Используй только информацию из CONTEXT.

CONTEXT
rank [1] FILE_NAME: Fort_Knox.pdf
RELATIVE_PATH: marketing/Fort_Knox.pdf

DOCUMENT_ID: doc-1

TEXT:
some text

---

rank [2] FILE_NAME: other.pdf
RELATIVE_PATH: marketing/other.pdf

DOCUMENT_ID: doc-2

TEXT:
more text
"""


@pytest.mark.unit
def test_parse_kb_search_hits_extracts_documents() -> None:
    hits = parse_kb_search_hits(SAMPLE_CONTEXT)

    assert len(hits) == 2
    assert hits[0]["document_id"] == "doc-1"
    assert hits[0]["source_name"] == "Fort_Knox.pdf"
    assert hits[1]["document_id"] == "doc-2"


@pytest.mark.unit
def test_is_kb_search_empty_detects_empty_response() -> None:
    assert is_kb_search_empty("Ничего не найдено") is True
    assert is_kb_search_empty(SAMPLE_CONTEXT) is False


@pytest.mark.unit
def test_format_kb_hits_summary_truncates_long_lists() -> None:
    summary = format_kb_hits_summary(
        [{"document_id": f"doc-{index}"} for index in range(15)],
        max_ids=3,
    )

    assert summary.startswith("count=15 ids=['doc-0', 'doc-1', 'doc-2']")
    assert summary.endswith("...+12")
