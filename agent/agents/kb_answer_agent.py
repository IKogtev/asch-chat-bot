from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset
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

logger = setup_logger("kb_answer_agent", "agent.log")


def validate_kb_answer_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Валидация результата kb_answer_agent.
    """
    status = data.get("status")
    mode = data.get("mode")
    message = str(data.get("message", "")).strip()
    source = data.get("source")

    if status != "ok":
        raise ValueError(f"Invalid status: {status}")

    if mode not in ("text_answer", "no_data"):
        raise ValueError(f"Invalid mode: {mode}")

    if not message:
        raise ValueError("message is required")

    if source not in ("faq_search", "kb_search", "faq_search+kb_search", "none"):
        raise ValueError(f"Invalid source: {source}")

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

            kbsearch_toolset = McpToolset(
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

            faqsearch_toolset = McpToolset(
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

    fallback = """
Ты - kb_answer_agent.

Тебе доступны переменные:
- {user_query} - исходный вопрос пользователя
- {search_query} - нормализованный поисковый запрос
- {faq_collection} - имя коллекции для faq_search
- {kb_answer_collection} - имя коллекции для kb_search
- {intent} - тип запроса (kb_answer, smalltalk, doc_search)

Правила:
1. Если {intent} == "smalltalk":
   - не вызывай faq_search
   - не вызывай kb_search
   - ответь кратко и естественно

2. Если {intent} != "smalltalk":
   - сначала ОБЯЗАТЕЛЬНО вызови faq_search
   - передай: query={search_query}, collection={faq_collection}
   - если {search_query} не пустой, используй его
   - иначе используй {user_query}

3. Если faq_search дал точный или достаточно уверенный прямой ответ на вопрос:
   - используй только faq_search
   - kb_search не вызывай
   - верни source="faq_search"

4. Если faq_search дал частично релевантный, слабый или неполный результат:
   - вызови kb_search
   - передай: query={search_query}, collection={kb_answer_collection}, include_metadata=true
   - если {search_query} пустой, используй {user_query}
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
{
  "status": "ok",
  "mode": "text_answer",
  "message": "краткий ответ",
  "source": "faq_search"
}
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
