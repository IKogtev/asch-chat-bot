from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import (
    FAQSEARCH_MCP_TIMEOUT_SEC,
    FAQSEARCH_MCP_TOKEN,
    FAQSEARCH_MCP_URL,
    KBSEARCH_MCP_URL,
    MCP_TIMEOUT_SEC,
    MCP_TOKEN,
)
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset
from .validation_utils import build_validation_error

logger = setup_logger("kb_answer_agent", "agent.log")

ASSISTANT_CAPABILITIES_ANSWER = "Я умею искать документы и помогать продавать продукты АСЖ."


def validate_kb_answer_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет и нормализует результат `kb_answer_agent`.

    Ожидаемый контракт:
    - `status="ok"`;
    - `mode` один из `text_answer`, `no_data`;
    - `message` обязателен и не должен быть пустым;
    - `source` один из `faq_search`, `kb_search`, `faq_search+kb_search`, `none`.

    Семантические правила:
    - при `mode="no_data"` обязателен `source="none"`;
    - при `mode="text_answer"` значение `source="none"` недопустимо.

    Возвращает нормализованный словарь с полями:
    - `status`
    - `mode`
    - `message`
    - `source`

    При нарушении контракта выбрасывает `ValueError` с диагностическим описанием,
    пригодным для логирования и локализации сбоя на этапе отладки.
    """
    agent_name = "kb_answer_agent"
    allowed_sources = ("faq_search", "kb_search", "faq_search+kb_search", "none")
    intent = str((context or {}).get("intent", "")).strip()

    def _validate_payload_type(payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise build_validation_error(
                agent=agent_name,
                stage="payload_type",
                problem=f"expected dict, got {type(payload).__name__}",
            )

    def _validate_basic_fields(payload: Dict[str, Any]) -> tuple[str, str, str, str]:
        status = str(payload.get("status", "")).strip()
        mode = str(payload.get("mode", "")).strip()
        message = str(payload.get("message", "")).strip()
        source = str(payload.get("source", "")).strip()

        if status != "ok":
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem=f"invalid status {status!r}, expected 'ok'",
                data=payload,
                fields=("status", "mode", "source"),
            )

        if mode not in ("text_answer", "no_data"):
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem=f"invalid mode {mode!r}, expected 'text_answer' or 'no_data'",
                data=payload,
                fields=("status", "mode", "source"),
            )

        if not message:
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem="message is required",
                data=payload,
                fields=("mode", "message", "source"),
            )

        if source not in allowed_sources:
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem=f"invalid source {source!r}, expected one of {list(allowed_sources)}",
                data=payload,
                fields=("mode", "source"),
            )

        return status, mode, message, source

    def _validate_semantics(payload: Dict[str, Any], mode: str, source: str) -> None:
        if mode == "no_data" and source != "none":
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="mode='no_data' requires source='none'",
                data=payload,
                fields=("mode", "source"),
            )

        if mode == "text_answer" and source == "none" and intent != "smalltalk":
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="mode='text_answer' must not use source='none' outside smalltalk",
                data=payload,
                fields=("mode", "source", "intent"),
            )

    _validate_payload_type(data)
    status, mode, message, source = _validate_basic_fields(data)
    _validate_semantics(data, mode, source)

    return {
        "status": status,
        "mode": mode,
        "message": message,
        "source": source,
    }


def create_kb_answer_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для ответа по базе знаний.

    Агент поддерживает два MCP-инструмента:
    - faq_search: приоритетный поиск по FAQ;
    - kb_search: fallback и дополнение к FAQ-ответу.

    Для smalltalk инструменты не вызываются.
    """
    tools = []

    if KBSEARCH_MCP_URL:
        try:
            headers = {"Authorization": f"Bearer {MCP_TOKEN}"} if MCP_TOKEN else None

            kbsearch_toolset = RefreshingMcpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=KBSEARCH_MCP_URL,
                    headers=headers,
                    timeout=MCP_TIMEOUT_SEC,
                ),
                tool_filter=["kb_search"],
            )

            tools.append(kbsearch_toolset)
            logger.info(f"MCP kbsearch подключен к kb_answer_agent: {KBSEARCH_MCP_URL}")

        except Exception as e:
            logger.error(
                f"Ошибка подключения MCP kbsearch для kb_answer_agent: {e}",
                exc_info=True,
            )
    else:
        logger.warning(
            "KBSEARCH_MCP_URL не задан - MCP kbsearch не подключён к kb_answer_agent"
        )

    if FAQSEARCH_MCP_URL:
        try:
            faq_headers = (
                {"Authorization": f"Bearer {FAQSEARCH_MCP_TOKEN}"}
                if FAQSEARCH_MCP_TOKEN
                else None
            )

            faqsearch_toolset = RefreshingMcpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=FAQSEARCH_MCP_URL,
                    headers=faq_headers,
                    timeout=FAQSEARCH_MCP_TIMEOUT_SEC,
                ),
                tool_filter=["faq_search"],
            )

            tools.append(faqsearch_toolset)
            logger.info(f"MCP faqsearch подключен к kb_answer_agent: {FAQSEARCH_MCP_URL}")

        except Exception as e:
            logger.error(
                f"Ошибка подключения MCP faqsearch для kb_answer_agent: {e}",
                exc_info=True,
            )
    else:
        logger.warning(
            "FAQSEARCH_MCP_URL не задан - MCP faqsearch не подключён к kb_answer_agent"
        )

    fallback = f"""
Ты - kb_answer_agent.

Тебе доступны переменные:
- {{user_query}} - исходный вопрос пользователя
- {{search_query}} - нормализованный поисковый запрос
- {{faq_collection}} - имя коллекции для faq_search
- {{kb_answer_collection}} - имя коллекции для kb_search
- {{intent}} - тип запроса (kb_answer, smalltalk, doc_search)

Правила:
1. Если {{intent}} == "smalltalk":
   - не вызывай faq_search
   - не вызывай kb_search
   - если {{user_query}} или {{search_query}} - это вопрос о возможностях ассистента
     (например: "что ты умеешь", "что умеешь", "что ты можешь", "что можешь",
     "чем ты можешь помочь", "чем можешь помочь", "какие у тебя возможности",
     "каковы твои возможности", "на что ты способен", "на что способен"),
     отвечай ровно одной фразой: "{ASSISTANT_CAPABILITIES_ANSWER}"
   - для этого ответа верни source="none"
   - не импровизируй и не добавляй новых деталей
   - в остальных smalltalk-случаях ответь кратко и естественно

2. Если {{intent}} != "smalltalk":
   - сначала ОБЯЗАТЕЛЬНО вызови faq_search
   - передай: query={{user_query}}, collection={{faq_collection}}

3. Если faq_search дал точный или достаточно уверенный прямой ответ на вопрос:
   - используй только faq_search
   - kb_search не вызывай
   - верни source="faq_search"

4. Если faq_search дал частично релевантный, слабый или неполный результат:
   - вызови kb_search
   - передай: query={{search_query}}, collection={{kb_answer_collection}}, include_metadata=true, search_profile="kb_answer"
   - если {{search_query}} пустой, используй {{user_query}}
   - используй kb_search только как дополнение к faq_search
   - если ответ собран по обоим источникам, верни source="faq_search+kb_search"
   - если в итоговый ответ вошли только данные kb_search, верни source="kb_search"

5. Если данные faq_search и kb_search противоречат друг другу:
   - приоритет у faq_search
   - конфликтующие детали из kb_search не используй

6. Если оба поиска не дали достаточных данных:
   - не отвечай по памяти
   - верни mode="no_data"
   - верни source="none"
   - в message кратко скажи, что точный ответ не найден

7. Для intent=kb_answer запрещено отвечать без обращения к faq_search, кроме случая smalltalk

8. Верни только JSON без markdown

Формат ответа:
{{
  "status": "ok",
  "mode": "text_answer",
  "message": "краткий ответ",
  "source": "faq_search"
}}
"""
    prompt_file = "kb_answer_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    agent = LlmAgent(
        name="kb_answer_agent",
        model=model,
        instruction=instruction,
        tools=tools,
        output_key="kb_answer_result_json",
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
