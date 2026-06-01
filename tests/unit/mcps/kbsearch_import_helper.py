"""Import helpers for mcp-server-kbsearch modules (avoids root ``utils`` package shadowing)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

ROOT = Path(__file__).resolve().parents[3]
KBSEARCH_APP = ROOT / "mcps" / "mcp-server-kbsearch" / "app"


def ensure_kbsearch_app_on_path() -> None:
    kb_app = str(KBSEARCH_APP)
    if sys.path[0] != kb_app:
        if kb_app in sys.path:
            sys.path.remove(kb_app)
        sys.path.insert(0, kb_app)

    utils_mod = sys.modules.get("utils")
    if utils_mod is None:
        return
    utils_file = getattr(utils_mod, "__file__", "") or ""
    if kb_app.replace("\\", "/") in utils_file.replace("\\", "/"):
        return
    for name in list(sys.modules):
        if name == "utils" or name.startswith("utils."):
            del sys.modules[name]


def load_kbsearch_module(relative_path: str, module_name: str) -> ModuleType:
    """Load a module file from kbsearch ``app/`` under a unique module name."""
    path = KBSEARCH_APP / relative_path
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def import_kbsearch_attr(relative_path: str, module_name: str, attr: str):
    ensure_kbsearch_app_on_path()
    mod = load_kbsearch_module(relative_path, module_name)
    return getattr(mod, attr)
