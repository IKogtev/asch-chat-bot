"""
Общий запуск LlmAgent с парсингом JSON в session.state
для оркестраторов и root.
"""
import json
import copy
from typing import Any, AsyncGenerator, Callable, Dict

from google.adk.agents import InvocationContext, LlmAgent
from google.adk.events import Event

from utils.logger import setup_logger
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


async def run_json_leaf_agent(
    ctx: InvocationContext,
    agent: LlmAgent,
    output_key: str,
    parsed_state_key: str,
    validator: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    log_label: str,
    validation_error_user_message: str,
) -> AsyncGenerator[Event, None]:
    async for event in agent.run_async(ctx):
        sanitized_event = strip_thought_parts(event)
        if sanitized_event is not None:
            yield sanitized_event

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
