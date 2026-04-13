"""
Общий запуск LlmAgent с парсингом JSON в session.state (для оркестраторов и root).
"""
import json
from typing import Any, AsyncGenerator, Callable, Dict

from google.adk.agents import LlmAgent, InvocationContext
from google.adk.events import Event

from .helpers import extract_json, truncate_for_log
from utils.logger import setup_logger

logger = setup_logger("json_leaf_runner", "agent.log")


async def run_json_leaf_agent(
    ctx: InvocationContext,
    agent: LlmAgent,
    output_key: str,
    parsed_state_key: str,
    validator: Callable[[Dict[str, Any]], Dict[str, Any]],
    log_label: str,
) -> AsyncGenerator[Event, None]:
    async for event in agent.run_async(ctx):
        yield event

    raw = str(ctx.session.state.get(output_key) or "").strip()
    logger.debug("%s raw: %s", log_label, truncate_for_log(raw, 500))

    parsed = validator(extract_json(raw))
    ctx.session.state[parsed_state_key] = parsed
    logger.debug("%s parsed: %s", log_label, json.dumps(parsed, ensure_ascii=False))
