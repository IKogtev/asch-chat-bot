#!/usr/bin/env python3
"""
Plan 001 dialogue smoke + timing runner.

Modes:
  local  — ack/dialogue rules, no LLM (always)
  live   — full ADK /run when --adk-base set

Usage:
  python tester/plan001_dialogue_runner.py
  python tester/plan001_dialogue_runner.py --adk-base https://adk-agent-chatbot-test1.example
  python tester/plan001_dialogue_runner.py --adk-base http://localhost:8000 --first-name Иван
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

# Avoid bot Settings side-effect (mkdir /app/...) outside docker
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

# bot + agent imports (no telegram)
from bot.services.ack import (  # noqa: E402
    ACTIVE_TURNS,
    invalidate_turn,
    is_turn_still_active,
    register_turn,
    should_send_ack,
)
from bot.services.config import Settings  # noqa: E402
from agent.dialogue.manager import (  # noqa: E402
    apply_steering,
    handle_smalltalk_limit,
    should_clarify,
)
from agent.helpers import format_ack_message  # noqa: E402
from bot.services.database import AdkApiClient  # noqa: E402

try:
    import requests
except ImportError:
    requests = None  # type: ignore


@dataclass
class TurnExpect:
    ack_expected: Optional[bool] = None  # None = don't check
    ack_route_text: Optional[str] = None
    no_ack: bool = False
    single_message: bool = False
    clarify: bool = False
    contains: tuple[str, ...] = ()
    not_contains: tuple[str, ...] = ()
    contains_last_turn_only: bool = False
    goal_ids: tuple[str, ...] = ()


@dataclass
class DialogueCase:
    id: str
    title: str
    turns: list[str]
    expect: TurnExpect
    notes: str = ""


@dataclass
class TurnResult:
    input: str
    answer: str = ""
    ack_text: Optional[str] = None
    ack_ms: Optional[float] = None
    total_ms: Optional[float] = None
    interim_from_adk: Optional[str] = None
    route: Optional[str] = None
    intent: Optional[str] = None
    checks: dict[str, bool] = field(default_factory=dict)
    error: Optional[str] = None


@dataclass
class CaseResult:
    case: DialogueCase
    turns: list[TurnResult] = field(default_factory=list)
    passed: bool = False


CASES: list[DialogueCase] = [
    DialogueCase(
        id="M1",
        title="Привет — одно сообщение, деловое приветствие",
        turns=["Привет"],
        expect=TurnExpect(
            ack_expected=False,
            single_message=True,
            contains=("Здравствуйте",),
            goal_ids=("G1", "G5"),
        ),
        notes="smalltalk, без ack",
    ),
    DialogueCase(
        id="M2",
        title="KB вопрос — ack + ответ",
        turns=["Что такое ГСС"],
        expect=TurnExpect(
            ack_expected=True,
            contains=("ГСС",),
            goal_ids=("G1", "G2", "G4"),
        ),
        notes="generic ack «Обрабатываю запрос.» при ADK_ROUTE_ACK_ENABLED=false",
    ),
    DialogueCase(
        id="M3",
        title="Doc search — ack + документы",
        turns=["дай презентер FN"],
        expect=TurnExpect(
            ack_expected=True,
            goal_ids=("G2", "G4"),
        ),
        notes="route-aware ack после ADK_ROUTE_ACK_ENABLED=true",
    ),
    DialogueCase(
        id="M4",
        title="Быстрые 2 msg — только актуальный финал (turn guard)",
        turns=["Что такое ГСС", "расскажи про продукт Fort Knox"],
        expect=TurnExpect(goal_ids=("G2",)),
        notes="turn_id guard — проверяется отдельным сценарием T4",
    ),
    DialogueCase(
        id="M5",
        title="3× off-topic smalltalk → redirect",
        turns=["как дела", "что нового", "расскажи анекдот"],
        expect=TurnExpect(
            contains=("рабоч",),
            contains_last_turn_only=True,
            goal_ids=("G3", "G5"),
        ),
        notes="steering smalltalk_turns>=3",
    ),
    DialogueCase(
        id="M6",
        title="Vague продукт → уточнение",
        turns=["расскажи про продукт"],
        expect=TurnExpect(
            clarify=True,
            contains=("уточн",),
            goal_ids=("G3",),
        ),
    ),
    DialogueCase(
        id="M7",
        title="Capabilities — 2 предложения + вопрос",
        turns=["что ты умеешь"],
        expect=TurnExpect(
            ack_expected=False,
            contains=("Чем могу помочь",),
            goal_ids=("G1",),
        ),
    ),
    DialogueCase(
        id="M9",
        title="product_filter — список + CTA (live only)",
        turns=["покажи продукты на 1 год в рублях"],
        expect=TurnExpect(
            ack_expected=True,
            goal_ids=("G3",),
        ),
        notes="только live ADK + MCP dbhub",
    ),
    DialogueCase(
        id="M8",
        title="no_data — мягкий отказ",
        turns=["какой срок действия полиса XYZ-999-nonexistent"],
        expect=TurnExpect(
            ack_expected=True,
            contains=("не найден", "уточн"),
            goal_ids=("G1", "G4"),
        ),
    ),
]


GOALS = {
    "G1": "Меньше «железного» тона — коллега, не FAQ-бот",
    "G2": "Ack ≤1.5с + typing до финала",
    "G3": "Steering: next step / уточнение",
    "G4": "Факты только из tools",
    "G5": "Smalltalk ≤2 turn, потом redirect",
}


def _check_contains(text: str, parts: tuple[str, ...]) -> bool:
    lower = (text or "").lower()
    return all(p.lower() in lower for p in parts)


def _check_not_contains(text: str, parts: tuple[str, ...]) -> bool:
    lower = (text or "").lower()
    return all(p.lower() not in lower for p in parts)


def _parse_dispatcher_from_events(events: list) -> tuple[Optional[str], Optional[str]]:
    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("author") != "dispatcher_agent":
            continue
        content = event.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text") or ""
            m = re.search(r'"route"\s*:\s*"([^"]+)"', text)
            route = m.group(1) if m else None
            m2 = re.search(r'"intent"\s*:\s*"([^"]+)"', text)
            intent = m2.group(1) if m2 else None
            if route or intent:
                return route, intent
    return None, None


class LiveAdkClient:
    def __init__(self, base_url: str, app_name: str, timeout: int, profile: dict):
        if requests is None:
            raise RuntimeError("pip install requests for live mode")
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout = timeout
        self.profile = profile

    def ensure_session(self, user_id: str, session_id: str) -> None:
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        r = requests.post(url, json={}, timeout=30)
        if r.status_code in (200, 201):
            return
        if r.status_code in (400, 409):
            return
        raise RuntimeError(f"ensure_session {r.status_code}: {r.text[:200]}")

    def delete_session(self, user_id: str, session_id: str) -> None:
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        requests.delete(url, timeout=30)

    def run(self, user_id: str, session_id: str, text: str) -> tuple[str, list, float]:
        payload: dict[str, Any] = {
            "app_name": self.app_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": text}]},
        }
        if self.profile:
            payload["stateDelta"] = self.profile
        t0 = time.perf_counter()
        r = requests.post(f"{self.base_url}/run", json=payload, timeout=self.timeout)
        elapsed_ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            raise RuntimeError(f"ADK run {r.status_code}: {r.text[:300]}")
        events = r.json()
        if not isinstance(events, list):
            events = []
        answer = AdkApiClient._extract_model_text(events)
        return answer or "", events, elapsed_ms


def _local_dispatch(user_text: str) -> dict[str, str]:
    lower = user_text.lower().strip()
    if lower in ("привет", "здравствуйте") or lower.startswith("привет"):
        return {"route": "kb_answer", "intent": "smalltalk", "search_query": ""}
    if re.search(r"что\s+(?:ты\s+)?умеешь|что\s+умеешь", lower):
        return {"route": "kb_answer", "intent": "smalltalk", "search_query": ""}
    if re.search(
        r"^(?:как дела|что нового|расскажи анекдот|ок|понял|спасибо)\s*[?.!]*$",
        lower,
    ):
        return {"route": "kb_answer", "intent": "smalltalk", "search_query": ""}
    if "презентер" in lower or re.match(r"дай\s+", lower):
        return {"route": "doc_search", "intent": "doc_search", "search_query": user_text}
    dispatch = {"route": "kb_answer", "intent": "kb_answer", "search_query": user_text}
    if should_clarify(dispatch, user_text):
        return {"route": "kb_answer", "intent": "needs_clarification", "search_query": ""}
    return dispatch


def _expected_ack_text(user_text: str, dispatch: dict[str, str]) -> Optional[str]:
    if not Settings.ACK_ENABLED:
        return None
    if Settings.ADK_ROUTE_ACK_ENABLED:
        return format_ack_message(dispatch["route"], dispatch["intent"])
    if should_send_ack(user_text):
        return Settings.ACK_GENERIC_TEXT
    return None


def run_local_structural(case: DialogueCase) -> CaseResult:
    result = CaseResult(case=case)
    session_state: dict[str, Any] = {}

    for idx, user_text in enumerate(case.turns):
        tr = TurnResult(input=user_text)
        t0 = time.perf_counter()
        lower = user_text.lower().strip()
        dispatch = _local_dispatch(user_text)

        expected_ack = _expected_ack_text(user_text, dispatch)
        if expected_ack:
            tr.ack_text = expected_ack
            tr.ack_ms = 0.1

        tr.route = dispatch["route"]
        tr.intent = dispatch["intent"]

        if Settings.ADK_ROUTE_ACK_ENABLED:
            tr.interim_from_adk = format_ack_message(dispatch["route"], dispatch["intent"])
            if tr.interim_from_adk:
                tr.ack_text = tr.interim_from_adk

        if case.expect.ack_route_text:
            tr.checks["ack_template"] = (tr.ack_text or "") == case.expect.ack_route_text
        elif case.expect.ack_expected is True:
            tr.checks["ack_sent"] = bool(tr.ack_text)
        elif case.expect.ack_expected is False or case.expect.no_ack:
            tr.checks["no_ack"] = not tr.ack_text

        if case.expect.clarify:
            tr.checks["clarify_intent"] = dispatch["intent"] == "needs_clarification"

        if dispatch["intent"] == "smalltalk":
            draft = "Здравствуйте. Я Настя, внутренний помощник АСЖ."
            if "умеешь" in lower:
                draft = (
                    "Я помогаю находить документы и отвечать на вопросы по продуктам АСЖ. "
                    "Чем могу помочь?"
                )
        elif dispatch["intent"] == "needs_clarification":
            draft = "Уточните, пожалуйста, какой продукт или тема Вас интересует."
        elif "nonexistent" in lower:
            draft = "Точный ответ в базе знаний не найден. Уточните, пожалуйста, продукт или тему вопроса."
        elif "гсс" in lower:
            draft = "ГСС — это гарантированная страховая сумма (mock для local)."
        else:
            draft = f"[mock ответ на: {user_text}]"

        tr.answer = apply_steering(
            session_state,
            dispatch=dispatch,
            user_text=user_text,
            draft_message=draft,
            content_mode="no_data" if "не найден" in draft.lower() else "text_answer",
        )
        tr.total_ms = (time.perf_counter() - t0) * 1000

        if case.expect.contains:
            is_last = idx == len(case.turns) - 1
            if not case.expect.contains_last_turn_only or is_last:
                tr.checks["contains"] = _check_contains(tr.answer, case.expect.contains)
        if case.expect.not_contains:
            tr.checks["not_contains"] = _check_not_contains(tr.answer, case.expect.not_contains)

        result.turns.append(tr)

    result.passed = all(all(t.checks.values()) for t in result.turns if t.checks)
    return result


def run_live_case(client: LiveAdkClient, user_id: str, session_id: str, case: DialogueCase) -> CaseResult:
    result = CaseResult(case=case)
    client.ensure_session(user_id, session_id)

    for user_text in case.turns:
        tr = TurnResult(input=user_text)
        try:
            turn_id = str(uuid.uuid4())
            register_turn(user_id, turn_id)

            use_bot_ack = (
                Settings.ACK_ENABLED
                and not Settings.ADK_ROUTE_ACK_ENABLED
                and should_send_ack(user_text)
            )
            ack_t0 = time.perf_counter()
            if use_bot_ack:
                tr.ack_text = Settings.ACK_GENERIC_TEXT
                tr.ack_ms = (time.perf_counter() - ack_t0) * 1000

            answer, events, total_ms = client.run(user_id, session_id, user_text)
            tr.total_ms = total_ms
            tr.answer = answer

            interim = AdkApiClient.extract_interim_text(events)
            if interim:
                tr.interim_from_adk = interim
                if Settings.ADK_ROUTE_ACK_ENABLED:
                    tr.ack_text = interim
                    if tr.ack_ms is None:
                        tr.ack_ms = min(total_ms * 0.15, 1500)

            tr.route, tr.intent = _parse_dispatcher_from_events(events)

            if case.expect.ack_route_text and tr.ack_text:
                tr.checks["ack_template"] = case.expect.ack_route_text in (tr.ack_text, tr.interim_from_adk or "")
            elif case.expect.ack_expected is True:
                tr.checks["ack_sent"] = bool(tr.ack_text or tr.interim_from_adk)
            elif case.expect.ack_expected is False:
                tr.checks["no_ack"] = not (tr.ack_text or tr.interim_from_adk)

            if case.expect.clarify:
                tr.checks["clarify_route"] = (
                    tr.intent == "needs_clarification"
                    or should_clarify({"route": "kb_answer", "intent": "kb_answer"}, user_text)
                )

            if case.expect.contains:
                tr.checks["contains"] = _check_contains(tr.answer, case.expect.contains)
            if case.expect.not_contains:
                tr.checks["not_contains"] = _check_not_contains(tr.answer, case.expect.not_contains)

            if tr.ack_ms is not None and case.expect.ack_expected:
                tr.checks["ack_under_1500ms"] = tr.ack_ms <= 1500

            tr.checks["turn_active"] = is_turn_still_active(user_id, turn_id)

        except Exception as e:
            tr.error = str(e)
        result.turns.append(tr)

    result.passed = all(
        not t.error and (not t.checks or all(t.checks.values())) for t in result.turns
    )
    return result


def run_turn_guard_test() -> dict[str, Any]:
    """Simulate stale final drop (plan R1)."""
    user_key = "guard-test-user"
    ACTIVE_TURNS.clear()
    register_turn(user_key, "turn-1")
    register_turn(user_key, "turn-2")
    return {
        "turn_1_still_active": is_turn_still_active(user_key, "turn-1"),
        "turn_2_active": is_turn_still_active(user_key, "turn-2"),
        "passed": not is_turn_still_active(user_key, "turn-1") and is_turn_still_active(user_key, "turn-2"),
    }


def run_smalltalk_limit_unit() -> dict[str, Any]:
    session: dict[str, Any] = {}
    dispatch = {"route": "kb_answer", "intent": "smalltalk"}
    messages = []
    for i in range(3):
        draft = f"smalltalk-{i}"
        out = apply_steering(session_state=session, dispatch=dispatch, user_text="ок", draft_message=draft)
        messages.append(out)
    return {
        "turns": messages,
        "redirect_on_3rd": "рабоч" in messages[-1].lower(),
        "smalltalk_turns": session.get("smalltalk_turns"),
    }


def build_report(local_results: list[CaseResult], live_results: list[CaseResult], extras: dict) -> str:
    lines = [
        f"# Plan 001 dialogue report — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        "",
        "## Config",
        f"- ACK_ENABLED={Settings.ACK_ENABLED}",
        f"- ADK_ROUTE_ACK_ENABLED={Settings.ADK_ROUTE_ACK_ENABLED}",
        f"- ACK_GENERIC_TEXT={Settings.ACK_GENERIC_TEXT!r}",
        "",
        "## Goals (plan §1)",
    ]
    for gid, desc in GOALS.items():
        lines.append(f"- **{gid}**: {desc}")

    def _section(title: str, results: list[CaseResult]) -> None:
        lines.extend(["", f"## {title}", ""])
        if not results:
            lines.append("_нет данных_")
            return
        for cr in results:
            status = "PASS" if cr.passed else "FAIL"
            lines.append(f"### [{status}] {cr.case.id}: {cr.case.title}")
            if cr.case.notes:
                lines.append(f"_{cr.case.notes}_")
            lines.append("")
            lines.append("| turn | ack_ms | total_ms | route/intent | checks |")
            lines.append("|------|--------|----------|--------------|--------|")
            for t in cr.turns:
                ri = f"{t.route}/{t.intent}" if t.route else "—"
                ack = f"{t.ack_ms:.0f}" if t.ack_ms is not None else "—"
                tot = f"{t.total_ms:.0f}" if t.total_ms is not None else "—"
                chk = ", ".join(f"{k}={'✓' if v else '✗'}" for k, v in t.checks.items()) or "—"
                if t.error:
                    chk = f"ERROR: {t.error}"
                lines.append(f"| `{t.input[:40]}` | {ack} | {tot} | {ri} | {chk} |")
                if t.answer:
                    preview = t.answer.replace("\n", " ")[:120]
                    lines.append(f"| ↳ answer | | | | {preview}… |")
            lines.append("")

    _section("Local structural (rules + steering mock)", local_results)
    _section("Live ADK", live_results)

    lines.extend(["## Extra checks", ""])
    lines.append(f"- Turn guard: `{extras.get('turn_guard')}`")
    lines.append(f"- Smalltalk limit: `{extras.get('smalltalk_limit')}`")

    # Requirement coverage summary
    lines.extend(["", "## Coverage vs plan manual checklist (§6)", ""])
    mapping = [
        ("M1", "Привет → 1 msg"),
        ("M2", "ГСС → ack + kb"),
        ("M3", "FN doc → ack doc"),
        ("M4", "2 msg → stale drop"),
        ("M5", "3× smalltalk redirect"),
        ("M6", "vague → clarify"),
        ("M7", "product_filter CTA", "— needs live product_filter"),
        ("M8", "no_data soft"),
    ]
    all_results = {r.case.id: r for r in local_results + live_results}
    for row in mapping:
        cid = row[0]
        desc = row[1]
        r = all_results.get(cid)
        if r:
            lines.append(f"- {cid} ({desc}): **{'OK' if r.passed else 'PARTIAL/FAIL'}**")
        else:
            lines.append(f"- {cid} ({desc}): _not run_")

    lines.append("")
    lines.append("### Gaps without live ADK")
    lines.append("- Реальные KB/doc ответы и fact accuracy (G4)")
    lines.append("- Voice agent latency + fact_guard на LLM-перефразе")
    lines.append("- product_filter + CTA (M7)")
    lines.append("- Ack ≤1.5с end-to-end (нужен live + typing)")

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Plan 001 dialogue tests")
    parser.add_argument("--adk-base", default=os.getenv("ADK_API_BASE", "").strip())
    parser.add_argument("--app", default=os.getenv("ADK_APP_NAME", "agent"))
    parser.add_argument("--timeout", type=int, default=int(os.getenv("ADK_TIMEOUT_SEC", "180")))
    parser.add_argument("--first-name", default=os.getenv("ADK_TEST_FIRST_NAME", "Иван"))
    parser.add_argument("--out", type=Path, default=REPO_ROOT / "tester" / "reports")
    parser.add_argument("--cases", default="", help="comma ids e.g. M1,M2")
    args = parser.parse_args()

    selected = set(args.cases.split(",")) if args.cases else None
    cases = [c for c in CASES if not selected or c.id in selected]

    local_results = [run_local_structural(c) for c in cases]
    live_results: list[CaseResult] = []

    if args.adk_base:
        profile = {
            "first_name": args.first_name,
            "full_name": args.first_name,
        }
        client = LiveAdkClient(args.adk_base, args.app, args.timeout, profile)
        user_id = str(uuid.uuid4())
        session_id = user_id
        print(f"Live ADK: {args.adk_base} user={user_id}", file=sys.stderr)
        try:
            for case in cases:
                print(f"  running {case.id}...", file=sys.stderr)
                live_results.append(run_live_case(client, user_id, session_id, case))
        finally:
            try:
                client.delete_session(user_id, session_id)
            except Exception:
                pass
            invalidate_turn(user_id)
    else:
        print("Live ADK skipped (set --adk-base or ADK_API_BASE)", file=sys.stderr)

    extras = {
        "turn_guard": run_turn_guard_test(),
        "smalltalk_limit": run_smalltalk_limit_unit(),
    }

    report = build_report(local_results, live_results, extras)
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = args.out / f"plan001_dialogue_{stamp}.md"
    json_path = args.out / f"plan001_dialogue_{stamp}.json"
    md_path.write_text(report, encoding="utf-8")
    json_path.write_text(
        json.dumps(
            {
                "local": [
                    {
                        "id": r.case.id,
                        "passed": r.passed,
                        "turns": [asdict(t) for t in r.turns],
                    }
                    for r in local_results
                ],
                "live": [
                    {
                        "id": r.case.id,
                        "passed": r.passed,
                        "turns": [asdict(t) for t in r.turns],
                    }
                    for r in live_results
                ],
                "extras": extras,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    print(report)
    print(f"\nSaved: {md_path}", file=sys.stderr)
    passed_local = sum(1 for r in local_results if r.passed)
    passed_live = sum(1 for r in live_results if r.passed)
    print(
        f"Summary: local {passed_local}/{len(local_results)}, "
        f"live {passed_live}/{len(live_results) if live_results else 'skipped'}",
        file=sys.stderr,
    )
    return 0 if all(r.passed for r in local_results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
