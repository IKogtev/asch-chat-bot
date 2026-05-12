"""
Общий запуск LlmAgent с парсингом JSON в session.state
для оркестраторов и root.
"""
import json
import copy
import logging
from typing import Any, AsyncGenerator, Callable, Dict

from google.adk.agents import InvocationContext, LlmAgent
from google.adk.events import Event

from utils.logger import setup_logger
from .helpers import extract_json, truncate_for_log

logger = setup_logger("json_leaf_runner", "agent.log")

MCP_SESSION_ERROR_MARKERS = (
    "Connection closed",
    "Failed to get tools from MCP server",
    "Attempted to exit cancel scope in a different task",
    "Session termination failed",
    "McpError",
)


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


def _extract_thought_texts(event: Event) -> list[str]:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None) or []
    texts: list[str] = []
    for part in parts:
        if getattr(part, "thought", False) is True:
            text = getattr(part, "text", None)
            if text:
                texts.append(str(text))
    return texts


def is_mcp_session_error(exc: BaseException) -> bool:
    message = f"{type(exc).__name__}: {exc!r}"
    return any(marker in message for marker in MCP_SESSION_ERROR_MARKERS)


async def _run_agent_and_collect_events(ctx: InvocationContext, agent: LlmAgent) -> list[Event]:
    events: list[Event] = []
    async for event in agent.run_async(ctx):
        thought_texts = _extract_thought_texts(event)
        is_debug_enabled = getattr(logger, "isEnabledFor", lambda level: False)
        if thought_texts and is_debug_enabled(logging.DEBUG):
            logger.debug(
                "%s thought parts stripped: %s",
                getattr(event, "author", "unknown"),
                truncate_for_log("\n\n".join(thought_texts), 2000),
            )
        sanitized_event = strip_thought_parts(event)
        if sanitized_event is not None:
            events.append(sanitized_event)
    return events


async def run_json_leaf_agent(
    ctx: InvocationContext,
    agent: LlmAgent,
    output_key: str,
    parsed_state_key: str,
    validator: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    log_label: str,
    validation_error_user_message: str,
    retry_agent_factory: Callable[[LlmAgent], LlmAgent] | None = None,
) -> AsyncGenerator[Event, None]:
    current_agent = agent
    attempt = 1
    retried_after_mcp_session_error = False
    while True:
        try:
            events = await _run_agent_and_collect_events(ctx, current_agent)
            break
        except Exception as exc:
            if (
                attempt >= 2
                and retry_agent_factory is not None
                and is_mcp_session_error(exc)
            ):
                logger.error(
                    "%s MCP session retry failed after recreating leaf agent: %s",
                    log_label,
                    exc,
                    exc_info=True,
                )

            if attempt >= 2 or retry_agent_factory is None or not is_mcp_session_error(exc):
                raise

            logger.warning(
                "%s MCP session error, recreating leaf agent and retrying once: %s",
                log_label,
                exc,
                exc_info=True,
            )
            ctx.session.state.pop(output_key, None)
            ctx.session.state.pop(parsed_state_key, None)
            current_agent = retry_agent_factory(current_agent)
            attempt += 1
            retried_after_mcp_session_error = True

    if retried_after_mcp_session_error:
        logger.info("%s MCP session retry succeeded", log_label)

    for event in events:
        yield event

    raw = str(ctx.session.state.get(output_key) or "").strip()
    logger.debug("%s raw: %s", log_label, truncate_for_log(raw, 500))

    try:
        extracted = extract_json(raw)
        logger.debug("%s extracted: %s", log_label, json.dumps(extracted, ensure_ascii=False))
        validator_context = dict(getattr(ctx.session, "state", {}) or {})
        parsed = validator(extracted, validator_context)
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

    ctx.session.state[parsed_state_key] = parsed
    logger.debug("%s parsed: %s", log_label, json.dumps(parsed, ensure_ascii=False))
