"""Сбор и сериализация таймингов/токенов по стадиям агентной цепочки."""
from __future__ import annotations

from typing import Any, Dict, Mapping, MutableMapping

STAGE_METRICS_STATE_KEY = "_stage_metrics"
TIMING_STATE_DELTA_KEY = "_timing"

LOG_LABEL_TO_STAGE: Dict[str, str] = {
    "owasp_result_json": "owasp",
    "dispatcher_result_json": "dispatcher",
    "doc_search_result_json": "doc_search",
    "kb_answer_result_json": "kb_answer",
    "smalltalk_result_json": "smalltalk",
    "product_selection_result_json": "product_selection",
}


def stage_name_from_log_label(log_label: str) -> str | None:
    return LOG_LABEL_TO_STAGE.get(str(log_label or "").strip())


def _as_non_negative_int(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return 0
    return number if number > 0 else 0


def extract_usage_tokens(usage: Any) -> tuple[int, int]:
    """Возвращает (input_tokens, output_tokens) из ADK usage_metadata."""
    if usage is None:
        return 0, 0

    if isinstance(usage, Mapping):
        prompt = usage.get("prompt_token_count")
        if prompt is None:
            prompt = usage.get("promptTokenCount")
        candidates = usage.get("candidates_token_count")
        if candidates is None:
            candidates = usage.get("candidatesTokenCount")
        return _as_non_negative_int(prompt), _as_non_negative_int(candidates)

    prompt = getattr(usage, "prompt_token_count", None)
    candidates = getattr(usage, "candidates_token_count", None)
    return _as_non_negative_int(prompt), _as_non_negative_int(candidates)


def _part_has_model_output(part: Any) -> bool:
    if part is None:
        return False
    if getattr(part, "thought", False) is True:
        return False
    if isinstance(part, Mapping) and part.get("thought") is True:
        return False

    text = getattr(part, "text", None)
    if text is None and isinstance(part, Mapping):
        text = part.get("text")
    if isinstance(text, str) and text.strip():
        return True

    for key in ("function_call", "functionCall"):
        call = getattr(part, key, None) if not isinstance(part, Mapping) else part.get(key)
        if call:
            return True
    return False


def event_has_model_output(event: Any) -> bool:
    """True, если событие содержит первый полезный output модели (текст/tool call)."""
    if event is None:
        return False

    content = getattr(event, "content", None)
    if content is None and isinstance(event, Mapping):
        content = event.get("content")
    parts = getattr(content, "parts", None) if content is not None else None
    if parts is None and isinstance(content, Mapping):
        parts = content.get("parts")
    if not parts:
        return False

    return any(_part_has_model_output(part) for part in parts)


def _event_usage_metadata(event: Any) -> Any:
    usage = getattr(event, "usage_metadata", None)
    if usage is None and isinstance(event, Mapping):
        usage = event.get("usage_metadata") or event.get("usageMetadata")
    return usage


def _event_is_partial(event: Any) -> bool:
    partial = getattr(event, "partial", None)
    if partial is None and isinstance(event, Mapping):
        partial = event.get("partial")
    return partial is True


def event_is_model_turn(event: Any) -> bool:
    """
    Один model turn ≈ один ответ LLM (не streaming-chunk, не function_response).

    Предпочитаем usage_metadata (одна запись на completion);
    fallback — non-partial event с текстом/function_call.
    """
    if event is None:
        return False
    if _event_usage_metadata(event) is not None:
        return True
    if _event_is_partial(event):
        return False
    return event_has_model_output(event)


def record_stage_metrics(
    state: MutableMapping[str, Any],
    stage: str,
    *,
    ms: int,
    ttft_ms: int | None,
    input_tokens: int,
    output_tokens: int,
    tool_calls: int = 0,
    model_turns: int = 0,
) -> None:
    """Пишет/аккумулирует метрики стадии в session.state (для doc_search retries)."""
    stage_name = str(stage or "").strip()
    if not stage_name:
        return

    bucket = state.get(STAGE_METRICS_STATE_KEY)
    if not isinstance(bucket, dict):
        bucket = {}
        state[STAGE_METRICS_STATE_KEY] = bucket

    existing = bucket.get(stage_name)
    if not isinstance(existing, dict):
        bucket[stage_name] = {
            "ms": max(0, int(ms)),
            "ttft_ms": None if ttft_ms is None else max(0, int(ttft_ms)),
            "input_tokens": max(0, int(input_tokens)),
            "output_tokens": max(0, int(output_tokens)),
            "tool_calls": max(0, int(tool_calls)),
            "model_turns": max(0, int(model_turns)),
        }
        return

    existing["ms"] = max(0, int(existing.get("ms") or 0)) + max(0, int(ms))
    existing["input_tokens"] = max(0, int(existing.get("input_tokens") or 0)) + max(
        0, int(input_tokens)
    )
    existing["output_tokens"] = max(0, int(existing.get("output_tokens") or 0)) + max(
        0, int(output_tokens)
    )
    existing["tool_calls"] = max(0, int(existing.get("tool_calls") or 0)) + max(
        0, int(tool_calls)
    )
    existing["model_turns"] = max(0, int(existing.get("model_turns") or 0)) + max(
        0, int(model_turns)
    )
    if existing.get("ttft_ms") is None and ttft_ms is not None:
        existing["ttft_ms"] = max(0, int(ttft_ms))


def flatten_stage_metrics(
    metrics: Mapping[str, Any] | None,
    *,
    route: str | None = None,
    intent: str | None = None,
) -> Dict[str, Any]:
    """Плоский dict: owasp_ms, owasp_tool_calls, owasp_model_turns, ... + route/intent."""
    flat: Dict[str, Any] = {}
    if isinstance(metrics, Mapping):
        for stage, values in metrics.items():
            if not isinstance(values, Mapping):
                continue
            stage_key = str(stage).strip()
            if not stage_key:
                continue
            flat[f"{stage_key}_ms"] = max(0, int(values.get("ms") or 0))
            ttft = values.get("ttft_ms")
            flat[f"{stage_key}_ttft_ms"] = None if ttft is None else max(0, int(ttft))
            flat[f"{stage_key}_input_tokens"] = max(0, int(values.get("input_tokens") or 0))
            flat[f"{stage_key}_output_tokens"] = max(0, int(values.get("output_tokens") or 0))
            flat[f"{stage_key}_tool_calls"] = max(0, int(values.get("tool_calls") or 0))
            flat[f"{stage_key}_model_turns"] = max(0, int(values.get("model_turns") or 0))

    if route not in (None, ""):
        flat["route"] = str(route)
    if intent not in (None, ""):
        flat["intent"] = str(intent)
    return flat


def build_timing_payload(session_state: Mapping[str, Any] | None) -> Dict[str, Any]:
    """Собирает плоский `_timing` payload из session.state текущего хода."""
    state = session_state or {}
    metrics = state.get(STAGE_METRICS_STATE_KEY)
    route = None
    intent = None
    dispatch = state.get("_dispatcher_result_parsed")
    if isinstance(dispatch, Mapping):
        route = dispatch.get("route")
        intent = dispatch.get("intent")
    if not route:
        route = state.get("last_route") or state.get("route")
    if not intent:
        intent = state.get("last_intent") or state.get("intent")
    return flatten_stage_metrics(metrics if isinstance(metrics, Mapping) else None, route=route, intent=intent)
