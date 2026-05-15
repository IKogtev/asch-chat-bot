"""Unit tests for legacy-collection fallback rescoring in kb_search."""

import ast
import re
import types
from pathlib import Path
from typing import Annotated

import pytest

KBSEARCH_V2 = (
    Path(__file__).resolve().parents[3]
    / "mcps"
    / "mcp-server-kbsearch"
    / "app"
    / "mcp-server-kbsearch_v2.py"
)


def _load_functions(file_path: Path, names: list[str], extra_globals: dict):
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    selected = [
        node
        for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in names
    ]
    module = ast.Module(body=selected, type_ignores=[])
    namespace = {"__builtins__": __builtins__}
    namespace.update(extra_globals)
    exec(compile(module, str(file_path), "exec"), namespace)
    return namespace


_funcs = _load_functions(
    KBSEARCH_V2,
    [
        "normalize_text",
        "expand_query_terms",
        "overlap_score",
        "phrase_score",
        "get_section_text",
        "metadata_to_searchable_fields",
        "low_info_penalty",
        "compute_lexical_score",
        "compute_final_score",
        "rescore_legacy_retriever_nodes",
    ],
    {
        "re": re,
        "Annotated": Annotated,
        "List": list,
        "SIMILARITY_CUTOFF": 0.35,
    },
)

rescore_legacy_retriever_nodes = _funcs["rescore_legacy_retriever_nodes"]


def _mock_node(
    *,
    content: str,
    source: str,
    document_id: str,
    score: float,
    section_path: list[str] | None = None,
):
    return types.SimpleNamespace(
        get_content=lambda c=content: c,
        metadata={
            "source": source,
            "document_id": document_id,
            "section_path": section_path or ["products"],
        },
        score=score,
    )


@pytest.mark.unit
def test_rescore_legacy_empty_nodes_returns_empty_list() -> None:
    assert rescore_legacy_retriever_nodes([], "fort knox", "hybrid", top_k=5) == []


@pytest.mark.unit
def test_rescore_legacy_dense_orders_by_dense_score_only() -> None:
    high_dense = _mock_node(
        content="общий текст без совпадений",
        source="other.pdf",
        document_id="doc-a",
        score=0.95,
    )
    low_dense_named = _mock_node(
        content="кратко",
        source="Fort_Knox_storiz.pdf",
        document_id="doc-b",
        score=0.40,
    )
    results = rescore_legacy_retriever_nodes(
        [low_dense_named, high_dense],
        "fort knox storiz",
        "dense",
        top_k=5,
        similarity_cutoff=0.0,
    )

    assert len(results) == 2
    assert results[0]["metadata"]["document_id"] == "doc-a"
    assert results[0]["score"] == pytest.approx(0.95)
    assert "lexical_score" not in results[0]


@pytest.mark.unit
def test_rescore_legacy_hybrid_boosts_filename_match_over_dense() -> None:
    high_dense = _mock_node(
        content="общий текст без совпадений",
        source="other.pdf",
        document_id="doc-a",
        score=0.95,
    )
    low_dense_named = _mock_node(
        content="Описание материалов Fort Knox для соцсетей",
        source="Fort_Knox_storiz.pdf",
        document_id="doc-b",
        score=0.40,
    )
    results = rescore_legacy_retriever_nodes(
        [high_dense, low_dense_named],
        "fort knox storiz",
        "hybrid",
        top_k=5,
        similarity_cutoff=0.0,
    )

    assert len(results) == 2
    assert results[0]["metadata"]["document_id"] == "doc-b"
    assert results[0]["lexical_score"] > 0
    assert results[0]["score"] > results[1]["score"]


@pytest.mark.unit
def test_rescore_legacy_respects_top_k_before_cutoff() -> None:
    nodes = [
        _mock_node(
            content=f"Fort Knox doc {i}",
            source=f"fort_{i}.pdf",
            document_id=f"doc-{i}",
            score=0.9 - i * 0.05,
        )
        for i in range(5)
    ]
    results = rescore_legacy_retriever_nodes(
        nodes,
        "fort knox",
        "dense",
        top_k=2,
        similarity_cutoff=0.0,
    )

    assert len(results) == 2
    assert results[0]["rank"] == 0
    assert results[1]["rank"] == 1


@pytest.mark.unit
def test_rescore_legacy_similarity_cutoff_filters_weak_hits() -> None:
    weak = _mock_node(
        content="x",
        source="unrelated.pdf",
        document_id="weak",
        score=0.1,
    )
    results = rescore_legacy_retriever_nodes(
        [weak],
        "fort knox",
        "dense",
        top_k=5,
        similarity_cutoff=0.35,
    )

    assert results == []


@pytest.mark.unit
def test_rescore_legacy_hybrid_appends_all_scored_nodes_not_empty_on_match() -> None:
    """Regression: hybrid branch must append every node to rescored (not drop list)."""
    node = _mock_node(
        content="Fort Knox презентер",
        source="Fort_Knox_presenter.pdf",
        document_id="doc-1",
        score=0.5,
    )
    results = rescore_legacy_retriever_nodes(
        [node],
        "fort knox презентер",
        "hybrid",
        top_k=10,
        similarity_cutoff=0.0,
    )

    assert len(results) == 1
    assert results[0]["dense_score"] == pytest.approx(0.5)
    assert "lexical_score" in results[0]
