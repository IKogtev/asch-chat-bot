from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from ..helpers import load_prompt


def validate_dispatcher_result(data: Dict[str, Any]) -> Dict[str, Any]:
    status = data.get("status", "").strip()
    route = data.get("route", "").strip()
    intent = data.get("intent", "").strip()
    
    if status != "ok":
        raise ValueError(f"Invalid status: {status}")
    
    if route not in ("doc_search", "kb_answer"):
        raise ValueError(f"Invalid route: {route}")
    
    # search_query обязателен только для поисковых запросов
    search_query = data.get("search_query", "").strip()
    if intent not in ("smalltalk", "greeting") and not search_query:
        raise ValueError("search_query is required for non-smalltalk intents")
    
    return {
        "status": status,
        "route": route,
        "intent": intent,
        "search_query": search_query,  # Может быть пустым для smalltalk
        "reason": data.get("reason", ""),
    }

def create_dispatcher_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для маршрутизации запросов.
    """
    fallback = """
Ты dispatcher_agent.
Верни только JSON без markdown и без пояснений.

Формат:
{
  "status": "ok",
  "route": "doc_search",
  "intent": "doc_search",
  "reason": "user asks to find documents",
  "search_query": "нормализованный поисковый запрос"
}

Разрешённые route:
- doc_search
- kb_answer

Разрешённые intent:
- doc_search
- kb_answer
- smalltalk

Правила:
- smalltalk идёт в route=kb_answer
- используй только snake_case
"""
    return LlmAgent(
        name="dispatcher_agent",
        model=model,
        instruction=load_prompt("dispatcher_agent_prompt.md", fallback),
        output_key="dispatcher_result_json",
    )