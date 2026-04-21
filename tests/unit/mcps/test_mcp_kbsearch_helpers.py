import ast
import re
import types
from pathlib import Path
from typing import Annotated

import pytest


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
    Path(__file__).resolve().parents[3] / "mcps" / "mcp-server-kbsearch" / "app" / "mcp-server-kbsearch_v2.py",
    [
        "get_file_link",
        "normalize_text",
        "expand_query_terms",
        "overlap_score",
        "phrase_score",
        "get_section_text",
        "metadata_to_searchable_fields",
        "low_info_penalty",
        "compute_lexical_score",
        "compute_final_score",
    ],
    {
        "re": re,
        "logger": types.SimpleNamespace(debug=lambda *a, **k: None),
        "Annotated": Annotated,
        "List": list,
    },
)

get_file_link = _funcs["get_file_link"]
normalize_text = _funcs["normalize_text"]
expand_query_terms = _funcs["expand_query_terms"]
overlap_score = _funcs["overlap_score"]
phrase_score = _funcs["phrase_score"]
get_section_text = _funcs["get_section_text"]
metadata_to_searchable_fields = _funcs["metadata_to_searchable_fields"]
low_info_penalty = _funcs["low_info_penalty"]
compute_lexical_score = _funcs["compute_lexical_score"]
compute_final_score = _funcs["compute_final_score"]


@pytest.mark.unit
def test_get_file_link_builds_relative_path() -> None:
    assert get_file_link("doc.pdf", ["kb", "folder"]) == "kb/folder/doc.pdf"


@pytest.mark.unit
def test_normalize_text_lowercases_and_cleans_separators() -> None:
    result = normalize_text("Fort-Knox/Alpha+123")

    assert result == "fort knox alpha 123"


@pytest.mark.unit
def test_expand_query_terms_adds_known_aliases() -> None:
    result = expand_query_terms("Форт Нокс")

    assert "форт нокс" in result
    assert "fort knox" in result


@pytest.mark.unit
def test_overlap_and_phrase_score_return_positive_match() -> None:
    assert overlap_score("Это Fort Knox продукт", ["fort", "knox"]) > 0
    assert phrase_score("Описание Fort Knox продукта", "Fort Knox") == 1.0


@pytest.mark.unit
def test_metadata_to_searchable_fields_and_section_text_convert_metadata() -> None:
    metadata = {"source": "doc.pdf", "section_path": ["A", "B"]}

    source, section = metadata_to_searchable_fields(metadata)

    assert source == "doc.pdf"
    assert section == "A B"
    assert get_section_text(["X", "Y"]) == "X Y"


@pytest.mark.unit
def test_compute_lexical_and_final_score_favor_relevant_text() -> None:
    lexical = compute_lexical_score(
        "fort knox",
        "Описание продукта Fort Knox для инвестиций",
        {"source": "fort_knox.pdf", "section_path": ["products"]},
    )

    final_score = compute_final_score(0.6, lexical, "Описание продукта Fort Knox для инвестиций")

    assert lexical > 0
    assert final_score > 0.6
    assert low_info_penalty("очень коротко") <= 1.0
