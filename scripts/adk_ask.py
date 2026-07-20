#!/usr/bin/env python3
"""
Отправка вопросов напрямую в ADK (минуя Telegram), сбор ответов/таймингов
и LLM-оценка ok/не ok по 10-балльной шкале (как в Jenkins tester).

Примеры:
  # Excel как в Jenkins (Вопросы / Ожидаемые ответы / Критерий успеха)
  python scripts/adk_ask.py \\
    --excel "C:\\Users\\Ivan\\Documents\\Alpha_ins\\QA_short.xlsx" \\
    --base https://adk-agent-chatbot-test1.sandbox-2.wwwnstcloud.ru \\
    --out-dir out/adk_ask

  # текстовый файл без оценки
  python scripts/adk_ask.py --questions scripts/sample_questions.txt --no-evaluate

  # один вопрос
  python scripts/adk_ask.py -q "Привет! Что ты умеешь?" --no-evaluate

Нужны env (как у tester):
  ADK_API_BASE, LLM_API_KEY, LLM_API_URL, LLM_API_MODEL

Выходные файлы в --out-dir:
  answers.jsonl / timings.jsonl
  timings_by_question.csv / summary_by_agent.csv|md
  evaluations.jsonl / evaluation_report.xlsx / evaluation_summary.md
  run.log
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, TextIO, Tuple
from uuid import UUID, uuid4

import requests

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parents[0] / ".env")
except ImportError:
    pass

DEFAULT_USER_ID = "00000000-0000-4000-8000-000000000001"
TIMING_STATE_DELTA_KEY = "_timing"

LLM_API_URL = os.getenv("LLM_API_URL", "https://api.llm.nstcloud.ru/v1").strip()
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip().strip('"').strip("'")
LLM_API_MODEL = os.getenv("LLM_API_MODEL", "Qwen/Qwen3-30B-A3B").strip().strip('"').strip("'")

KNOWN_AGENTS = (
    "owasp",
    "dispatcher",
    "doc_search",
    "kb_answer",
    "product_selection",
)

EVALUATION_SYSTEM_PROMPT = """
You are an expert evaluator of AI assistant responses.

Your task: assess whether the assistant produced enough correct information
to satisfy the reference answer and success criteria.

You will receive:
1. user_question
2. reference_answer
3. success_criteria
4. assistant_answer
5. assistant_answer_raw

Evaluation rules:
- Use assistant_answer as the final short answer.
- Use assistant_answer_raw as additional evidence with the detailed agent chain,
  intermediate technical output, structured data, tool results, and diagnostics.
- You MUST take assistant_answer_raw into account in every evaluation.
- If assistant_answer is brief, technical, or incomplete, but assistant_answer_raw
  contains enough correct information for the downstream bot to produce the expected
  user-facing answer, this may still be considered successful.
- Do not require an exact wording match with the reference answer.
- Focus on semantic correctness and satisfaction of the success criteria.
- If assistant_answer_raw contradicts assistant_answer, rely on the fuller factual
  content and mention the contradiction in explanation.
- If neither assistant_answer nor assistant_answer_raw contains enough information
  to satisfy the criteria, set meets_criteria to false.
- For document search scenarios, structured raw output with a relevant document list,
  document titles, snippets, ids, and evidence of successful retrieval is strong
  evidence of success, even if assistant_answer is only a short technical phrase.
- Penalize hallucinations, contradictions, missing required elements, and irrelevant output.

Evaluate using:
- accuracy (0–10): how precisely the available information matches the reference
- completeness (0–10): how fully the required result is covered
- relevance (0–10): how well it fits the question
- meets_criteria (true/false): meets success criteria or not
- overall_score (0–10): overall quality
- explanation: brief 1–3 sentence justification

⚠️ OUTPUT RULES (critical):
1. Respond with ONLY valid JSON, no text before or after.
2. Start directly with `{` and end with `}`.
3. No markdown, comments, or quotes around JSON.
4. Answer in Russian.

Example of valid output:
{"accuracy": 8, "completeness": 7, "relevance": 9, "meets_criteria": true, "overall_score": 8, "explanation": "Итоговый ответ короткий, но в raw есть достаточные данные для корректного пользовательского ответа."}
"""

logger = logging.getLogger("adk_ask")


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_user_id(value: str) -> str:
    s = (value or "").strip()
    try:
        return str(UUID(s))
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"user_id must be a UUID (doc_search persists user_id as UUID): {e}"
        ) from e


def build_profile_state_delta(
    *,
    first_name: str = "Local",
    last_name: str = "",
    username: str = "adk_ask",
    region: str = "",
) -> Dict[str, Any]:
    fn = first_name.strip() or "Local"
    ln = last_name.strip()
    out: Dict[str, Any] = {
        "first_name": fn,
        "last_name": ln,
        "full_name": f"{fn} {ln}".strip(),
        "username": username.strip(),
        "region": region.strip(),
    }
    return {k: v for k, v in out.items() if v not in ("", None)}


def _iter_state_deltas(event: dict) -> Iterable[dict]:
    candidates = []
    actions = event.get("actions")
    if isinstance(actions, dict):
        candidates.extend(
            [
                actions.get("stateDelta"),
                actions.get("state_delta"),
                actions.get("state_delta_json"),
            ]
        )
    candidates.extend([event.get("stateDelta"), event.get("state_delta")])
    for delta in candidates:
        if isinstance(delta, dict):
            yield delta


def extract_timing(events: list) -> Optional[Dict[str, Any]]:
    if not events:
        return None
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        for delta in _iter_state_deltas(event):
            timing = delta.get(TIMING_STATE_DELTA_KEY)
            if isinstance(timing, dict) and timing:
                return dict(timing)
    return None


def extract_answer(events: list) -> str:
    if not events:
        return ""
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        author = event.get("author")
        actions = event.get("actions") or {}
        is_final = bool(actions.get("end_of_agent") or actions.get("endOfAgent"))
        if author != "root_agent" or not is_final:
            continue
        content = event.get("content")
        if not isinstance(content, dict):
            continue
        parts = content.get("parts") or []
        out: List[str] = []
        for part in parts:
            if not isinstance(part, dict) or part.get("thought") is True:
                continue
            t = part.get("text")
            if t and str(t).strip():
                out.append(str(t).strip())
        if out:
            return "\n".join(out).strip()
    return ""


def extract_answer_raw(events: list) -> str:
    """Все non-thought text parts (как answer_raw в Jenkins tester)."""
    if not events:
        return ""
    out: List[str] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        content = event.get("content")
        if isinstance(content, dict):
            if isinstance(content.get("text"), str) and content["text"].strip():
                out.append(content["text"].strip())
            for part in content.get("parts") or []:
                if not isinstance(part, dict) or part.get("thought") is True:
                    continue
                t = part.get("text")
                if t and str(t).strip():
                    out.append(str(t).strip())
        elif isinstance(content, str) and content.strip():
            out.append(content.strip())
        if isinstance(event.get("text"), str) and event["text"].strip():
            out.append(event["text"].strip())
    return "\n".join(out).strip()


class JsonlWriter:
    def __init__(self, path: Path):
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: TextIO = path.open("a", encoding="utf-8")

    def write(self, record: Dict[str, Any]) -> None:
        self._fh.write(json.dumps(record, ensure_ascii=False) + "\n")
        self._fh.flush()

    def close(self) -> None:
        self._fh.close()

    def __enter__(self) -> "JsonlWriter":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()


class AdkClient:
    def __init__(
        self,
        base_url: str,
        app_name: str,
        timeout_sec: int,
        profile: Dict[str, Any],
    ):
        self.base_url = base_url.rstrip("/")
        self.app_name = app_name
        self.timeout_sec = timeout_sec
        self.profile = dict(profile)
        self.session = requests.Session()

    def healthcheck(self) -> bool:
        for path in ("/openapi.json", "/docs", "/"):
            url = f"{self.base_url}{path}"
            try:
                r = self.session.get(url, timeout=5)
                if r.status_code < 500:
                    logger.info("ADK reachable: GET %s -> %s", url, r.status_code)
                    return True
            except requests.RequestException as e:
                logger.debug("health %s failed: %s", url, e)
        return False

    def ensure_session(self, user_id: str, session_id: str) -> None:
        url = f"{self.base_url}/apps/{self.app_name}/users/{user_id}/sessions/{session_id}"
        r = self.session.post(url, json={}, timeout=min(self.timeout_sec, 30))
        if r.status_code in (200, 201):
            return
        if r.status_code in (400, 409):
            try:
                detail = str((r.json() or {}).get("detail") or "").lower()
            except (json.JSONDecodeError, ValueError, TypeError):
                detail = (r.text or "").lower()
            if "exists" in detail or "already" in detail:
                return
        raise RuntimeError(f"ensure_session failed: {r.status_code} {r.text[:300]}")

    def run(
        self, user_id: str, session_id: str, text: str
    ) -> Tuple[str, str, List[Dict[str, Any]]]:
        url = f"{self.base_url}/run"
        payload: Dict[str, Any] = {
            "app_name": self.app_name,
            "user_id": user_id,
            "session_id": session_id,
            "new_message": {"role": "user", "parts": [{"text": text}]},
        }
        if self.profile:
            payload["stateDelta"] = dict(self.profile)
        r = self.session.post(url, json=payload, timeout=self.timeout_sec)
        if r.status_code != 200:
            raise RuntimeError(f"ADK /run failed: {r.status_code} {r.text[:500]}")
        events = r.json()
        if not isinstance(events, list):
            events = []
        answer = extract_answer(events) or "Агент не вернул ответ"
        answer_raw = extract_answer_raw(events) or answer
        return answer, answer_raw, events


def _cell_str(value: Any) -> str:
    if value is None:
        return ""
    try:
        import pandas as pd

        if pd.isna(value):
            return ""
    except ImportError:
        pass
    return str(value).strip()


def load_excel_cases(path: Path) -> List[Dict[str, Any]]:
    try:
        import pandas as pd
    except ImportError as e:
        raise RuntimeError(
            "Для --excel нужны pandas и openpyxl: pip install pandas openpyxl"
        ) from e

    df = pd.read_excel(path)
    if "Use case" in df.columns:
        df["Use case"] = df["Use case"].ffill()
    if "Dialog tag" in df.columns:
        df["Dialog tag"] = df["Dialog tag"].ffill()
    if "№" not in df.columns or df["№"].isnull().any():
        df = df.copy()
        df["№"] = df.index + 1

    required = "Вопросы"
    if required not in df.columns:
        raise RuntimeError(
            f"В Excel нет колонки '{required}'. Найдено: {list(df.columns)}"
        )

    cases: List[Dict[str, Any]] = []
    for _, row in df.iterrows():
        question = _cell_str(row.get("Вопросы"))
        if not question:
            continue
        cases.append(
            {
                "n": int(row.get("№") or len(cases) + 1),
                "use_case": _cell_str(row.get("Use case")),
                "dialog_tag": _cell_str(row.get("Dialog tag")),
                "question": question,
                "reference_answer": _cell_str(row.get("Ожидаемые ответы")),
                "requirements": _cell_str(row.get("Критерий успеха")),
            }
        )
    return cases


def load_text_cases(path: Optional[Path], inline: List[str]) -> List[Dict[str, Any]]:
    questions: List[str] = []
    for q in inline:
        q = q.strip()
        if q:
            questions.append(q)
    if path is not None:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                questions.append(line)
    if not questions and not sys.stdin.isatty():
        for line in sys.stdin:
            line = line.strip()
            if line and not line.startswith("#"):
                questions.append(line)
    return [
        {
            "n": i,
            "use_case": "",
            "dialog_tag": "",
            "question": q,
            "reference_answer": "",
            "requirements": "",
        }
        for i, q in enumerate(questions, start=1)
    ]


class _SafeStreamHandler(logging.StreamHandler):
    """Не падает на UnicodeEncodeError в Git Bash / cp1251."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record) + self.terminator
            stream = self.stream
            try:
                stream.write(msg)
            except UnicodeEncodeError:
                encoding = getattr(stream, "encoding", None) or "utf-8"
                raw = msg.encode(encoding, errors="replace")
                buffer = getattr(stream, "buffer", None)
                if buffer is not None:
                    buffer.write(raw)
                else:
                    stream.write(raw.decode(encoding, errors="replace"))
            self.flush()
        except Exception:
            self.handleError(record)


def _configure_stdio_utf8() -> None:
    """Git Bash / Windows cp1251 ломается на кириллице и emoji — форсируем UTF-8."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def setup_logging(log_path: Path, verbose: bool) -> None:
    _configure_stdio_utf8()
    log_path.parent.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    sh = _SafeStreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_path, encoding="utf-8")
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
    logging.getLogger("urllib3").setLevel(logging.WARNING)


# --- timing summaries (agents) ---


def _as_int(value: Any) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def discover_agents(timing_rows: List[Dict[str, Any]]) -> List[str]:
    found: set[str] = set()
    for row in timing_rows:
        timing = row.get("timing") or {}
        if not isinstance(timing, dict):
            continue
        for key in timing:
            if isinstance(key, str) and key.endswith("_ms") and not key.endswith("_ttft_ms"):
                found.add(key[: -len("_ms")])
    extras = sorted(a for a in found if a not in KNOWN_AGENTS)
    return list(KNOWN_AGENTS) + extras


def _percentile_nearest(values: List[float], pct: float) -> Optional[float]:
    if not values:
        return None
    if len(values) == 1:
        return float(values[0])
    ordered = sorted(values)
    k = max(1, int(round(pct / 100.0 * len(ordered)))) - 1
    return float(ordered[min(k, len(ordered) - 1)])


def build_question_rows(
    timing_rows: List[Dict[str, Any]], agents: List[str]
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for row in timing_rows:
        timing = row.get("timing") if isinstance(row.get("timing"), dict) else {}
        out: Dict[str, Any] = {
            "n": row.get("n"),
            "wall_ms": row.get("wall_ms"),
            "route": timing.get("route", ""),
            "intent": timing.get("intent", ""),
            "error": row.get("error", ""),
            "question": row.get("question", ""),
        }
        for agent in agents:
            out[f"{agent}_ms"] = _as_int(timing.get(f"{agent}_ms"))
        rows.append(out)
    return rows


def build_agent_summary(
    timing_rows: List[Dict[str, Any]], agents: List[str]
) -> List[Dict[str, Any]]:
    buckets: Dict[str, List[float]] = {a: [] for a in agents}
    wall_ms: List[float] = []
    for row in timing_rows:
        w = _as_int(row.get("wall_ms"))
        if w is not None:
            wall_ms.append(float(w))
        timing = row.get("timing") if isinstance(row.get("timing"), dict) else {}
        for agent in agents:
            ms = _as_int(timing.get(f"{agent}_ms"))
            if ms is not None and ms > 0:
                buckets[agent].append(float(ms))

    def _stats(values: List[float]) -> Dict[str, Any]:
        if not values:
            return {"hits": 0, "sum": "", "avg": "", "p50": "", "max": ""}
        return {
            "hits": len(values),
            "sum": int(round(sum(values))),
            "avg": round(statistics.fmean(values), 1),
            "p50": round(_percentile_nearest(values, 50) or 0.0, 1),
            "max": int(round(max(values))),
        }

    summary: List[Dict[str, Any]] = []
    wall = _stats(wall_ms)
    wall_sum = float(wall["sum"] or 0)
    summary.append(
        {
            "agent": "wall (e2e)",
            "hits": wall["hits"],
            "sum_ms": wall["sum"],
            "avg_ms": wall["avg"],
            "p50_ms": wall["p50"],
            "max_ms": wall["max"],
            "share_of_wall_pct": 100.0 if wall["hits"] else "",
        }
    )
    for agent in agents:
        st = _stats(buckets[agent])
        share = ""
        if wall_sum > 0 and st["sum"] != "":
            share = round(100.0 * float(st["sum"]) / wall_sum, 1)
        summary.append(
            {
                "agent": agent,
                "hits": st["hits"],
                "sum_ms": st["sum"],
                "avg_ms": st["avg"],
                "p50_ms": st["p50"],
                "max_ms": st["max"],
                "share_of_wall_pct": share,
            }
        )
    return summary


def write_csv(path: Path, rows: List[Dict[str, Any]], fieldnames: List[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: row.get(k, "") for k in fieldnames})


def format_md_table(rows: List[Dict[str, Any]], fieldnames: List[str]) -> str:
    if not rows:
        return "_empty_\n"
    header = "| " + " | ".join(fieldnames) + " |"
    sep = "| " + " | ".join("---" for _ in fieldnames) + " |"
    lines = [header, sep]
    for row in rows:
        cells = []
        for key in fieldnames:
            val = row.get(key, "")
            if val is None:
                val = ""
            cells.append(str(val).replace("|", "\\|").replace("\n", " "))
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_agent_summaries(out_dir: Path, timing_rows: List[Dict[str, Any]]) -> Tuple[Path, Path, Path]:
    agents = discover_agents(timing_rows)
    question_rows = build_question_rows(timing_rows, agents)
    summary_rows = build_agent_summary(timing_rows, agents)

    q_fields = ["n", "wall_ms", "route", "intent"] + [f"{a}_ms" for a in agents] + ["error", "question"]
    q_compact = [{k: r.get(k, "") for k in q_fields} for r in question_rows]
    by_q_path = out_dir / "timings_by_question.csv"
    write_csv(by_q_path, q_compact, q_fields)

    summary_fields = ["agent", "hits", "sum_ms", "avg_ms", "p50_ms", "max_ms", "share_of_wall_pct"]
    summary_csv = out_dir / "summary_by_agent.csv"
    summary_md = out_dir / "summary_by_agent.md"
    write_csv(summary_csv, summary_rows, summary_fields)
    summary_md.write_text(
        "# Timing summary by agent\n\n"
        + format_md_table(summary_rows, summary_fields)
        + "\n## Per question (ms)\n\n"
        + format_md_table(q_compact, q_fields),
        encoding="utf-8",
    )
    logger.info(
        "Summary by agent:\n%s",
        format_md_table(summary_rows, ["agent", "hits", "avg_ms", "p50_ms", "max_ms", "share_of_wall_pct"]).rstrip(),
    )
    return by_q_path, summary_csv, summary_md


# --- LLM evaluation (same contract as tester/adk_agent_tester.py) ---


def validate_evaluation_result(result: Dict[str, Any]) -> Dict[str, Any]:
    required_keys = [
        "accuracy",
        "completeness",
        "relevance",
        "meets_criteria",
        "overall_score",
        "explanation",
    ]
    validated = dict(result or {})
    for key in required_keys:
        if key not in validated:
            if key == "meets_criteria":
                validated[key] = False
            elif key == "explanation":
                validated[key] = "Объяснение отсутствует"
            else:
                validated[key] = 0
        elif key in ("accuracy", "completeness", "relevance", "overall_score"):
            try:
                validated[key] = max(0.0, min(10.0, float(validated[key])))
            except (TypeError, ValueError):
                validated[key] = 0
        elif key == "meets_criteria":
            validated[key] = bool(validated[key])
        elif key == "explanation":
            validated[key] = str(validated[key]) if validated[key] else "Объяснение отсутствует"
    return validated


def create_default_evaluation(error_message: str) -> Dict[str, Any]:
    return {
        "accuracy": 0,
        "completeness": 0,
        "relevance": 0,
        "meets_criteria": False,
        "overall_score": 0,
        "explanation": f"Ошибка оценки: {error_message}",
    }


def format_evaluation_badge(evaluation: Dict[str, Any]) -> str:
    meets = bool(evaluation.get("meets_criteria", False))
    score = float(evaluation.get("overall_score", 0) or 0)
    # Без emoji: в Git Bash/Windows cp1251 StreamHandler падает на U+1F7E2
    marker = "OK" if meets else "FAIL"
    return f"{marker} | overall={score:.1f}/10"


def evaluate_answer_llm(
    *,
    question: str,
    reference_answer: str,
    requirements: str,
    answer: str,
    answer_raw: str,
    api_url: str,
    api_key: str,
    model: str,
    max_retries: int = 3,
    timeout_sec: int = 60,
) -> Dict[str, Any]:
    if not all([question, reference_answer, requirements]) or not (answer or answer_raw):
        return create_default_evaluation("Один или несколько обязательных параметров пусты")
    if not api_key:
        return create_default_evaluation("LLM_API_KEY не задан")

    base = api_url.rstrip("/")
    url = f"{base}/chat/completions"
    human = (
        "Evaluate the following AI assistant's response and return the result ONLY in JSON format:\n\n"
        f"Question: {question}\n\n"
        f"Reference answer: {reference_answer}\n\n"
        f"Success criteria: {requirements}\n\n"
        f"AI assistant's final answer: {answer}\n\n"
        f"AI assistant's detailed raw answer: {answer_raw}\n"
    )
    payload = {
        "model": model,
        "temperature": 0,
        "max_tokens": 2000,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": EVALUATION_SYSTEM_PROMPT},
            {"role": "user", "content": human},
        ],
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    for attempt in range(max_retries):
        try:
            r = requests.post(url, headers=headers, json=payload, timeout=timeout_sec)
            if r.status_code == 401:
                detail = (r.text or "")[:300]
                logger.error(
                    "LLM 401 for model=%r url=%s body=%s",
                    model,
                    url,
                    detail,
                )
                # Часто это не «плохой ключ», а team_model_access_denied из‑за префикса openai/
                return create_default_evaluation(
                    f"LLM 401 Unauthorized (model={model}): {detail}"
                )
            if r.status_code >= 400:
                logger.warning(
                    "LLM HTTP %s (попытка %d/%d): %s",
                    r.status_code,
                    attempt + 1,
                    max_retries,
                    r.text[:300],
                )
                time.sleep(2)
                continue
            data = r.json()
            response_text = (
                ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            ).strip()
            if not response_text or response_text in ("[]", "null"):
                time.sleep(1)
                continue
            json_match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", response_text, re.DOTALL)
            if not json_match:
                time.sleep(1)
                continue
            result = json.loads(json_match.group(0))
            if isinstance(result, dict) and any(
                k in result for k in ("accuracy", "completeness", "relevance", "overall_score")
            ):
                return validate_evaluation_result(result)
            time.sleep(1)
        except Exception as e:
            logger.warning(
                "Ошибка LLM (%s, попытка %d/%d): %s",
                type(e).__name__,
                attempt + 1,
                max_retries,
                e,
            )
            time.sleep(2)
    return create_default_evaluation(f"Не удалось получить корректный ответ после {max_retries} попыток")


def write_evaluation_report(
    out_dir: Path,
    rows: List[Dict[str, Any]],
    *,
    excel_stem: str,
) -> Tuple[Path, Path]:
    """Пишет evaluation_report.xlsx + evaluation_summary.md."""
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "evaluation_summary.md"
    xlsx_path = out_dir / f"evaluation_report_{excel_stem}_{datetime.now().strftime('%Y-%m-%d_%H-%M')}.xlsx"

    scored = [r for r in rows if r.get("evaluated")]
    ok_n = sum(1 for r in scored if r.get("meets_criteria"))
    bad_n = len(scored) - ok_n
    avg_score = (
        round(statistics.fmean(float(r.get("overall_score") or 0) for r in scored), 2)
        if scored
        else 0
    )

    by_uc: Dict[str, List[Dict[str, Any]]] = {}
    for r in scored:
        by_uc.setdefault(r.get("use_case") or "(без use case)", []).append(r)
    uc_rows = []
    for uc, items in by_uc.items():
        uc_rows.append(
            {
                "use_case": uc,
                "count": len(items),
                "ok": sum(1 for x in items if x.get("meets_criteria")),
                "not_ok": sum(1 for x in items if not x.get("meets_criteria")),
                "avg_overall": round(
                    statistics.fmean(float(x.get("overall_score") or 0) for x in items), 2
                ),
            }
        )

    detail_fields = [
        "n",
        "use_case",
        "dialog_tag",
        "question",
        "reference_answer",
        "requirements",
        "answer",
        "answer_raw",
        "wall_s",
        "accuracy",
        "completeness",
        "relevance",
        "meets_criteria",
        "overall_score",
        "explanation",
        "error",
    ]
    md = (
        f"# Evaluation summary\n\n"
        f"- scored: **{len(scored)}**\n"
        f"- ok (meets_criteria): **{ok_n}**\n"
        f"- not ok: **{bad_n}**\n"
        f"- avg overall_score: **{avg_score}/10**\n\n"
        f"## By use case\n\n"
        + format_md_table(uc_rows, ["use_case", "count", "ok", "not_ok", "avg_overall"])
        + "\n## Details\n\n"
        + format_md_table(
            [
                {
                    "n": r.get("n"),
                    "use_case": r.get("use_case"),
                    "meets_criteria": r.get("meets_criteria"),
                    "overall_score": r.get("overall_score"),
                    "question": (r.get("question") or "")[:80],
                    "explanation": (r.get("explanation") or "")[:120],
                }
                for r in rows
            ],
            ["n", "use_case", "meets_criteria", "overall_score", "question", "explanation"],
        )
    )
    md_path.write_text(md, encoding="utf-8")
    logger.info(
        "Evaluation: scored=%d ok=%d not_ok=%d avg_overall=%.2f/10",
        len(scored),
        ok_n,
        bad_n,
        avg_score,
    )
    logger.info(
        "By use case:\n%s",
        format_md_table(uc_rows, ["use_case", "count", "ok", "not_ok", "avg_overall"]).rstrip(),
    )

    try:
        import pandas as pd

        detail_df = pd.DataFrame([{k: r.get(k, "") for k in detail_fields} for r in rows])
        rename = {
            "n": "№",
            "use_case": "Код use case",
            "dialog_tag": "Dialog tag",
            "question": "Вопросы",
            "reference_answer": "Ожидаемые ответы",
            "requirements": "Критерий успеха",
            "answer": "Ответ ADK Agent",
            "answer_raw": "Ответ ADK Agent (raw)",
            "wall_s": "Время ответа (сек)",
            "accuracy": "Точность",
            "completeness": "Полнота",
            "relevance": "Релевантность",
            "meets_criteria": "Соответствие критериям",
            "overall_score": "Общая оценка",
            "explanation": "Объяснение",
            "error": "Ошибка ADK",
        }
        detail_df = detail_df.rename(columns=rename)
        summary_df = pd.DataFrame(uc_rows).rename(
            columns={
                "use_case": "Код use case",
                "count": "Число вопросов",
                "ok": "OK",
                "not_ok": "Не OK",
                "avg_overall": "Общая оценка (сред)",
            }
        )
        with pd.ExcelWriter(xlsx_path, engine="xlsxwriter") as writer:
            summary_df.to_excel(writer, sheet_name="Итоги", index=False)
            detail_df.to_excel(writer, sheet_name="Детали", index=False)
    except Exception as e:
        logger.warning("Не удалось сохранить xlsx (%s: %s). Есть CSV/MD.", type(e).__name__, e)
        csv_fallback = out_dir / "evaluation_report.csv"
        write_csv(csv_fallback, [{k: r.get(k, "") for k in detail_fields} for r in rows], detail_fields)
        xlsx_path = csv_fallback

    return xlsx_path, md_path


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Ask ADK directly (bypass Telegram), collect timings + LLM evaluation"
    )
    p.add_argument(
        "--base",
        default=os.getenv("ADK_API_BASE", "http://localhost:8000").strip(),
        help="ADK API base URL (env ADK_API_BASE)",
    )
    p.add_argument("--app", default=os.getenv("ADK_APP_NAME", "agent").strip())
    p.add_argument("--timeout", type=int, default=int(os.getenv("ADK_TIMEOUT_SEC", "180")))
    p.add_argument("--user-id", type=parse_user_id, default=DEFAULT_USER_ID)
    p.add_argument("--session-id", default="", help="Shared session when no Dialog tag")
    p.add_argument(
        "--fresh-session",
        action="store_true",
        help="New session for every question (ignored when Dialog tag groups exist)",
    )
    p.add_argument("--excel", type=Path, help="Excel like Jenkins: Вопросы/Ожидаемые ответы/Критерий успеха")
    p.add_argument("--questions", type=Path, help="Text file: one question per line")
    p.add_argument("-q", "--question", action="append", default=[])
    p.add_argument(
        "--out-dir",
        type=Path,
        default=Path("out") / "adk_ask" / datetime.now().strftime("%Y%m%d_%H%M%S"),
    )
    p.add_argument("--first-name", default=os.getenv("ADK_TEST_FIRST_NAME", "Local"))
    p.add_argument("--last-name", default=os.getenv("ADK_TEST_LAST_NAME", ""))
    p.add_argument("--username", default=os.getenv("ADK_TEST_USERNAME", "adk_ask"))
    p.add_argument("--no-warmup", action="store_true")
    p.add_argument(
        "--evaluate",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="LLM evaluation (default: on for --excel, off for plain questions)",
    )
    p.add_argument("--llm-url", default=LLM_API_URL)
    p.add_argument("--llm-key", default=LLM_API_KEY)
    p.add_argument("--llm-model", default=LLM_API_MODEL)
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    out_dir: Path = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(out_dir / "run.log", args.verbose)

    excel_stem = "run"
    if args.excel:
        cases = load_excel_cases(args.excel.expanduser().resolve())
        excel_stem = args.excel.stem
    else:
        cases = load_text_cases(args.questions, args.question)

    if not cases:
        logger.error("No questions. Pass --excel / --questions / -q / stdin.")
        return 2

    do_evaluate = args.evaluate
    if do_evaluate is None:
        do_evaluate = bool(args.excel)

    if do_evaluate and not args.llm_key:
        logger.warning("LLM_API_KEY/--llm-key не задан — автооценка будет пропущена.")
        do_evaluate = False

    profile = build_profile_state_delta(
        first_name=args.first_name,
        last_name=args.last_name,
        username=args.username,
    )
    client = AdkClient(args.base, args.app, args.timeout, profile)

    logger.info(
        "ADK base=%s app=%s cases=%d evaluate=%s out=%s",
        args.base,
        args.app,
        len(cases),
        do_evaluate,
        out_dir,
    )
    if not client.healthcheck():
        logger.error("ADK is not reachable at %s", args.base)
        return 1

    has_dialog_tags = any(_cell_str(c.get("dialog_tag")) != "" for c in cases)
    shared_session = (args.session_id or str(uuid4())).strip()

    answers_path = out_dir / "answers.jsonl"
    timings_path = out_dir / "timings.jsonl"
    evals_path = out_dir / "evaluations.jsonl"
    for path in (answers_path, timings_path, evals_path):
        if path.exists():
            path.unlink()

    ok = 0
    failed = 0
    timing_rows: List[Dict[str, Any]] = []
    result_rows: List[Dict[str, Any]] = []

    with JsonlWriter(answers_path) as answers_w, JsonlWriter(timings_path) as timings_w, JsonlWriter(
        evals_path
    ) as evals_w:
        current_session = ""
        current_dialog_key: Optional[str] = None
        block_session_id = shared_session

        def ensure(session_id: str) -> None:
            nonlocal current_session
            if session_id == current_session:
                return
            client.ensure_session(args.user_id, session_id)
            if not args.no_warmup:
                logger.info("Warmup session %s", session_id)
                try:
                    client.run(args.user_id, session_id, "Привет! Ты готов отвечать на вопросы?")
                except (RuntimeError, requests.RequestException) as e:
                    logger.warning("Warmup failed (continuing): %s", e)
            current_session = session_id

        for idx, case in enumerate(cases, start=1):
            dialog_tag = _cell_str(case.get("dialog_tag"))
            if has_dialog_tags:
                if dialog_tag != current_dialog_key:
                    block_session_id = str(uuid4())
                    current_dialog_key = dialog_tag
                    logger.info(
                        "=== New ADK session for Dialog tag=%r: %s ===",
                        dialog_tag,
                        block_session_id,
                    )
                session_id = block_session_id
            elif args.fresh_session:
                session_id = str(uuid4())
            else:
                session_id = shared_session

            ensure(session_id)

            question = case["question"]
            logger.info("[%d/%d] Q: %s", idx, len(cases), question)
            t0 = time.perf_counter()
            answer = ""
            answer_raw = ""
            error = ""
            timing: Optional[Dict[str, Any]] = None
            try:
                answer, answer_raw, events = client.run(args.user_id, session_id, question)
                timing = extract_timing(events)
                ok += 1
            except (RuntimeError, requests.RequestException, OSError) as e:
                error = f"{type(e).__name__}: {e}"
                failed += 1
                logger.error("[%d/%d] error: %s", idx, len(cases), error)
            wall_s = round(time.perf_counter() - t0, 3)
            ts = _utc_now_iso()

            answers_w.write(
                {
                    "ts": ts,
                    "n": case["n"],
                    "use_case": case.get("use_case"),
                    "dialog_tag": dialog_tag,
                    "session_id": session_id,
                    "user_id": args.user_id,
                    "question": question,
                    "answer": answer,
                    "answer_raw": answer_raw,
                    "error": error,
                    "wall_s": wall_s,
                }
            )
            timing_row = {
                "ts": ts,
                "n": case["n"],
                "session_id": session_id,
                "question": question,
                "wall_s": wall_s,
                "wall_ms": int(wall_s * 1000),
                "error": error,
                "timing": timing or {},
            }
            timings_w.write(timing_row)
            timing_rows.append(timing_row)

            evaluation: Dict[str, Any]
            evaluated = False
            if do_evaluate:
                evaluation = evaluate_answer_llm(
                    question=question,
                    reference_answer=case.get("reference_answer") or "",
                    requirements=case.get("requirements") or "",
                    answer=answer,
                    answer_raw=answer_raw,
                    api_url=args.llm_url,
                    api_key=args.llm_key,
                    model=args.llm_model,
                )
                evaluated = True
                logger.info(
                    "[%d/%d] %s | %s",
                    idx,
                    len(cases),
                    format_evaluation_badge(evaluation),
                    question[:80],
                )
            else:
                evaluation = create_default_evaluation("Автооценка отключена")

            eval_row = {
                "ts": ts,
                "n": case["n"],
                "use_case": case.get("use_case"),
                "dialog_tag": dialog_tag,
                "question": question,
                "reference_answer": case.get("reference_answer"),
                "requirements": case.get("requirements"),
                "answer": answer,
                "answer_raw": answer_raw,
                "wall_s": wall_s,
                "error": error,
                "evaluated": evaluated,
                **evaluation,
            }
            evals_w.write(eval_row)
            result_rows.append(eval_row)

            preview = (answer or error)[:160].replace("\n", " ")
            logger.info("[%d/%d] %.2fs | %s", idx, len(cases), wall_s, preview)

    by_q_path, summary_csv, summary_md = write_agent_summaries(out_dir, timing_rows)
    report_path, eval_md = write_evaluation_report(out_dir, result_rows, excel_stem=excel_stem)

    logger.info(
        "Done. ok=%d failed=%d answers=%s timings=%s agents=%s eval=%s",
        ok,
        failed,
        answers_path,
        timings_path,
        summary_csv,
        report_path,
    )
    logger.info("Per-question timings: %s | eval md: %s | agents md: %s", by_q_path, eval_md, summary_md)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
