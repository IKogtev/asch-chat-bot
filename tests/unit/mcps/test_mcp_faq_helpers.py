import ast
import types
from pathlib import Path

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


runtime = types.SimpleNamespace(initialized=True, last_update="2026-04-17T10:00:00")
indexer = types.SimpleNamespace(get_active_metadata=lambda: {"documents_count": 7, "index_status": "initialized"})

_funcs = _load_functions(
    Path(__file__).resolve().parents[3] / "mcps" / "mcp-server-faq" / "mcp_faq_v2.py",
    ["get_faq_status"],
    {
        "Dict": dict,
        "faq_runtime": runtime,
        "indexer": indexer,
        "metadata_document_count": lambda metadata: metadata.get("documents_count")
        or metadata.get("document_count")
        or 0,
        "USE_QDRANT": False,
        "qdrant_init_task": None,
        "FAQ_QDRANT_RETRY_INTERVAL": 10.0,
        "FAQ_QDRANT_INIT_TIMEOUT": 600.0,
    },
)

get_faq_status = _funcs["get_faq_status"]


@pytest.mark.unit
def test_get_faq_status_returns_runtime_and_metadata_snapshot() -> None:
    result = get_faq_status()

    assert result["initialized"] is True
    assert result["documents_count"] == 7
    assert result["metadata"]["index_status"] == "initialized"
    assert result["qdrant_init"]["use_qdrant"] is False
