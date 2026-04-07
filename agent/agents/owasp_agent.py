from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from ..helpers import load_prompt
from utils.logger import setup_logger
from ..prompt_loader import start_prompt_watcher

logger = setup_logger("owasp_agent", "agent.log")

def validate_owasp_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Валидация результата owasp_agent.
    """
    status = data.get("status")
    route = data.get("route")
    
    if status not in ("ok", "blocked"):
        raise ValueError(f"Invalid status: {status}")
    
    if route not in ("continue", "reject"):
        raise ValueError(f"Invalid route: {route}")
    
    if status == "blocked" and not data.get("user_message"):
        raise ValueError("blocked status requires user_message")
    
    return {
        "status": status,
        "route": route,
        "reason": data.get("reason", ""),
        "user_message": data.get("user_message", ""),
    }


def create_owasp_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для проверки безопасности запросов (OWASP).
    """
    fallback = """
Ты owasp_agent.
Верни только JSON без markdown и без пояснений.

Безопасный запрос:
{
  "status": "ok",
  "route": "continue",
  "reason": "safe"
}

Небезопасный запрос:
{
  "status": "blocked",
  "route": "reject",
  "reason": "prompt_injection",
  "user_message": "Запрос отклонён по соображениям безопасности."
}
"""
    prompt_file = "owasp_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    agent = LlmAgent(
        name="owasp_agent",
        model=model,
        instruction=instruction,
        output_key="owasp_result_json",
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent