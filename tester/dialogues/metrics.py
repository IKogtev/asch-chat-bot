"""Parse ADK /run events into tuning metrics (tokens, routes, per-agent latency)."""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class TokenUsage:
    prompt: int = 0
    candidates: int = 0
    total: int = 0
    cached: int = 0

    def add(self, other: TokenUsage) -> None:
        self.prompt += other.prompt
        self.candidates += other.candidates
        self.total += other.total
        self.cached += other.cached

    def to_dict(self) -> dict[str, int]:
        return {
            "prompt": self.prompt,
            "candidates": self.candidates,
            "total": self.total,
            "cached": self.cached,
        }


@dataclass
class AgentStep:
    author: str
    model_version: str = ""
    latency_ms: Optional[float] = None
    tokens: TokenUsage = field(default_factory=TokenUsage)
    finish_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "author": self.author,
            "model_version": self.model_version,
            "latency_ms": round(self.latency_ms, 1) if self.latency_ms is not None else None,
            "tokens": self.tokens.to_dict(),
            "finish_reason": self.finish_reason,
        }


@dataclass
class TurnMetrics:
    route: Optional[str] = None
    intent: Optional[str] = None
    search_query: Optional[str] = None
    agents: list[AgentStep] = field(default_factory=list)
    tokens: TokenUsage = field(default_factory=TokenUsage)
    wall_ms: Optional[float] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "route": self.route,
            "intent": self.intent,
            "search_query": self.search_query,
            "wall_ms": round(self.wall_ms, 1) if self.wall_ms is not None else None,
            "tokens": self.tokens.to_dict(),
            "agents": [a.to_dict() for a in self.agents],
        }


def parse_usage(raw: Any) -> TokenUsage:
    if not isinstance(raw, dict):
        return TokenUsage()
    return TokenUsage(
        prompt=int(raw.get("promptTokenCount") or raw.get("prompt_tokens") or 0),
        candidates=int(raw.get("candidatesTokenCount") or raw.get("completion_tokens") or 0),
        total=int(raw.get("totalTokenCount") or raw.get("total_tokens") or 0),
        cached=int(raw.get("cachedContentTokenCount") or raw.get("cached_tokens") or 0),
    )


def parse_dispatcher(events: list[dict[str, Any]]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    for event in events:
        if event.get("author") != "dispatcher_agent":
            continue
        content = event.get("content") or {}
        for part in content.get("parts") or []:
            if not isinstance(part, dict):
                continue
            text = part.get("text") or ""
            route_m = re.search(r'"route"\s*:\s*"([^"]+)"', text)
            intent_m = re.search(r'"intent"\s*:\s*"([^"]+)"', text)
            query_m = re.search(r'"search_query"\s*:\s*"([^"]*)"', text)
            route = route_m.group(1) if route_m else None
            intent = intent_m.group(1) if intent_m else None
            search_query = query_m.group(1) if query_m else None
            if route or intent:
                return route, intent, search_query or None
    return None, None, None


def analyze_turn_events(events: list[Any], wall_ms: Optional[float] = None) -> TurnMetrics:
    normalized: list[dict[str, Any]] = [e for e in events if isinstance(e, dict)]
    metrics = TurnMetrics(wall_ms=wall_ms)
    metrics.route, metrics.intent, metrics.search_query = parse_dispatcher(normalized)

    llm_events: list[dict[str, Any]] = []
    for event in normalized:
        author = str(event.get("author") or "")
        if not author or author == "root_agent":
            continue
        usage = event.get("usageMetadata") or event.get("usage_metadata")
        if usage or author.endswith("_agent"):
            llm_events.append(event)

    prev_ts: Optional[float] = None
    for event in llm_events:
        author = str(event.get("author") or "")
        usage = parse_usage(event.get("usageMetadata") or event.get("usage_metadata"))
        metrics.tokens.add(usage)

        ts_raw = event.get("timestamp")
        ts = float(ts_raw) if ts_raw is not None else None
        latency_ms: Optional[float] = None
        if ts is not None and prev_ts is not None:
            latency_ms = (ts - prev_ts) * 1000
        if ts is not None:
            prev_ts = ts

        metrics.agents.append(
            AgentStep(
                author=author,
                model_version=str(event.get("modelVersion") or event.get("model_version") or ""),
                latency_ms=latency_ms,
                tokens=usage,
                finish_reason=str(event.get("finishReason") or event.get("finish_reason") or ""),
            )
        )

    return metrics


def aggregate_tokens(turns: list[TurnMetrics]) -> TokenUsage:
    total = TokenUsage()
    for turn in turns:
        total.add(turn.tokens)
    return total


def aggregate_by_agent(turns: list[TurnMetrics]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for turn in turns:
        for step in turn.agents:
            bucket = out.setdefault(
                step.author,
                {
                    "calls": 0,
                    "latency_ms_sum": 0.0,
                    "latency_ms_count": 0,
                    "tokens": TokenUsage(),
                    "models": set(),
                },
            )
            bucket["calls"] += 1
            if step.latency_ms is not None:
                bucket["latency_ms_sum"] += step.latency_ms
                bucket["latency_ms_count"] += 1
            bucket["tokens"].add(step.tokens)
            if step.model_version:
                bucket["models"].add(step.model_version)

    result: dict[str, dict[str, Any]] = {}
    for author, bucket in sorted(out.items()):
        count = bucket["latency_ms_count"]
        result[author] = {
            "calls": bucket["calls"],
            "latency_ms_avg": round(bucket["latency_ms_sum"] / count, 1) if count else None,
            "tokens": bucket["tokens"].to_dict(),
            "models": sorted(bucket["models"]),
        }
    return result


def route_intent_matrix(turns: list[tuple[str, TurnMetrics]]) -> list[dict[str, Any]]:
    """Per user turn: route/intent counts for dispatcher tuning."""
    rows: list[dict[str, Any]] = []
    for user_text, tm in turns:
        rows.append(
            {
                "user": user_text,
                "route": tm.route,
                "intent": tm.intent,
                "search_query": tm.search_query,
                "wall_ms": round(tm.wall_ms, 1) if tm.wall_ms is not None else None,
                "tokens_total": tm.tokens.total,
            }
        )
    return rows


def git_revision(repo_root: Any) -> Optional[str]:
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return out.strip() or None
    except Exception:
        return None


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


_IRON_PHRASES = (
    "чем могу помочь",
    "я помогаю находить документы",
    "могу показать карточку продукта",
    "ваш помощник",
    "рада, что вы здесь",
)


def analyze_human_likeness(
    turns: list[tuple[str, str, dict[str, str]]],
) -> dict[str, Any]:
    """
    Heuristic «живость» по транскрипту сценария.
    turns: (user, answer, persona) per turn.
    """
    first_name = ""
    for _, _, persona in turns:
        if persona.get("first_name"):
            first_name = persona["first_name"]
            break

    name_hits = 0
    iron_hits = 0
    duplicate_answers = 0
    answers: list[str] = []
    prev = ""

    for _, answer, _ in turns:
        text = (answer or "").lower()
        answers.append(answer or "")
        if first_name and re.search(rf"\b{re.escape(first_name.lower())}\b", text):
            name_hits += 1
        for phrase in _IRON_PHRASES:
            if phrase in text:
                iron_hits += 1
        if prev and answer.strip() == prev.strip():
            duplicate_answers += 1
        prev = answer or ""

    turn_count = len(turns) or 1
    name_ratio = name_hits / turn_count
    iron_ratio = iron_hits / turn_count

    score = 10.0
    score -= min(4.0, name_hits * 1.5)
    score -= min(2.0, iron_hits * 0.5)
    score -= min(2.0, duplicate_answers * 1.0)
    score = max(0.0, min(10.0, score))

    return {
        "score": round(score, 1),
        "name_in_replies": name_hits,
        "name_ratio": round(name_ratio, 2),
        "iron_phrase_hits": iron_hits,
        "duplicate_replies": duplicate_answers,
        "turns": turn_count,
    }


def aggregate_human_likeness(
    scenarios: list[Any],
) -> dict[str, Any]:
    """Aggregate from ScenarioRecord-like objects with .turns and .persona."""
    per_scenario: dict[str, Any] = {}
    scores: list[float] = []
    total_name = 0
    total_iron = 0
    total_turns = 0

    for scenario in scenarios:
        rows = [
            (t.user, t.answer, scenario.persona)
            for t in scenario.turns
            if t.answer and not t.error
        ]
        if not rows:
            continue
        stats = analyze_human_likeness(rows)
        per_scenario[scenario.id] = stats
        scores.append(stats["score"])
        total_name += stats["name_in_replies"]
        total_iron += stats["iron_phrase_hits"]
        total_turns += stats["turns"]

    avg = round(sum(scores) / len(scores), 1) if scores else None
    return {
        "avg_score": avg,
        "name_in_replies_total": total_name,
        "iron_phrase_hits_total": total_iron,
        "turns_total": total_turns,
        "per_scenario": per_scenario,
    }
