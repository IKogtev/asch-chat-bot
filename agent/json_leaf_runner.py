"""
Общий запуск LlmAgent с парсингом JSON в session.state
для оркестраторов и root.
"""
import copy
import json
import time
import ast
from typing import Any, AsyncGenerator, Callable, Dict, Mapping

from google.adk.agents import InvocationContext, LlmAgent
from google.adk.events import Event

from utils.logger import setup_logger
from .doc_search_kb_context import format_kb_hits_summary, parse_kb_search_hits
from .doc_search_validation import DocSearchRetryableValidationError
from .helpers import extract_json, truncate_for_log
from .stage_metrics import (
    event_has_model_output,
    event_is_model_turn,
    extract_usage_tokens,
    record_stage_metrics,
    stage_name_from_log_label,
)

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
    if isinstance(response, list):
        parts = [_response_to_text(item) for item in response]
        return "\n".join(part for part in parts if part)
    if isinstance(response, dict):
        if response.get("type") == "text" and "text" in response:
            return str(response.get("text") or "")
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
    attempt = ctx.session.state.get("doc_search_attempt")
    rerank_only = ctx.session.state.get("doc_search_rerank_only")
    if rerank_only:
        existing = ctx.session.state.get("_doc_search_kb_hits")
        existing_hits = existing if isinstance(existing, list) else []
        logger.info(
            "doc_search kb_hits: attempt=%s rerank_only=True keep_existing %s",
            attempt,
            format_kb_hits_summary(existing_hits),
        )
        return

    if not response_texts:
        logger.info(
            "doc_search kb_hits: attempt=%s no kb_search response to store",
            attempt,
        )
        return

    # kb_search уже склеивает чанки в один документ; при нескольких вызовах тула
    # берём только последний ответ как источник allowed document_id.
    hits = parse_kb_search_hits(response_texts[-1])
    if hits:
        ctx.session.state["_doc_search_kb_hits"] = hits
        logger.info(
            "doc_search kb_hits: attempt=%s stored_from_kb_search %s",
            attempt,
            format_kb_hits_summary(hits),
        )
    else:
        logger.info(
            "doc_search kb_hits: attempt=%s kb_search response parsed empty",
            attempt,
        )


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
    stage_name = stage_name_from_log_label(log_label)
    _t_llm0 = time.monotonic()
    ttft_ms: int | None = None
    input_tokens = 0
    output_tokens = 0
    model_turns = 0
    usage_model_turns = 0
    output_model_turns = 0
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
        if ttft_ms is None and event_has_model_output(event):
            ttft_ms = int((time.monotonic() - _t_llm0) * 1000.0)
        usage = getattr(event, "usage_metadata", None)
        if usage is None and isinstance(event, dict):
            usage = event.get("usage_metadata") or event.get("usageMetadata")
        if usage is not None:
            usage_model_turns += 1
        elif event_is_model_turn(event):
            output_model_turns += 1
        in_tok, out_tok = extract_usage_tokens(usage)
        input_tokens += in_tok
        output_tokens += out_tok
        sanitized_event = strip_thought_parts(event)
        if sanitized_event is not None:
            yield sanitized_event

    model_turns = usage_model_turns if usage_model_turns > 0 else output_model_turns
    llm_ms = int((time.monotonic() - _t_llm0) * 1000.0)
    if stage_name:
        record_stage_metrics(
            ctx.session.state,
            stage_name,
            ms=llm_ms,
            ttft_ms=ttft_ms if ttft_ms is not None else llm_ms,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=len(tool_calls),
            model_turns=model_turns,
        )
        logger.info(
            "stage_metrics: stage=%s ms=%s ttft_ms=%s input_tokens=%s output_tokens=%s "
            "tool_calls=%s model_turns=%s",
            stage_name,
            llm_ms,
            ttft_ms if ttft_ms is not None else llm_ms,
            input_tokens,
            output_tokens,
            len(tool_calls),
            model_turns,
        )

    if _doc_timing:
        _store_doc_search_kb_hits(ctx, kb_search_response_texts)
        logger.info(
            "doc_search_result_json: attempt=%s rerank_only=%s kb_search_calls=%s",
            ctx.session.state.get("doc_search_attempt"),
            ctx.session.state.get("doc_search_rerank_only"),
            tool_calls.count("kb_search"),
        )

    _llm_ms: float | None = None
    if _doc_timing and _t_llm0 is not None:
        _llm_ms = (time.monotonic() - _t_llm0) * 1000.0
        
    # Достаем сырой результат без жесткого каста к str на первом шаге
    raw_payload = ctx.session.state.get(output_key)
    logger.debug(
        "%s tool diagnostics: calls=%s events=%s",
        log_label,
        tool_calls,
        json.dumps(tool_event_summaries, ensure_ascii=False),
    )
    logger.debug("%s raw: %s", log_label, truncate_for_log(raw_payload, 500))

    _t_parse0 = time.monotonic() if _doc_timing else None
    try:
        if isinstance(raw_payload, (dict, Mapping)):
            # Ветка 1: Если новый агент с output_schema уже вернул готовый Python-словарь
            extracted = dict(raw_payload)
        elif hasattr(raw_payload, "model_dump"):
            # Ветка 2: Если вернулся Pydantic объект напрямую
            extracted = raw_payload.model_dump()
        else:
            # Ветка 3: Если вернулась строка (старый формат из промпта)
            raw_str = str(raw_payload or "").strip()
            try:
                # Шаг А: Стандартный строгий JSON (для двойных кавычек)
                extracted = extract_json(raw_str)
            except Exception:
                # Шаг Б: Если упал, пробуем безопасно распарсить одинарные кавычки 
                parsed_literal = ast.literal_eval(raw_str)
                if isinstance(parsed_literal, (dict, Mapping)):
                    extracted = dict(parsed_literal)
                else:
                    raise ValueError("Parsed literal from string is not a dictionary")
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
            truncate_for_log(str(raw_payload), 500),
        )
        raise AgentValidationFailure(
            log_label=log_label,
            validation_error=str(exc),
            raw=str(raw_payload),
            user_message=validation_error_user_message,
        ) from exc

    if _doc_timing and _t_parse0 is not None:
        _parse_ms = (time.monotonic() - _t_parse0) * 1000.0
        logger.debug(
            "doc_search LLM timing: agent.run_async wall_ms=%s; json_extract+validate wall_ms=%.1f",
            llm_ms,
            _parse_ms,
        )

    ctx.session.state[parsed_state_key] = parsed
    logger.debug("%s parsed: %s", log_label, json.dumps(parsed, ensure_ascii=False))
