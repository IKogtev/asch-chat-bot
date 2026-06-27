"""Save dialogue run bundle: manifest + summary + per-turn metrics for tuning."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .metrics import (
    TurnMetrics,
    aggregate_by_agent,
    aggregate_human_likeness,
    aggregate_tokens,
    git_revision,
    route_intent_matrix,
    utc_now_iso,
)


@dataclass
class TurnRecord:
    index: int
    user: str
    answer: str = ""
    notes: str = ""
    checks: dict[str, bool] = field(default_factory=dict)
    error: Optional[str] = None
    metrics: TurnMetrics = field(default_factory=TurnMetrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "user": self.user,
            "answer": self.answer,
            "notes": self.notes,
            "checks": self.checks,
            "error": self.error,
            "metrics": self.metrics.to_dict(),
        }


@dataclass
class ScenarioRecord:
    id: str
    title: str
    description: str
    tags: list[str]
    source_file: str
    persona: dict[str, str]
    passed: bool = False
    turns: list[TurnRecord] = field(default_factory=list)

    @property
    def turn_count(self) -> int:
        return len(self.turns)

    @property
    def wall_ms(self) -> float:
        return sum(t.metrics.wall_ms or 0 for t in self.turns)

    @property
    def tokens_total(self) -> int:
        return sum(t.metrics.tokens.total for t in self.turns)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "tags": self.tags,
            "source_file": self.source_file,
            "persona": self.persona,
            "passed": self.passed,
            "turn_count": self.turn_count,
            "wall_ms": round(self.wall_ms, 1),
            "tokens_total": self.tokens_total,
            "turns": [t.to_dict() for t in self.turns],
        }


@dataclass
class RunBundle:
    run_id: str
    started_at: str
    finished_at: str = ""
    adk_base: str = ""
    app_name: str = "agent"
    scenario_ids: list[str] = field(default_factory=list)
    environment: dict[str, Any] = field(default_factory=dict)
    scenarios: list[ScenarioRecord] = field(default_factory=list)
    git_revision: Optional[str] = None

    @property
    def passed_count(self) -> int:
        return sum(1 for s in self.scenarios if s.passed)

    @property
    def total_turns(self) -> int:
        return sum(s.turn_count for s in self.scenarios)

    @property
    def total_wall_ms(self) -> float:
        return sum(s.wall_ms for s in self.scenarios)

    def all_turn_metrics(self) -> list[TurnMetrics]:
        return [t.metrics for s in self.scenarios for t in s.turns]

    def build_totals(self) -> dict[str, Any]:
        turn_metrics = self.all_turn_metrics()
        tokens = aggregate_tokens(turn_metrics)
        wall_values = [t.wall_ms for t in turn_metrics if t.wall_ms is not None]
        return {
            "scenarios": len(self.scenarios),
            "scenarios_passed": self.passed_count,
            "turns": self.total_turns,
            "wall_ms_sum": round(self.total_wall_ms, 1),
            "wall_ms_avg": round(sum(wall_values) / len(wall_values), 1) if wall_values else None,
            "wall_ms_max": round(max(wall_values), 1) if wall_values else None,
            "tokens": tokens.to_dict(),
            "by_agent": aggregate_by_agent(turn_metrics),
            "human_likeness": aggregate_human_likeness(self.scenarios),
        }

    def build_manifest(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "git_revision": self.git_revision,
            "adk_base": self.adk_base,
            "app_name": self.app_name,
            "scenario_ids": self.scenario_ids,
            "environment": self.environment,
            "totals": self.build_totals(),
            "scenarios_summary": [
                {
                    "id": s.id,
                    "title": s.title,
                    "passed": s.passed,
                    "turns": s.turn_count,
                    "wall_ms": round(s.wall_ms, 1),
                    "tokens_total": s.tokens_total,
                    "tags": s.tags,
                }
                for s in self.scenarios
            ],
        }

    def build_summary_md(self) -> str:
        totals = self.build_totals()
        lines = [
            f"# Dialogue run `{self.run_id}`",
            "",
            f"- Started: {self.started_at}",
            f"- Finished: {self.finished_at}",
            f"- Git: `{self.git_revision or '—'}`",
            f"- ADK: `{self.adk_base}` · app `{self.app_name}`",
            "",
            "## Environment",
            "",
        ]
        env = self.environment
        lines.append(f"- LLM model: `{env.get('llm_model', '—')}`")
        lines.append(f"- LLM URL: `{env.get('llm_api_url', '—')}`")
        flags = env.get("feature_flags") or {}
        if flags:
            lines.append("- Feature flags:")
            for k, v in sorted(flags.items()):
                lines.append(f"  - `{k}` = `{v}`")
        mcp = env.get("mcp") or {}
        if mcp:
            lines.append("- MCP:")
            for k, v in sorted(mcp.items()):
                lines.append(f"  - `{k}` = `{v or '(off)'}`")

        lines.extend(
            [
                "",
                "## Totals",
                "",
                f"| metric | value |",
                f"|--------|------:|",
                f"| scenarios | {totals['scenarios']} ({totals['scenarios_passed']} passed) |",
                f"| turns | {totals['turns']} |",
                f"| wall time (sum) | {totals['wall_ms_sum']:.0f} ms |",
                f"| wall time (avg/turn) | {totals['wall_ms_avg'] or '—'} ms |",
                f"| wall time (max/turn) | {totals['wall_ms_max'] or '—'} ms |",
                f"| tokens prompt | {totals['tokens']['prompt']} |",
                f"| tokens candidates | {totals['tokens']['candidates']} |",
                f"| tokens total | {totals['tokens']['total']} |",
                f"| tokens cached | {totals['tokens']['cached']} |",
                "",
            ]
        )
        human = totals.get("human_likeness") or {}
        if human.get("avg_score") is not None:
            lines.extend(
                [
                    "## Human-likeness (heuristic)",
                    "",
                    f"- Avg score: **{human['avg_score']}/10**",
                    f"- Name in replies: {human.get('name_in_replies_total', 0)} / "
                    f"{human.get('turns_total', 0)} turns",
                    f"- Iron phrase hits: {human.get('iron_phrase_hits_total', 0)}",
                    "",
                ]
            )
        lines.extend(
            [
                "## By agent (dispatcher / intention chain)",
                "",
                "| agent | calls | avg latency ms | tokens total | models |",
                "|-------|------:|---------------:|-------------:|--------|",
            ]
        )
        for author, bucket in (totals.get("by_agent") or {}).items():
            models = ", ".join(bucket.get("models") or []) or "—"
            avg = bucket.get("latency_ms_avg")
            avg_s = f"{avg:.0f}" if avg is not None else "—"
            lines.append(
                f"| `{author}` | {bucket['calls']} | {avg_s} | "
                f"{bucket['tokens']['total']} | {models} |"
            )

        lines.extend(["", "## Scenarios", ""])
        for s in self.scenarios:
            status = "PASS" if s.passed else "FAIL"
            lines.append(
                f"### [{status}] {s.id}: {s.title} "
                f"({s.turn_count} turns · {s.wall_ms:.0f} ms · {s.tokens_total} tok)"
            )
            lines.append("")
            lines.append(f"_{s.description}_")
            lines.append("")
            lines.append("| # | user | route/intent | ms | tok | answer |")
            lines.append("|---|------|--------------|---:|----:|--------|")
            for t in s.turns:
                ri = "—"
                if t.metrics.route or t.metrics.intent:
                    ri = f"{t.metrics.route or '?'}/{t.metrics.intent or '?'}"
                ms = f"{t.metrics.wall_ms:.0f}" if t.metrics.wall_ms is not None else "—"
                tok = t.metrics.tokens.total
                user = t.user.replace("|", "\\|")[:50]
                ans = (t.answer or t.error or "—").replace("\n", " ").replace("|", "\\|")[:80]
                lines.append(f"| {t.index} | `{user}` | {ri} | {ms} | {tok} | {ans} |")
            lines.append("")

            routing = route_intent_matrix([(t.user, t.metrics) for t in s.turns])
            lines.append("<details><summary>Routing matrix (tuning)</summary>")
            lines.append("")
            lines.append("```json")
            lines.append(json.dumps(routing, ensure_ascii=False, indent=2))
            lines.append("```")
            lines.append("")
            lines.append("</details>")
            lines.append("")

        lines.extend(
            [
                "## Tuning notes",
                "",
                "- Compare `manifest.json` → `totals.by_agent` across runs after prompt changes.",
                "- Use `routing/` JSONL per scenario to review dispatcher route/intent drift.",
                "- High `dispatcher_agent` tokens + wrong route → tune `dispatcher_agent_prompt.md`.",
                "- High latency on `kb_answer_agent` with smalltalk → check intent misroute.",
                "",
            ]
        )
        return "\n".join(lines)


def collect_environment() -> dict[str, Any]:
    return {
        "llm_model": os.getenv("LLM_API_MODEL", ""),
        "llm_api_url": os.getenv("LLM_API_URL", ""),
        "llm_voice_model": os.getenv("LLM_VOICE_MODEL", ""),
        "agent_prompts_dir": os.getenv("AGENT_PROMPTS_DIR", ""),
        "feature_flags": {
            "ACK_ENABLED": os.getenv("ACK_ENABLED", "true"),
            "ADK_ROUTE_ACK_ENABLED": os.getenv("ADK_ROUTE_ACK_ENABLED", "false"),
            "DIALOGUE_MANAGER_ENABLED": os.getenv("DIALOGUE_MANAGER_ENABLED", "true"),
            "VOICE_AGENT_ENABLED": os.getenv("VOICE_AGENT_ENABLED", "false"),
            "AGENT_DIALOG_MEMORY_MAX_TURNS": os.getenv("AGENT_DIALOG_MEMORY_MAX_TURNS", "3"),
        },
        "mcp": {
            "KBSEARCH_MCP_URL": os.getenv("KBSEARCH_MCP_URL", ""),
            "FAQSEARCH_MCP_URL": os.getenv("FAQSEARCH_MCP_URL", ""),
            "DBHUB_MCP_URL": os.getenv("DBHUB_MCP_URL", ""),
        },
        "profile_defaults": {
            "ADK_TEST_FIRST_NAME": os.getenv("ADK_TEST_FIRST_NAME", ""),
            "ADK_TEST_LAST_NAME": os.getenv("ADK_TEST_LAST_NAME", ""),
        },
    }


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")


def save_run_bundle(bundle: RunBundle, runs_dir: Path, *, save_events: bool = True) -> Path:
    run_dir = runs_dir / bundle.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "events").mkdir(exist_ok=True)
    (run_dir / "routing").mkdir(exist_ok=True)

    manifest = bundle.build_manifest()
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "summary.md").write_text(bundle.build_summary_md(), encoding="utf-8")
    (run_dir / "results.json").write_text(
        json.dumps([s.to_dict() for s in bundle.scenarios], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    for scenario in bundle.scenarios:
        routing_path = run_dir / "routing" / f"{scenario.id}.jsonl"
        with routing_path.open("w", encoding="utf-8") as fh:
            for turn in scenario.turns:
                row = {
                    "turn": turn.index,
                    "user": turn.user,
                    "route": turn.metrics.route,
                    "intent": turn.metrics.intent,
                    "search_query": turn.metrics.search_query,
                    "wall_ms": turn.metrics.wall_ms,
                    "tokens": turn.metrics.tokens.to_dict(),
                    "agents": [a.to_dict() for a in turn.metrics.agents],
                    "checks": turn.checks,
                    "error": turn.error,
                }
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    if save_events:
        events_dir = run_dir / "events"
        for scenario in bundle.scenarios:
            for turn in scenario.turns:
                raw = getattr(turn, "_raw_events", None)
                if raw:
                    path = events_dir / f"{scenario.id}__turn-{turn.index:02d}.json"
                    path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")

    latest = runs_dir / "latest"
    if latest.is_symlink() or latest.exists():
        latest.unlink()
    latest.symlink_to(run_dir.name)

    return run_dir


def create_run_bundle(adk_base: str, app_name: str, repo_root: Path) -> RunBundle:
    return RunBundle(
        run_id=new_run_id(),
        started_at=utc_now_iso(),
        adk_base=adk_base,
        app_name=app_name,
        git_revision=git_revision(repo_root),
        environment=collect_environment(),
    )
