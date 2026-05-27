"""Load shared modules from repo root ``utils/`` when running kbsearch locally."""

from __future__ import annotations

import importlib.util
from pathlib import Path


def load_shared_module(module_file: str, module_name: str):
    shared = Path(__file__).resolve().parents[4] / "utils" / module_file
    spec = importlib.util.spec_from_file_location(module_name, shared)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load shared utils module: {shared}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod
