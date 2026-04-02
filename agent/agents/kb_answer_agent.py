from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import KBSEARCH_MCP_URL, MCP_TOKEN, MCP_TIMEOUT_SEC
from ..helpers import load_prompt

logger = setup_logger("kb_answer_agent", "agent.log")


def validate_kb_answer_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Валидация результата kb_answer_agent.
    """
    status = data.get("status")
    mode = data.get("mode")
    message = str(data.get("message", "")).strip()

    if status != "ok":
        raise ValueError(f"Invalid status: {status}")

    if mode not in ("text_answer", "no_data"):
        raise ValueError(f"Invalid mode: {mode}")

    if not message:
        raise ValueError("message is required")

    return {
        "status": status,
        "mode": mode,
        "message": message,
    }

def create_kb_answer_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для ответа по базе знаний с прямым вызовом MCP kb_search.
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
            logger.info(f"✓ MCP kbsearch подключен к kb_answer_agent: {KBSEARCH_MCP_URL}")

        except Exception as e:
            logger.error(f"✗ Ошибка подключения MCP kbsearch для kb_answer_agent: {e}", exc_info=True)
    else:
        logger.warning("⚠ KBSEARCH_MCP_URL не задан — MCP kbsearch не подключён к kb_answer_agent")

    fallback = """
Ты — kb_answer_agent.

Тебе доступны переменные состояния:
- {user_query} — исходный вопрос пользователя
- {search_query} — нормализованный поисковый запрос
- {kb_answer_collection} — имя коллекции для поиска
- {intent} — тип запроса (kb_answer, smalltalk, doc_search)

Правила:
1. Если {intent} == "smalltalk":
   - НЕ вызывай kb_search
   - Ответь в дружелюбном разговорном стиле
   - Будь кратким и естественным
   
2. Если {intent} == "kb_answer":
   - ОБЯЗАТЕЛЬНО вызови tool kb_search
   - Передавай: query={search_query}, collection={kb_answer_collection}, include_metadata=true
   - Если {search_query} пустой, используй {user_query}
   - Используй только найденные фрагменты
   - Если точных данных не хватает, скажи это честно

3. Не отвечай по памяти при intent=kb_answer
4. Верни только JSON без markdown

Формат ответа:
{
  "status": "ok",
  "mode": "text_answer",
  "message": "Краткий ответ"
}
"""

    return LlmAgent(
        name="kb_answer_agent",
        model=model,
        instruction=load_prompt("kb_answer_agent_prompt.md", fallback),
        tools=tools,
        output_key="kb_answer_result_json",
    )