from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from utils.logger import setup_logger
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from .validation_utils import build_validation_error

logger = setup_logger("voice_agent", "agent.log")


def validate_voice_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Validate voice_agent JSON output."""
    agent_name = "voice_agent"
    _ = context

    if not isinstance(data, dict):
        raise build_validation_error(
            agent=agent_name,
            stage="payload_type",
            problem=f"expected dict, got {type(data).__name__}",
        )

    status = str(data.get("status", "")).strip()
    message = str(data.get("message", "")).strip()
    if status != "ok":
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid status {status!r}, expected 'ok'",
            data=data,
        )
    if not message:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem="message is required",
            data=data,
        )
    return {"status": status, "message": message}


def create_voice_agent(model: LiteLlm) -> LlmAgent:
    """Corporate tone rewriter; no tools."""
    fallback = """
Ты voice_agent. Перефразируй {voice_draft} в деловом тоне на «Вы».
Не добавляй факты, коды, числа, условия. Верни JSON: {{"status":"ok","message":"..."}}
"""
    prompt_file = "voice_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    agent = LlmAgent(
        name="voice_agent",
        model=model,
        instruction=instruction,
        output_key="voice_result_json",
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
