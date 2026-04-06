from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import KBSEARCH_MCP_URL, MCP_TOKEN, MCP_TIMEOUT_SEC
from ..helpers import load_prompt

logger = setup_logger("doc_search_agent", "agent.log")


def validate_doc_search_result(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Валидация результата doc_search_agent.
    Для mode=document_list обязателен непустой results (полный список после kb_search + LLM).
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

    results_raw = data.get("results")
    validated: list[Dict[str, Any]] = []

    if mode == "document_list":
        if not isinstance(results_raw, list) or not results_raw:
            raise ValueError("document_list requires non-empty results array")
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            document_id = item.get("document_id")
            source_name = item.get("source_name")
            if not document_id or not source_name:
                continue
            validated.append(
                {
                    "document_id": document_id,
                    "source_name": str(source_name),
                    "source_path": str(item["source_path"]).strip()
                    if item.get("source_path")
                    else None,
                    "snippet": str(item.get("snippet") or "").strip()[:500],
                }
            )
        if not validated:
            raise ValueError("document_list: no valid items in results")

    return {
        "status": status,
        "mode": mode,
        "message": message,
        "results": validated,
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

Формат ответа (для document_list обязателен массив results — полный список документов):
{
  "status": "ok",
  "mode": "document_list",
  "message": "Краткое вводное предложение.",
  "results": [
    {"document_id": "...", "source_name": "...", "source_path": null, "snippet": "..."}
  ]
}
"""

    return LlmAgent(
        name="doc_search_agent",
        model=model,
        instruction=load_prompt("doc_search_agent_prompt.md", fallback),
        tools=tools,
        output_key="doc_search_result_json",
    )