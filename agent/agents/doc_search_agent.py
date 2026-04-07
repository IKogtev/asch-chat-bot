from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import KBSEARCH_MCP_URL, MCP_TOKEN, MCP_TIMEOUT_SEC
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher

logger = setup_logger("doc_search_agent", "agent.log")


def validate_doc_search_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Валидация результата doc_search_agent.
    """
    status = data.get("status")
    mode = data.get("mode")
    message = str(data.get("message", "")).strip()

    if status != "ok":
        raise ValueError(f"Invalid status: {status}")

    if mode not in ("document_list", "no_data", "info", "app_command"):
        raise ValueError(f"Invalid mode: {mode}")

    if not message:
        raise ValueError("message is required")

    return {
        "status": status,
        "mode": mode,
        "message": message,
    }


def create_doc_search_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для поиска документов с подключением MCP kb_search.
    Возвращает только JSON по контракту.
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
            logger.info(f"✓ MCP kbsearch подключен к doc_search_agent: {KBSEARCH_MCP_URL}")

        except Exception as e:
            logger.error(f"✗ Ошибка подключения MCP kbsearch для doc_search_agent: {e}", exc_info=True)
    else:
        logger.warning("⚠ KBSEARCH_MCP_URL не задан — MCP kbsearch не подключён к doc_search_agent")

    fallback = """
Ты — doc_search_agent.

Тебе доступны переменные состояния:
- {user_query} — исходное сообщение пользователя
- {search_query} — нормализованный поисковый запрос
- {doc_search_collection} — имя коллекции для поиска, его надо передавать в kb_search

Правила:
1. Для содержательного запроса на поиск документов ОБЯЗАТЕЛЬНО вызови tool kb_search.
2. Передавай:
   - query={search_query}
   - collection={doc_search_collection}
   - include_metadata=true
3. Если {search_query} пустой, используй {user_query}.
4. Не отвечай по памяти.
5. Возвращай только JSON без markdown fences.
6. В message можно использовать выделение текста для имён файлов и заголовка списка.

Формат ответа:
{
  "status": "ok",
  "mode": "document_list",
  "message": "**Найденные файлы:**\\n1. **Имя файла** — краткий комментарий."
}
"""
    
    prompt_file = "doc_search_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    agent = LlmAgent(
        name="doc_search_agent",
        model=model,
        instruction=instruction,
        tools=tools,
        output_key="doc_search_result_json",
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent