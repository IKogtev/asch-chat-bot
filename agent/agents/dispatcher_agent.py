from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from ..config import DISPATCHER_TEMPERATURE, build_generate_content_config
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from utils.logger import setup_logger
from .validation_utils import build_validation_error

logger = setup_logger("dispatcher_agent", "agent.log")

ASSISTANT_CAPABILITIES_SMALLTALK_EXAMPLES = (
    "что ты умеешь",
    "что умеешь",
    "что ты можешь",
    "что можешь",
    "чем ты можешь помочь",
    "чем можешь помочь",
    "какие у тебя возможности",
    "каковы твои возможности",
    "на что ты способен",
    "на что способен",
)


def validate_dispatcher_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет и нормализует результат `dispatcher_agent`.

    Ожидаемый контракт:
    - `status="ok"`;
    - `route` один из `doc_search`, `kb_answer`, `product_selection`;
    - `intent` один из `doc_search`, `show_more`, `show_all`, `file_download`,
      `kb_answer`, `smalltalk`, `product_card`, `product_kit`, `product_filter`,
      `product_compare`, `product_attribute_values`;
    - `reason` обязателен всегда.

    Семантические правила:
    - `doc_search`, `show_more`, `show_all`, `file_download` допустимы только с `route="doc_search"`;
    - `kb_answer` и `smalltalk` допустимы только с `route="kb_answer"`;
    - follow-up intent (`show_more`, `show_all`, `file_download`) не должен содержать `search_query`;
    - `smalltalk` должен иметь пустой `search_query`;
    - для `doc_search` с `intent="doc_search"` ожидается **дословный** текст последнего сообщения пользователя в `search_query` (нормализацию под поиск делает downstream `doc_search_agent`);
    - для `kb_answer` обязателен непустой `search_query` (может быть нормализован диспетчером).

    Возвращает нормализованный словарь с полями:
    - `status`
    - `route`
    - `intent`
    - `search_query`
    - `reason`

    При нарушении контракта выбрасывает `ValueError` с диагностическим описанием,
    пригодным для логирования и локализации сбоя на этапе отладки.
    """
    agent_name = "dispatcher_agent"
    _ = context
    allowed_routes = {"doc_search", "kb_answer", "product_selection"}
    doc_route_intents = {"doc_search", "show_more", "show_all", "file_download"}
    kb_route_intents = {"kb_answer", "smalltalk"}
    product_route_intents = {
        "product_card",
        "product_kit",
        "product_filter",
        "product_compare",
        "product_attribute_values",
    }
    allowed_intents = doc_route_intents | kb_route_intents | product_route_intents
    follow_up_no_query = {"show_more", "show_all", "file_download"}

    def _validate_payload_type(payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise build_validation_error(
                agent=agent_name,
                stage="payload_type",
                problem=f"expected dict, got {type(payload).__name__}",
            )

    def _validate_basic_fields(payload: Dict[str, Any]) -> tuple[str, str, str, str, str]:
        status = str(payload.get("status", "")).strip()
        route = str(payload.get("route", "")).strip()
        intent = str(payload.get("intent", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        search_query = str(payload.get("search_query", "")).strip()

        if status != "ok":
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem=f"invalid status {status!r}, expected 'ok'",
                data=payload,
                fields=("status", "route", "intent"),
            )

        if route not in allowed_routes:
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem=f"invalid route {route!r}, expected one of {sorted(allowed_routes)}",
                data=payload,
                fields=("status", "route", "intent"),
            )

        if intent not in allowed_intents:
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem=f"invalid intent {intent!r}, expected one of {sorted(allowed_intents)}",
                data=payload,
                fields=("status", "route", "intent"),
            )

        if not reason:
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem="reason is required",
                data=payload,
                fields=("route", "intent", "reason"),
            )

        return status, route, intent, reason, search_query

    def _validate_semantics(payload: Dict[str, Any], route: str, intent: str, search_query: str) -> None:
        if intent in doc_route_intents and route != "doc_search":
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="doc_search intents must use route='doc_search'",
                data=payload,
                fields=("route", "intent"),
            )

        if intent in kb_route_intents and route != "kb_answer":
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="kb_answer and smalltalk intents must use route='kb_answer'",
                data=payload,
                fields=("route", "intent"),
            )

        if intent in product_route_intents and route != "product_selection":
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="product intents must use route='product_selection'",
                data=payload,
                fields=("route", "intent"),
            )

        if intent in follow_up_no_query and search_query:
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="follow-up intents must not carry search_query",
                data=payload,
                fields=("intent", "search_query"),
            )

        if intent == "smalltalk" and search_query:
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="smalltalk must have empty search_query",
                data=payload,
                fields=("intent", "search_query"),
            )

        if intent not in follow_up_no_query and intent != "smalltalk" and not search_query:
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="search_query is required for doc_search, kb_answer, and product intents",
                data=payload,
                fields=("route", "intent", "search_query"),
            )

    _validate_payload_type(data)
    status, route, intent, reason, search_query = _validate_basic_fields(data)
    _validate_semantics(data, route, intent, search_query)

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
Use state variable {from_glossary} as a dictionary of terms already found by code.
Do not call tools and do not invent additional expansions.
If a user term is present in {from_glossary}, use its definition when choosing route and intent.
Do not substitute definitions into search_query and do not replace abbreviations with full names — downstream code expands the query.
High-priority product-card rule: if the latest user message asks to show, open, display, describe, or provide parameters/card/details for a numeric product code, return route="product_selection", intent="product_card", reason="product_card". Examples: "покажи 8914", "параметры 8914", "карточка 8914". Do not route these messages to kb_answer as applicability or explanation questions.
High-priority product-focus rule: if the latest user message is "Что сейчас в фокусе?" or "что в фокусе", return route="product_selection", intent="product_filter", reason="product_filter", search_query="покажи продукты в фокусе". Do not route these messages to kb_answer.

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
- product_selection

Разрешённые intent:
- doc_search, show_more, show_all, file_download (только с route=doc_search)
- kb_answer, smalltalk (только с route=kb_answer)
- product_card, product_kit, product_filter, product_compare (только с route=product_selection)

Правила:
- smalltalk идёт в route=kb_answer
- вопросы о возможностях ассистента относи к smalltalk, включая разговорные формулировки:
  "что ты умеешь", "что умеешь", "что ты можешь", "что можешь",
  "чем ты можешь помочь", "чем можешь помочь", "какие у тебя возможности",
  "каковы твои возможности", "на что ты способен", "на что способен"
- для таких вопросов верни route="kb_answer", intent="smalltalk", search_query=""
- show_more / show_all / file_download - follow-up к списку документов, route=doc_search
- используй только snake_case
"""
    prompt_file = "dispatcher_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    name = "dispatcher_agent"
    if DISPATCHER_TEMPERATURE != -1:
        logger.debug(f"Agent {name} it's temperature: {DISPATCHER_TEMPERATURE}")
    else:
        logger.debug(f"Agent {name} temperature set to -1 so google adk decide himself")
    agent = LlmAgent(
        name=name,
        model=model,
        instruction=instruction,
        include_contents="none",
        output_key="dispatcher_result_json",
        generate_content_config=build_generate_content_config(DISPATCHER_TEMPERATURE),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
