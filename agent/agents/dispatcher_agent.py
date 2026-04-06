from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from ..helpers import load_prompt


def validate_dispatcher_result(data: Dict[str, Any]) -> Dict[str, Any]:
    status = str(data.get("status", "")).strip()
    route = str(data.get("route", "")).strip()
    intent = str(data.get("intent", "")).strip()
    reason = str(data.get("reason", "")).strip()
    search_query = str(data.get("search_query", "")).strip()

    allowed_routes = {"doc_search", "kb_answer"}
    doc_route_intents = {"doc_search", "show_more", "show_all", "file_download"}
    kb_route_intents = {"kb_answer", "smalltalk"}
    allowed_intents = doc_route_intents | kb_route_intents

    if status != "ok":
        raise ValueError(f"Invalid status: {status}")

    if route not in allowed_routes:
        raise ValueError(f"Invalid route: {route}")

    if intent not in allowed_intents:
        raise ValueError(f"Invalid intent: {intent}")

    if intent in doc_route_intents and route != "doc_search":
        raise ValueError("doc_search route intents must use route=doc_search")

    if intent in kb_route_intents and route != "kb_answer":
        raise ValueError("intent=kb_answer|smalltalk must use route=kb_answer")

    follow_up_no_query = {"show_more", "show_all", "file_download"}
    if intent not in follow_up_no_query and intent != "smalltalk" and not search_query:
        raise ValueError("search_query is required for this intent")

    if not reason:
        raise ValueError("reason is required")

    return {
        "status": status,
        "route": route,
        "intent": intent,
        "search_query": search_query,
        "reason": reason,
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
- doc_search, show_more, show_all, file_download (только с route=doc_search)
- kb_answer, smalltalk (только с route=kb_answer)

Правила:
- smalltalk идёт в route=kb_answer
- show_more / show_all / file_download — follow-up к списку документов, route=doc_search
- используй только snake_case
"""
    return LlmAgent(
        name="dispatcher_agent",
        model=model,
        instruction=load_prompt("dispatcher_agent_prompt.md", fallback),
        output_key="dispatcher_result_json",
    )