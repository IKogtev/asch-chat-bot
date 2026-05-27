import ast
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
    ["get_file_link"],
    {
        "logger": types.SimpleNamespace(debug=lambda *a, **k: None),
        "Annotated": Annotated,
        "List": list,
    },
)

get_file_link = _funcs["get_file_link"]


@pytest.mark.unit
def test_get_file_link_builds_relative_path() -> None:
    assert get_file_link("doc.pdf", ["kb", "folder"]) == "kb/folder/doc.pdf"
