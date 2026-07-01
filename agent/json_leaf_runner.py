"""
Общий запуск LlmAgent с парсингом JSON в session.state
для оркестраторов и root.
"""
import copy
import json
import time
from typing import Any, AsyncGenerator, Callable, Dict

from google.adk.agents import InvocationContext, LlmAgent
from google.adk.events import Event

from utils.logger import setup_logger
from .doc_search_kb_context import parse_kb_search_hits
from .doc_search_validation import DocSearchRetryableValidationError
from .helpers import extract_json, truncate_for_log

logger = setup_logger("json_leaf_runner", "agent.log")


class AgentValidationFailure(Exception):
    """Non-fatal validation/parsing failure for a leaf agent result."""

    def __init__(
        self,
        *,
        log_label: str,
        validation_error: str,
        raw: str,
        user_message: str,
    ) -> None:
        self.log_label = log_label
        self.validation_error = validation_error
        self.raw = raw
        self.user_message = user_message
        super().__init__(f"{log_label}: {validation_error}")


def _copy_with_updates(obj: Any, updates: Dict[str, Any]) -> Any:
    if hasattr(obj, "model_copy"):
        return obj.model_copy(deep=True, update=updates)

    cloned = copy.copy(obj)
    for key, value in updates.items():
        setattr(cloned, key, value)
    return cloned


def _has_meaningful_actions(event: Event) -> bool:
    actions = getattr(event, "actions", None)
    if not actions:
        return False

    values = getattr(actions, "__dict__", None)
    if isinstance(values, dict):
        return any(value not in (None, False, {}, [], "") for value in values.values())

    if hasattr(actions, "model_dump"):
        dumped = actions.model_dump(exclude_none=True)
        return any(value not in (None, False, {}, [], "") for value in dumped.values())

    return True


def strip_thought_parts(event: Event) -> Event | None:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts:
        return event

    filtered_parts = [
        part for part in parts if getattr(part, "thought", False) is not True
    ]
    if len(filtered_parts) == len(parts):
        return event

    if not filtered_parts and not _has_meaningful_actions(event):
        return None

    return _copy_with_updates(
        event,
        {"content": _copy_with_updates(content, {"parts": filtered_parts})},
    )


def _get_mapping_or_attr(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _extract_function_call_name(part: Any) -> str | None:
    function_call = (
        _get_mapping_or_attr(part, "function_call")
        or _get_mapping_or_attr(part, "functionCall")
    )
    if not function_call:
        return None

    name = _get_mapping_or_attr(function_call, "name")
    if not name:
        return None

    return str(name).strip() or None


def _safe_json_preview(value: Any, max_length: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return truncate_for_log(text, max_length)


def _extract_function_call_summary(part: Any) -> dict[str, str] | None:
    function_call = (
        _get_mapping_or_attr(part, "function_call")
        or _get_mapping_or_attr(part, "functionCall")
    )
    if not function_call:
        return None

    name = _get_mapping_or_attr(function_call, "name")
    if not name:
        return None

    args = (
        _get_mapping_or_attr(function_call, "args")
        or _get_mapping_or_attr(function_call, "arguments")
        or {}
    )
    return {
        "type": "call",
        "name": str(name).strip(),
        "args_preview": _safe_json_preview(args, 500),
    }


def _response_to_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        for key in ("content", "text", "result"):
            if key in response:
                nested = _response_to_text(response.get(key))
                if nested:
                    return nested
        try:
            return json.dumps(response, ensure_ascii=False, default=str)
        except Exception:
            return str(response)
    return str(response)


def _extract_function_response_raw(part: Any) -> tuple[str, Any] | None:
    function_response = (
        _get_mapping_or_attr(part, "function_response")
        or _get_mapping_or_attr(part, "functionResponse")
    )
    if not function_response:
        return None

    name = _get_mapping_or_attr(function_response, "name")
    if not name:
        return None

    response = (
        _get_mapping_or_attr(function_response, "response")
        or _get_mapping_or_attr(function_response, "result")
        or {}
    )
    return str(name).strip(), response


def _extract_kb_search_response_texts_from_event(event: Event) -> list[str]:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts and isinstance(event, dict):
        content = event.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
    if not parts:
        return []

    texts: list[str] = []
    for part in parts:
        raw = _extract_function_response_raw(part)
        if not raw:
            continue
        name, response = raw
        if name != "kb_search":
            continue
        text = _response_to_text(response).strip()
        if text:
            texts.append(text)
    return texts


def _store_doc_search_kb_hits(ctx: InvocationContext, response_texts: list[str]) -> None:
    if ctx.session.state.get("doc_search_rerank_only"):
        return

    if not response_texts:
        return

    # kb_search уже склеивает чанки в один документ; при нескольких вызовах тула
    # берём только последний ответ как источник allowed document_id.
    hits = parse_kb_search_hits(response_texts[-1])
    if hits:
        ctx.session.state["_doc_search_kb_hits"] = hits


def _extract_function_response_summary(part: Any) -> dict[str, str] | None:
    function_response = (
        _get_mapping_or_attr(part, "function_response")
        or _get_mapping_or_attr(part, "functionResponse")
    )
    if not function_response:
        return None

    name = _get_mapping_or_attr(function_response, "name") or ""
    response = (
        _get_mapping_or_attr(function_response, "response")
        or _get_mapping_or_attr(function_response, "result")
        or {}
    )
    return {
        "type": "response",
        "name": str(name).strip(),
        "response_preview": _safe_json_preview(response, 800),
    }


def _extract_tool_event_summaries(event: Event) -> list[dict[str, str]]:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts and isinstance(event, dict):
        content = event.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
    if not parts:
        return []

    summaries = []
    for part in parts:
        call_summary = _extract_function_call_summary(part)
        if call_summary:
            summaries.append(call_summary)
            continue

        response_summary = _extract_function_response_summary(part)
        if response_summary:
            summaries.append(response_summary)
    return summaries


def _extract_function_call_names(event: Event) -> list[str]:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts and isinstance(event, dict):
        content = event.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
    if not parts:
        return []

    names = []
    for part in parts:
        name = _extract_function_call_name(part)
        if name:
            names.append(name)
    return names


async def run_json_leaf_agent(
    ctx: InvocationContext,
    agent: LlmAgent,
    output_key: str,
    parsed_state_key: str,
    validator: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    log_label: str,
    validation_error_user_message: str,
) -> AsyncGenerator[Event, None]:
    _doc_timing = log_label == "doc_search_result_json"
    _t_llm0 = time.monotonic() if _doc_timing else None
    tool_calls: list[str] = []
    tool_event_summaries: list[dict[str, str]] = []
    kb_search_response_texts: list[str] = []
    async for event in agent.run_async(ctx):
        tool_calls.extend(_extract_function_call_names(event))
        tool_event_summaries.extend(_extract_tool_event_summaries(event))
        if _doc_timing:
            kb_search_response_texts.extend(
                _extract_kb_search_response_texts_from_event(event)
            )
        sanitized_event = strip_thought_parts(event)
        if sanitized_event is not None:
            yield sanitized_event

    if _doc_timing:
        _store_doc_search_kb_hits(ctx, kb_search_response_texts)

    _llm_ms: float | None = None
    if _doc_timing and _t_llm0 is not None:
        _llm_ms = (time.monotonic() - _t_llm0) * 1000.0

    raw = str(ctx.session.state.get(output_key) or "").strip()
    logger.debug(
        "%s tool diagnostics: calls=%s events=%s",
        log_label,
        tool_calls,
        json.dumps(tool_event_summaries, ensure_ascii=False),
    )
    logger.debug("%s raw: %s", log_label, truncate_for_log(raw, 500))

    _t_parse0 = time.monotonic() if _doc_timing else None
    try:
        extracted = extract_json(raw)
        logger.debug("%s extracted: %s", log_label, json.dumps(extracted, ensure_ascii=False))
        validator_context = dict(getattr(ctx.session, "state", {}) or {})
        validator_context["_adk_tool_calls"] = tool_calls
        validator_context["_adk_tool_event_summaries"] = tool_event_summaries
        parsed = validator(extracted, validator_context)
    except DocSearchRetryableValidationError:
        raise
    except Exception as exc:
        logger.warning(
            "%s validation/parsing error: %s; user_query=%s; search_query=%s; intent=%s; route=%s; raw=%s",
            log_label,
            exc,
            truncate_for_log(ctx.session.state.get("user_query"), 200),
            truncate_for_log(ctx.session.state.get("search_query"), 200),
            truncate_for_log(ctx.session.state.get("intent"), 100),
            truncate_for_log(ctx.session.state.get("route"), 100),
            truncate_for_log(raw, 500),
        )
        raise AgentValidationFailure(
            log_label=log_label,
            validation_error=str(exc),
            raw=raw,
            user_message=validation_error_user_message,
        ) from exc

    if _doc_timing and _t_parse0 is not None and _llm_ms is not None:
        _parse_ms = (time.monotonic() - _t_parse0) * 1000.0
        logger.debug(
            "doc_search LLM timing: agent.run_async wall_ms=%.1f; json_extract+validate wall_ms=%.1f",
            _llm_ms,
            _parse_ms,
        )

    ctx.session.state[parsed_state_key] = parsed
    logger.debug("%s parsed: %s", log_label, json.dumps(parsed, ensure_ascii=False))
