#!/usr/bin/env python3
"""Compare two dialogue run manifests for tuning dispatcher / dialogue manager."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

RUNS_DIR = Path(__file__).resolve().parent / "runs"


def resolve_run_id(runs_dir: Path, run_id: str) -> Path:
    if run_id == "latest":
        latest = runs_dir / "latest"
        if latest.is_symlink():
            return runs_dir / latest.readlink()
        if latest.exists():
            return latest
    path = runs_dir / run_id
    if not (path / "manifest.json").exists():
        raise FileNotFoundError(f"Run not found: {run_id} ({path})")
    return path


def load_manifest(run_dir: Path) -> dict:
    return json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))


def fmt_delta(new: float | int | None, old: float | int | None) -> str:
    if new is None or old is None:
        return "—"
    d = float(new) - float(old)
    sign = "+" if d > 0 else ""
    return f"{sign}{d:.1f}"


def main() -> int:
    parser = argparse.ArgumentParser(description="Compare dialogue run manifests")
    parser.add_argument("run_a", help="Run id or 'latest'")
    parser.add_argument("run_b", nargs="?", help="Run id to compare against (default: previous by name)")
    parser.add_argument("--runs-dir", type=Path, default=RUNS_DIR)
    args = parser.parse_args()

    dir_a = resolve_run_id(args.runs_dir, args.run_a)
    man_a = load_manifest(dir_a)

    if args.run_b:
        dir_b = resolve_run_id(args.runs_dir, args.run_b)
    else:
        siblings = sorted(p for p in args.runs_dir.iterdir() if p.is_dir() and (p / "manifest.json").exists())
        idx = next((i for i, p in enumerate(siblings) if p.name == dir_a.name), -1)
        if idx <= 0:
            print("No previous run to compare", file=sys.stderr)
            return 1
        dir_b = siblings[idx - 1]

    man_b = load_manifest(dir_b)
    ta = man_a.get("totals") or {}
    tb = man_b.get("totals") or {}

    print(f"A: {man_a['run_id']}  git={man_a.get('git_revision')}  model={man_a.get('environment', {}).get('llm_model')}")
    print(f"B: {man_b['run_id']}  git={man_b.get('git_revision')}  model={man_b.get('environment', {}).get('llm_model')}")
    print()
    print(f"{'metric':<28} {'A':>12} {'B':>12} {'delta':>12}")
    print("-" * 68)

    rows = [
        ("turns", ta.get("turns"), tb.get("turns")),
        ("wall_ms_sum", ta.get("wall_ms_sum"), tb.get("wall_ms_sum")),
        ("wall_ms_avg", ta.get("wall_ms_avg"), tb.get("wall_ms_avg")),
        ("tokens.total", (ta.get("tokens") or {}).get("total"), (tb.get("tokens") or {}).get("total")),
        ("tokens.prompt", (ta.get("tokens") or {}).get("prompt"), (tb.get("tokens") or {}).get("prompt")),
    ]
    for name, va, vb in rows:
        print(f"{name:<28} {str(va):>12} {str(vb):>12} {fmt_delta(va, vb):>12}")

    print()
    print("By agent (tokens total):")
    agents_a = (ta.get("by_agent") or {})
    agents_b = (tb.get("by_agent") or {})
    for author in sorted(set(agents_a) | set(agents_b)):
        va = (agents_a.get(author) or {}).get("tokens", {}).get("total", 0)
        vb = (agents_b.get(author) or {}).get("tokens", {}).get("total", 0)
        print(f"  {author:<24} {va:>8} {vb:>8}  {fmt_delta(va, vb)}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
