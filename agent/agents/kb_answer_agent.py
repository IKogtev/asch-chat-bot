from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from ..helpers import load_prompt


def validate_kb_answer_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Валидация результата kb_answer_agent.
    """
    status = data.get("status")
    mode = data.get("mode")
    
    if status != "ok":
        raise ValueError(f"Invalid status: {status}")
    
    if mode not in ("text_answer", "no_data"):
        raise ValueError(f"Invalid mode: {mode}")
    
    message = data.get("message", "").strip()
    if not message:
        raise ValueError("message is required")
    
    return {
        "status": status,
        "mode": mode,
        "message": message,
    }


def create_kb_answer_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для формирования ответов на основе базы знаний.
    """
    fallback = """
Ты kb_answer_agent.
Текущий запрос пользователя:
{user_query}

Контекст поиска:
{kb_answer_context_json}

Верни только JSON без markdown и без пояснений.

Формат:
{
  "status": "ok",
  "mode": "text_answer",
  "message": "Краткий ответ по базе знаний"
}

Правила:
- отвечай только на основе переданного контекста;
- если данных мало, честно скажи об этом;
- не возвращай список документов как основной режим.
"""
    return LlmAgent(
        name="kb_answer_agent",
        model=model,
        instruction=load_prompt("kb_answer_agent_prompt.md", fallback),
        output_key="kb_answer_result_json",
    )
