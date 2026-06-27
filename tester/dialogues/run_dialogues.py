#!/usr/bin/env python3
"""
Прогон реалистичных диалогов + сохранение run bundle для тюнинга dispatcher/dialogue manager.

Usage:
  python tester/dialogues/run_dialogues.py --list
  python tester/dialogues/run_dialogues.py --adk-base http://127.0.0.1:8080
  python tester/dialogues/run_dialogues.py --adk-base http://127.0.0.1:8080 --ids real-01
  python tester/dialogues/compare_runs.py latest prev-run-id
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"
RUNS_DIR = Path(__file__).resolve().parent / "runs"

os.environ.setdefault("UPLOAD_NEWS", str(REPO_ROOT / ".test_tmp" / "upload"))
os.environ.setdefault(
    "BOT_START_MESSAGE_FILE",
    str(REPO_ROOT / ".test_tmp" / "bot_start_message.md"),
)
os.environ.setdefault(
    "BOT_HELP_MESSAGE_FILE",
    str(REPO_ROOT / ".test_tmp" / "bot_help_message.md"),
)
(REPO_ROOT / ".test_tmp").mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(REPO_ROOT))

from bot.services.database import AdkApiClient  # noqa: E402

from tester.dialogues.metrics import analyze_turn_events, utc_now_iso  # noqa: E402
from tester.dialogues.run_bundle import (  # noqa: E402
    RunBundle,
    ScenarioRecord,
    TurnRecord,
    create_run_bundle,
    save_run_bundle,
)

try:
    import requests
except ImportError:
    requests = None  # type: ignore


@dataclass
class TurnSpec:
    user: str
    notes: str = ""
    expect_contains: tuple[str, ...] = ()
    expect_not_contains: tuple[str, ...] = ()


@dataclass
class DialogueScenario:
    id: str
    title: str
    description: str
    persona: dict[str, str]
    tags: list[str]
    turns: list[TurnSpec]
    source_file: str


def _parse_turn(raw: dict[str, Any]) -> TurnSpec:
    return TurnSpec(
        user=str(raw.get("user") or "").strip(),
        notes=str(raw.get("notes") or ""),
        expect_contains=tuple(raw.get("expect_contains") or ()),
        expect_not_contains=tuple(raw.get("expect_not_contains") or ()),
    )


def load_scenario(path: Path) -> DialogueScenario:
    data = json.loads(path.read_text(encoding="utf-8"))
    turns = [_parse_turn(t) for t in data.get("turns") or []]
    if not turns:
        raise ValueError(f"{path.name}: empty turns")
    for t in turns:
        if not t.user:
            raise ValueError(f"{path.name}: turn with empty user")
    return DialogueScenario(
        id=str(data.get("id") or path.stem),
        title=str(data.get("title") or path.stem),
        description=str(data.get("description") or ""),
        persona={k: str(v) for k, v in (data.get("persona") or {}).items() if v},
        tags=list(data.get("tags") or []),
        turns=turns,
        source_file=path.name,
    )


def load_all_scenarios(ids: Optional[set[str]] = None) -> list[DialogueScenario]:
    paths = sorted(SCENARIOS_DIR.glob("*.json"))
    if not paths:
        raise FileNotFoundError(f"No scenarios in {SCENARIOS_DIR}")
    out: list[DialogueScenario] = []
    for path in paths:
        scenario = load_scenario(path)
        if ids and scenario.id not in ids:
            continue
        out.append(scenario)
    if ids:
        missing = ids - {s.id for s in out}
        if missing:
            raise ValueError(f"Unknown scenario ids: {sorted(missing)}")
    return out


def build_profile(default_persona: dict[str, str], scenario: DialogueScenario) -> dict[str, str]:
    fn = (
        scenario.persona.get("first_name")
        or default_persona.get("first_name")
        or os.getenv("ADK_TEST_FIRST_NAME", "Дмитрий")
    ).strip()
    ln = (
        scenario.persona.get("last_name")
        or default_persona.get("last_name")
        or os.getenv("ADK_TEST_LAST_NAME", "")
    ).strip()
    profile = {
        "first_name": fn,
        "last_name": ln,
        "full_name": scenario.persona.get("full_name") or f"{fn} {ln}".strip(),
    }
    for key in ("username", "region"):
        val = scenario.persona.get(key) or default_persona.get(key) or os.getenv(f"ADK_TEST_{key.upper()}", "")
        if str(val).strip():
            profile[key] = str(val).strip()
    return profile


def _check_contains(text: str, parts: tuple[str, ...]) -> bool:
    lower = (text or "").lower()
    return all(p.lower() in lower for p in parts)


def _check_not_contains(text: str, parts: tuple[str, ...]) -> bool:
    lower = (text or "").lower()
    return all(p.lower() not in lower for p in parts)


class AdkRunner:
    def __init__(self, base_url: str, app_name: str, timeout: int):
        if requests is None:
            raise RuntimeError("pip install requests")
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout = timeout

    def ensure_session(self, user_id: str, session_id: str) -> None:
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        r = requests.post(url, json={}, timeout=30)
        if r.status_code in (200, 201, 400, 409):
            return
        raise RuntimeError(f"ensure_session {r.status_code}: {r.text[:200]}")

    def delete_session(self, user_id: str, session_id: str) -> None:
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        try:
            requests.delete(url, timeout=30)
        except Exception:
            pass

    def run_turn(
        self,
        user_id: str,
        session_id: str,
        text: str,
        profile: dict[str, str],
    ) -> tuple[str, list[Any], float]:
        payload: dict[str, Any] = {
            "app_name": self.app_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": text}]},
            "stateDelta": profile,
        }
        t0 = time.perf_counter()
        r = requests.post(f"{self.base_url}/run", json=payload, timeout=self.timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            raise RuntimeError(f"ADK run {r.status_code}: {r.text[:400]}")
        events = r.json()
        if not isinstance(events, list):
            events = []
        answer = AdkApiClient._extract_model_text(events) or ""
        return answer, events, elapsed_ms


def run_scenario(
    client: AdkRunner,
    scenario: DialogueScenario,
    profile: dict[str, str],
) -> ScenarioRecord:
    user_id = str(uuid.uuid4())
    session_id = user_id
    client.ensure_session(user_id, session_id)
    record = ScenarioRecord(
        id=scenario.id,
        title=scenario.title,
        description=scenario.description,
        tags=scenario.tags,
        source_file=scenario.source_file,
        persona=profile,
    )
    try:
        for idx, turn in enumerate(scenario.turns, start=1):
            tr = TurnRecord(index=idx, user=turn.user, notes=turn.notes)
            try:
                answer, events, wall_ms = client.run_turn(user_id, session_id, turn.user, profile)
                tr.answer = answer
                tr.metrics = analyze_turn_events(events, wall_ms=wall_ms)
                tr._raw_events = events  # type: ignore[attr-defined]
                if turn.expect_contains:
                    tr.checks["contains"] = _check_contains(answer, turn.expect_contains)
                if turn.expect_not_contains:
                    tr.checks["not_contains"] = _check_not_contains(answer, turn.expect_not_contains)
            except Exception as exc:
                tr.error = str(exc)
            record.turns.append(tr)

        checks = [v for t in record.turns for v in t.checks.values()]
        errors = [t for t in record.turns if t.error]
        record.passed = not errors and all(checks) if checks else not errors
    finally:
        client.delete_session(user_id, session_id)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description="Run dialogue scenarios + save tuning run bundle")
    parser.add_argument("--list", action="store_true", help="List scenarios and exit")
    parser.add_argument("--adk-base", default=os.getenv("ADK_API_BASE", "").strip())
    parser.add_argument("--app", default=os.getenv("ADK_APP_NAME", "agent"))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("ADK_TIMEOUT_SEC", "180")))
    parser.add_argument("--ids", default="", help="Comma-separated scenario ids, e.g. real-01,real-11")
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=RUNS_DIR,
        help="Directory for run bundles (default: tester/dialogues/runs)",
    )
    parser.add_argument(
        "--no-events",
        action="store_true",
        help="Skip saving raw ADK events (smaller bundle)",
    )
    args = parser.parse_args()

    selected = set(x.strip() for x in args.ids.split(",") if x.strip()) or None
    scenarios = load_all_scenarios(selected)

    if args.list:
        print(f"Scenarios in {SCENARIOS_DIR}:\n")
        for s in scenarios:
            print(f"  {s.id:8}  {len(s.turns):2} turns  {s.source_file}")
            print(f"           {s.title}")
        return 0

    if not args.adk_base:
        print("Set --adk-base or ADK_API_BASE (or use --list)", file=sys.stderr)
        return 2

    default_persona = {
        "first_name": os.getenv("ADK_TEST_FIRST_NAME", "Дмитрий"),
        "last_name": os.getenv("ADK_TEST_LAST_NAME", "Иванов"),
    }
    client = AdkRunner(args.adk_base, args.app, args.timeout)
    bundle = create_run_bundle(args.adk_base, args.app, REPO_ROOT)
    bundle.scenario_ids = [s.id for s in scenarios]

    print(f"Run {bundle.run_id} — ADK {args.adk_base} — {len(scenarios)} scenario(s)", file=sys.stderr)
    for scenario in scenarios:
        profile = build_profile(default_persona, scenario)
        print(f"  {scenario.id}: {scenario.title} ({len(scenario.turns)} turns)...", file=sys.stderr)
        record = run_scenario(client, scenario, profile)
        bundle.scenarios.append(record)
        mark = "OK" if record.passed else "FAIL"
        print(
            f"    -> {mark} ({record.wall_ms:.0f} ms · {record.tokens_total} tok)",
            file=sys.stderr,
        )

    bundle.finished_at = utc_now_iso()
    run_dir = save_run_bundle(bundle, args.runs_dir, save_events=not args.no_events)

    totals = bundle.build_totals()
    print(f"\nBundle: {run_dir}", file=sys.stderr)
    print(f"  summary.md · manifest.json · results.json · routing/", file=sys.stderr)
    print(
        f"  {totals['turns']} turns · {totals['wall_ms_sum']:.0f} ms · "
        f"{totals['tokens']['total']} tokens",
        file=sys.stderr,
    )
    return 0 if bundle.passed_count == len(bundle.scenarios) else 1


if __name__ == "__main__":
    raise SystemExit(main())
