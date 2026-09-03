"""Scoped, sanitized DEBUG tracing for LLM and MCP diagnostics."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import time
from contextvars import ContextVar
from datetime import datetime
from typing import Any, Callable, Mapping, Sequence

from utils.logger import setup_logger


logger = setup_logger("llm_debug_trace", "agent.log")

_TRACE_CONTEXT: ContextVar[dict[str, str] | None] = ContextVar(
    "llm_debug_trace_context",
    default=None,
)
_TOOL_START: ContextVar[float | None] = ContextVar(
    "llm_debug_trace_tool_start",
    default=None,
)

_SECRET_KEY_MARKERS = (
    "authorization",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "cookie",
)
_SQL_TABLE_RE = re.compile(
    r"\b(?:from|join|update|into)\s+([A-Za-z_][A-Za-z0-9_.]*)",
    flags=re.IGNORECASE,
)
_TOOL_CALL_RE = re.compile(r"<tool_call>(.*?)</tool_call>", flags=re.DOTALL)


def _env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _max_length() -> int:
    try:
        return max(100, int(os.getenv("LLM_TRACE_MAX_LENGTH", "1000")))
    except (TypeError, ValueError):
        return 1000


def _configured_agents() -> set[str]:
    raw = os.getenv("LLM_TRACE_AGENTS", "advisor_content_agent")
    return {item.strip() for item in raw.split(",") if item.strip()}


def debug_trace_enabled(agent_name: str | None = None) -> bool:
    """Return whether scoped tracing is enabled for the supplied agent."""
    if not _env_bool("LLM_TRACE_ENABLED"):
        return False
    if agent_name is None:
        return True
    configured = _configured_agents()
    return "*" in configured or agent_name in configured


def _debug_logging_enabled() -> bool:
    checker = getattr(logger, "isEnabledFor", None)
    return bool(checker(logging.DEBUG)) if callable(checker) else True


def _sha256(value: Any) -> str:
    if isinstance(value, bytes):
        data = value
    else:
        data = str(value or "").encode("utf-8", errors="replace")
    return hashlib.sha256(data).hexdigest()[:16]


def _text_metadata(value: Any, *, include_preview: bool | None = None) -> dict[str, Any]:
    text = "" if value is None else str(value)
    result: dict[str, Any] = {
        "length": len(text),
        "sha256": _sha256(text),
        "empty": not bool(text.strip()),
    }
    if include_preview is None:
        include_preview = _env_bool("LLM_TRACE_INCLUDE_CONTENT")
    if include_preview:
        result["preview"] = text[: _max_length()]
    return result


def _is_secret_key(key: Any) -> bool:
    normalized = str(key or "").lower().replace("-", "_")
    return any(marker in normalized for marker in _SECRET_KEY_MARKERS)


def sanitize_value(value: Any, *, depth: int = 0) -> Any:
    """Convert a value to a bounded structure while removing secrets and bulk content."""
    if depth >= 5:
        return {"type": type(value).__name__, "truncated": True}
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _text_metadata(value)
    if isinstance(value, Mapping):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text):
                result[key_text] = "[REDACTED]"
            else:
                result[key_text] = sanitize_value(item, depth=depth + 1)
        return result
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        limit = 30
        items = [sanitize_value(item, depth=depth + 1) for item in list(value)[:limit]]
        if len(value) > limit:
            items.append({"truncated_items": len(value) - limit})
        return items
    if hasattr(value, "model_dump"):
        try:
            return sanitize_value(value.model_dump(exclude_none=True), depth=depth)
        except Exception:
            pass
    if hasattr(value, "__dict__"):
        try:
            public = {
                key: item
                for key, item in vars(value).items()
                if not key.startswith("_")
            }
            if public:
                return sanitize_value(public, depth=depth)
        except Exception:
            pass
    return {"type": type(value).__name__, "repr": _text_metadata(repr(value))}


def trace_debug(event: str, payload: Mapping[str, Any], *, agent_name: str | None = None) -> None:
    """Write one structured DEBUG record when scoped tracing is active."""
    if not debug_trace_enabled(agent_name) or not _debug_logging_enabled():
        return
    try:
        context = _TRACE_CONTEXT.get() or {}
        record = {
            "event": event,
            **context,
            **dict(payload),
        }
        logger.debug("llm_trace %s", json.dumps(record, ensure_ascii=False, default=str))
    except Exception:
        logger.debug("llm_trace serialization failed: event=%s", event, exc_info=True)


def trace_debug_safely(
    event: str,
    payload_factory: Callable[[], Mapping[str, Any]],
    *,
    agent_name: str | None = None,
) -> None:
    """Build and write a trace record without affecting application control flow."""
    if not debug_trace_enabled(agent_name) or not _debug_logging_enabled():
        return
    try:
        trace_debug(event, payload_factory(), agent_name=agent_name)
    except Exception:
        logger.debug("llm_trace collection failed: event=%s", event, exc_info=True)


def _context_metadata(callback_context: Any) -> dict[str, str]:
    return {
        "agent": str(getattr(callback_context, "agent_name", "") or ""),
        "invocation_id": str(getattr(callback_context, "invocation_id", "") or ""),
    }


def _content_parts(content: Any) -> list[Any]:
    if isinstance(content, Mapping):
        parts = content.get("parts")
    else:
        parts = getattr(content, "parts", None)
    return list(parts or [])


def _mapping_or_attr(value: Any, key: str, default: Any = None) -> Any:
    if isinstance(value, Mapping):
        return value.get(key, default)
    return getattr(value, key, default)


def summarize_message(content: Any) -> dict[str, Any]:
    role = _mapping_or_attr(content, "role", "")
    parts = []
    for part in _content_parts(content):
        text = _mapping_or_attr(part, "text")
        function_call = _mapping_or_attr(part, "function_call") or _mapping_or_attr(
            part, "functionCall"
        )
        function_response = _mapping_or_attr(part, "function_response") or _mapping_or_attr(
            part, "functionResponse"
        )
        if text is not None:
            parts.append(
                {
                    "type": "thought" if _mapping_or_attr(part, "thought", False) else "text",
                    "text": _text_metadata(text),
                }
            )
        elif function_call:
            parts.append(
                {
                    "type": "function_call",
                    "name": str(_mapping_or_attr(function_call, "name", "") or ""),
                    "args": summarize_tool_args(
                        _mapping_or_attr(function_call, "args")
                        or _mapping_or_attr(function_call, "arguments")
                        or {}
                    ),
                }
            )
        elif function_response:
            parts.append(
                {
                    "type": "function_response",
                    "name": str(_mapping_or_attr(function_response, "name", "") or ""),
                    "response": summarize_tool_response(
                        _mapping_or_attr(function_response, "response")
                        or _mapping_or_attr(function_response, "result")
                        or {}
                    ),
                }
            )
        else:
            parts.append({"type": type(part).__name__})
    return {"role": str(role or ""), "parts": parts}


def _tool_declarations(value: Any) -> list[dict[str, Any]]:
    dumped = value
    if hasattr(value, "model_dump"):
        try:
            dumped = value.model_dump(exclude_none=True)
        except Exception:
            dumped = value
    if not isinstance(dumped, Mapping):
        return []
    declarations = dumped.get("function_declarations") or dumped.get("functionDeclarations") or []
    result = []
    for declaration in declarations:
        if hasattr(declaration, "model_dump"):
            declaration = declaration.model_dump(exclude_none=True)
        name = _mapping_or_attr(declaration, "name", "")
        parameters = _mapping_or_attr(declaration, "parameters") or _mapping_or_attr(
            declaration, "parameters_json_schema"
        )
        result.append(
            {
                "name": str(name or ""),
                "schema_sha256": _sha256(
                    json.dumps(parameters, sort_keys=True, ensure_ascii=False, default=str)
                ),
            }
        )
    return result


def summarize_llm_request(llm_request: Any) -> dict[str, Any]:
    contents = _mapping_or_attr(llm_request, "contents", []) or []
    config = _mapping_or_attr(llm_request, "config")
    tools = _mapping_or_attr(config, "tools", []) or []
    config_dump = sanitize_value(config) if config is not None else None
    declarations = [item for tool in tools for item in _tool_declarations(tool)]
    return {
        "model": str(_mapping_or_attr(llm_request, "model", "") or ""),
        "messages": [summarize_message(content) for content in contents],
        "message_roles": [str(_mapping_or_attr(content, "role", "") or "") for content in contents],
        "has_nonempty_user_message": any(
            str(_mapping_or_attr(content, "role", "") or "") == "user"
            and any(
                not _text_metadata(_mapping_or_attr(part, "text", ""))["empty"]
                for part in _content_parts(content)
                if _mapping_or_attr(part, "text") is not None
            )
            for content in contents
        ),
        "tools": declarations,
        "tool_count": len(declarations),
        "config": config_dump,
    }


def summarize_provider_request(model: str, messages: Any, kwargs: Mapping[str, Any]) -> dict[str, Any]:
    additional = kwargs.get("additional_args") or {}
    transformed = additional.get("complete_input_dict") if isinstance(additional, Mapping) else None
    request = transformed if isinstance(transformed, Mapping) else kwargs
    request_messages = request.get("messages", messages) if isinstance(request, Mapping) else messages
    tools = request.get("tools", []) if isinstance(request, Mapping) else []
    return {
        "model": model,
        "messages": [
            {
                "role": str(_mapping_or_attr(message, "role", "") or ""),
                "content": _text_metadata(_mapping_or_attr(message, "content", "")),
            }
            for message in list(request_messages or [])
        ],
        "tools": [summarize_tool_definition(tool) for tool in list(tools or [])],
        "tool_choice": request.get("tool_choice") if isinstance(request, Mapping) else None,
        "stream": request.get("stream") if isinstance(request, Mapping) else None,
        "temperature": request.get("temperature") if isinstance(request, Mapping) else None,
        "max_tokens": (
            request.get("max_tokens") or request.get("max_output_tokens")
            if isinstance(request, Mapping)
            else None
        ),
        "extra_body": sanitize_value(request.get("extra_body")) if isinstance(request, Mapping) else None,
    }


def summarize_tool_definition(tool: Any) -> dict[str, Any]:
    dumped = tool.model_dump(exclude_none=True) if hasattr(tool, "model_dump") else tool
    function = _mapping_or_attr(dumped, "function", {}) or {}
    name = _mapping_or_attr(function, "name", "") or _mapping_or_attr(dumped, "name", "")
    parameters = _mapping_or_attr(function, "parameters", {}) or _mapping_or_attr(
        dumped, "parameters", {}
    )
    return {
        "name": str(name or ""),
        "schema_sha256": _sha256(
            json.dumps(parameters, sort_keys=True, ensure_ascii=False, default=str)
        ),
    }


def summarize_provider_response(response: Any) -> dict[str, Any]:
    response_type = type(response).__name__
    if hasattr(response, "model_dump"):
        try:
            response = response.model_dump(exclude_none=True)
        except Exception:
            pass
    choices = _mapping_or_attr(response, "choices", []) or []
    summarized_choices = []
    for choice in list(choices):
        message = _mapping_or_attr(choice, "message") or _mapping_or_attr(choice, "delta") or {}
        content = _mapping_or_attr(message, "content")
        reasoning = _mapping_or_attr(message, "reasoning_content")
        tool_calls = _mapping_or_attr(message, "tool_calls", []) or []
        summarized_choices.append(
            {
                "finish_reason": _mapping_or_attr(choice, "finish_reason"),
                "content": _text_metadata(content),
                "reasoning_content": _text_metadata(reasoning),
                "tool_calls": [
                    {
                        "id": _text_metadata(_mapping_or_attr(call, "id", "")),
                        "name": str(
                            _mapping_or_attr(_mapping_or_attr(call, "function", {}), "name", "")
                            or ""
                        ),
                        "arguments": summarize_tool_args(
                            _mapping_or_attr(
                                _mapping_or_attr(call, "function", {}),
                                "arguments",
                                "",
                            )
                        ),
                    }
                    for call in list(tool_calls)
                ],
            }
        )
    return {
        "response_type": response_type,
        "id": _text_metadata(_mapping_or_attr(response, "id", "")),
        "model": str(_mapping_or_attr(response, "model", "") or ""),
        "choices": summarized_choices,
    }


def summarize_tool_args(args: Any) -> dict[str, Any]:
    if isinstance(args, str):
        try:
            parsed = json.loads(args)
        except (TypeError, ValueError, json.JSONDecodeError):
            parsed = None
        if isinstance(parsed, Mapping):
            args = parsed
        else:
            return {"type": "string", **_text_metadata(args)}
    if not isinstance(args, Mapping):
        return {"type": type(args).__name__, "value": sanitize_value(args)}
    result: dict[str, Any] = {"keys": sorted(str(key) for key in args)}
    serialized = json.dumps(args, ensure_ascii=False, sort_keys=True, default=str)
    result.update({"length": len(serialized), "sha256": _sha256(serialized)})
    sql = args.get("sql") or args.get("query")
    if isinstance(sql, str):
        result["sql"] = {
            **_text_metadata(sql),
            "tables": sorted(set(_SQL_TABLE_RE.findall(sql))),
        }
    return result


def summarize_tool_response(response: Any) -> dict[str, Any]:
    serialized = json.dumps(response, ensure_ascii=False, default=str)
    result: dict[str, Any] = {
        "type": type(response).__name__,
        "length": len(serialized),
        "sha256": _sha256(serialized),
    }
    if isinstance(response, Mapping):
        result["keys"] = sorted(str(key) for key in response)
        for key in ("row_count", "count", "total"):
            value = response.get(key)
            if isinstance(value, int):
                result[key] = value
        for key in ("rows", "data", "items", "results"):
            value = response.get(key)
            if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
                result[f"{key}_count"] = len(value)
    elif isinstance(response, Sequence) and not isinstance(response, (str, bytes, bytearray)):
        result["item_count"] = len(response)
    return result


def summarize_adk_event(event: Any) -> dict[str, Any]:
    content = _mapping_or_attr(event, "content")
    return {
        "author": str(_mapping_or_attr(event, "author", "") or ""),
        "partial": bool(_mapping_or_attr(event, "partial", False)),
        "finish_reason": _mapping_or_attr(event, "finish_reason")
        or _mapping_or_attr(event, "finishReason"),
        "content": summarize_message(content) if content is not None else None,
        "usage": sanitize_value(
            _mapping_or_attr(event, "usage_metadata")
            or _mapping_or_attr(event, "usageMetadata")
        ),
    }


def classify_tool_call_text(value: Any) -> dict[str, Any]:
    text = "" if value is None else str(value)
    opening_count = text.count("<tool_call>")
    closing_count = text.count("</tool_call>")
    matches = _TOOL_CALL_RE.findall(text)
    empty_count = 0
    valid_json_count = 0
    invalid_json_count = 0
    names = []
    for inner in matches:
        stripped = inner.strip()
        if not stripped:
            empty_count += 1
            continue
        try:
            parsed = json.loads(stripped)
        except json.JSONDecodeError:
            invalid_json_count += 1
            continue
        valid_json_count += 1
        if isinstance(parsed, Mapping) and parsed.get("name"):
            names.append(str(parsed["name"]))
    if not opening_count and not closing_count:
        classification = "none"
    elif empty_count:
        classification = "empty_tool_tag"
    elif invalid_json_count or opening_count != closing_count:
        classification = "invalid_tool_json"
    else:
        classification = "raw_tool_call_not_converted"
    return {
        "classification": classification,
        "opening_tags": opening_count,
        "closing_tags": closing_count,
        "matched_tags": len(matches),
        "empty_tags": empty_count,
        "valid_json_tags": valid_json_count,
        "invalid_json_tags": invalid_json_count,
        "tool_names": names,
        "text": _text_metadata(text),
    }


def before_model_debug_trace(*, callback_context: Any, llm_request: Any) -> None:
    metadata = _context_metadata(callback_context)
    if not debug_trace_enabled(metadata["agent"]):
        return None
    _TRACE_CONTEXT.set(metadata)
    trace_debug_safely(
        "adk_model_request",
        lambda: summarize_llm_request(llm_request),
        agent_name=metadata["agent"],
    )
    return None


def after_model_debug_trace(*, callback_context: Any, llm_response: Any) -> None:
    metadata = _context_metadata(callback_context)
    trace_debug_safely(
        "adk_model_response",
        lambda: {"response": sanitize_value(llm_response)},
        agent_name=metadata["agent"],
    )
    _TRACE_CONTEXT.set(None)
    return None


def on_model_error_debug_trace(*, callback_context: Any, llm_request: Any, error: Exception) -> None:
    metadata = _context_metadata(callback_context)
    trace_debug_safely(
        "adk_model_error",
        lambda: {
            "request": summarize_llm_request(llm_request),
            "error_type": type(error).__name__,
            "error": _text_metadata(str(error)),
        },
        agent_name=metadata["agent"],
    )
    _TRACE_CONTEXT.set(None)
    return None


def _tool_name(tool: Any) -> str:
    return str(getattr(tool, "name", "") or type(tool).__name__)


def _tool_callback_metadata(tool_context: Any) -> dict[str, str]:
    return {
        "agent": str(getattr(tool_context, "agent_name", "") or ""),
        "invocation_id": str(getattr(tool_context, "invocation_id", "") or ""),
    }


def before_tool_debug_trace(*, tool: Any, args: dict[str, Any], tool_context: Any) -> None:
    metadata = _tool_callback_metadata(tool_context)
    _TOOL_START.set(time.monotonic())
    _TRACE_CONTEXT.set(metadata)
    trace_debug_safely(
        "adk_tool_request",
        lambda: {"tool": _tool_name(tool), "args": summarize_tool_args(args)},
        agent_name=metadata["agent"],
    )
    return None


def after_tool_debug_trace(
    *,
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    tool_response: Any,
) -> None:
    metadata = _tool_callback_metadata(tool_context)
    started = _TOOL_START.get()
    trace_debug_safely(
        "adk_tool_response",
        lambda: {
            "tool": _tool_name(tool),
            "args": summarize_tool_args(args),
            "duration_ms": None if started is None else int((time.monotonic() - started) * 1000),
            "response": summarize_tool_response(tool_response),
        },
        agent_name=metadata["agent"],
    )
    _TOOL_START.set(None)
    _TRACE_CONTEXT.set(None)
    return None


def on_tool_error_debug_trace(
    *,
    tool: Any,
    args: dict[str, Any],
    tool_context: Any,
    error: Exception,
) -> None:
    metadata = _tool_callback_metadata(tool_context)
    started = _TOOL_START.get()
    trace_debug_safely(
        "adk_tool_error",
        lambda: {
            "tool": _tool_name(tool),
            "args": summarize_tool_args(args),
            "duration_ms": None if started is None else int((time.monotonic() - started) * 1000),
            "error_type": type(error).__name__,
            "error": _text_metadata(str(error)),
        },
        agent_name=metadata["agent"],
    )
    _TOOL_START.set(None)
    _TRACE_CONTEXT.set(None)
    return None


class LiteLlmDebugTraceCallback:
    """LiteLLM callback object that records sanitized provider-boundary events."""

    _asch_debug_trace_callback = True

    def log_pre_api_call(self, model: str, messages: Any, kwargs: Mapping[str, Any]) -> None:
        context = _TRACE_CONTEXT.get()
        if not context or not debug_trace_enabled(context.get("agent")):
            return
        trace_debug_safely(
            "litellm_pre_api_call",
            lambda: summarize_provider_request(model, messages, kwargs),
            agent_name=context.get("agent"),
        )

    async def async_log_success_event(
        self,
        kwargs: Mapping[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        context = _TRACE_CONTEXT.get()
        if not context or not debug_trace_enabled(context.get("agent")):
            return
        trace_debug_safely(
            "litellm_success",
            lambda: {
                "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                "response": summarize_provider_response(response_obj),
            },
            agent_name=context.get("agent"),
        )

    async def async_log_failure_event(
        self,
        kwargs: Mapping[str, Any],
        response_obj: Any,
        start_time: datetime,
        end_time: datetime,
    ) -> None:
        context = _TRACE_CONTEXT.get()
        if not context or not debug_trace_enabled(context.get("agent")):
            return
        trace_debug_safely(
            "litellm_failure",
            lambda: {
                "duration_ms": int((end_time - start_time).total_seconds() * 1000),
                "response": summarize_provider_response(response_obj),
                "error": _text_metadata(str(kwargs.get("exception") or response_obj or "")),
            },
            agent_name=context.get("agent"),
        )


def register_litellm_debug_callback() -> None:
    """Register the passive LiteLLM callback once when tracing is enabled."""
    if not debug_trace_enabled():
        return
    try:
        import litellm
        from litellm.integrations.custom_logger import CustomLogger

        callbacks = list(getattr(litellm, "callbacks", None) or [])
        if any(getattr(item, "_asch_debug_trace_callback", False) for item in callbacks):
            return

        class RegisteredLiteLlmDebugTraceCallback(LiteLlmDebugTraceCallback, CustomLogger):
            pass

        callbacks.append(RegisteredLiteLlmDebugTraceCallback())
        litellm.callbacks = callbacks
        trace_debug("litellm_callback_registered", {"callback_count": len(callbacks)})
    except Exception:
        logger.debug("llm_trace LiteLLM callback registration failed", exc_info=True)


def log_advisor_input_state(ctx: Any) -> None:
    """Record the advisor input state shape without logging profile values."""
    agent_name = "advisor_content_agent"
    if not debug_trace_enabled(agent_name):
        return
    trace_debug_safely(
        "advisor_input_state",
        lambda: _summarize_advisor_input_state(ctx),
        agent_name=agent_name,
    )


def _summarize_advisor_input_state(ctx: Any) -> dict[str, Any]:
    state = getattr(getattr(ctx, "session", None), "state", {}) or {}
    profile = state.get("advisor_client_profile") or {}
    advisor_context = state.get("advisor_dialog_context_json") or ""
    if hasattr(profile, "model_dump"):
        profile = profile.model_dump(exclude_none=True)
    profile_fields = sorted(str(key) for key in profile) if isinstance(profile, Mapping) else []
    return {
        "invocation_id": str(getattr(ctx, "invocation_id", "") or ""),
        "state_keys": sorted(str(key) for key in state),
        "query": _text_metadata(state.get("advisor_search_query", "")),
        "profile_empty": not bool(profile),
        "profile_fields": profile_fields,
        "advisor_context": _text_metadata(advisor_context),
        "has_source_turn": "advisor_source_turn" in state,
        "has_updated_at": "advisor_updated_at" in state,
    }
