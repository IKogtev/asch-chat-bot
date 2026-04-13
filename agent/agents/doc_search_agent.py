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
    Проверяет и нормализует результат выдачи от doc_search_agent в соответствии с бот-контрактом.

    Валидация и обработка:
      - Ожидается dict с ключами: status, mode, message, results.
      - Для mode = "document_list" обязательно: status="ok", results - непустой список документов.
        Каждый документ требует ключей document_id и source_name, остальные поля опциональны.
        Не валидные или помеченные is_relevant=false — игнорируются.
        Поле message не предназначено для показа пользователю при mode=document_list, оставляется пустым или служебным.
      - Для mode = "no_data", "info", "app_command": message обязателен и может быть показан пользователю.
      - Любой статус, отличный от "ok", считается ошибкой.

    Исключены устаревшие форматы (mode="search_results") — допускается только стандартизованный контракт.

    :param data: dict — ответ doc_search_agent (разобранный JSON).
    :return: dict — нормализованный результат, подходящий для дальнейшей обработки.
    :raises: ValueError при несоответствии контракта.
    """
    if not isinstance(data, dict):
        raise ValueError("doc_search result must be a dict")

    mode = data.get("mode")

    # Строго принимаем только актуальные режимы
    allowed_modes = ("document_list", "no_data", "info", "app_command")
    if mode not in allowed_modes:
        raise ValueError(f"Invalid mode: {mode}")

    status = data.get("status")
    # Если статус не прописан, проставим "ok" для штатных режимов (doc_search_agent всегда работает штатно)
    if status is None and mode in allowed_modes:
        status = "ok"
        data = {**data, "status": status}

    if status != "ok":
        raise ValueError(f"Invalid status: {status}")

    message = str(data.get("message", "")).strip()

    # Для не-document_list message обязателен (для UI)
    if mode != "document_list" and not message:
        raise ValueError("message is required for this mode")

    results_raw = data.get("results")
    validated: list[Dict[str, Any]] = []

    if mode == "document_list":
        # Для document_list: "results" — массив документов, не пустой
        if not isinstance(results_raw, list) or not results_raw:
            raise ValueError("document_list requires non-empty results array")
        for item in results_raw:
            if not isinstance(item, dict):
                continue
            # Пропускаем нерелевантные (если присутствует is_relevant)
            if item.get("is_relevant") is False:
                continue
            document_id = item.get("document_id")
            source_name = item.get("source_name")
            if not document_id or not source_name:
                continue
            validated.append(
                {
                    "document_id": document_id,
                    "source_name": str(source_name),
                    "source_path": str(item["source_path"]).strip() if item.get("source_path") else None,
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
    Создаёт и возвращает агента doc_search_agent для поиска документов.

    Особенности и детали:
      - Агент интегрируется с MCP kb_search через инструмент McpToolset (если указан KBSEARCH_MCP_URL).
      - Ожидает всегда контракт, подходящий под validate_doc_search_result.
      - Возвращаемый агент — только JSON (не текст!), используемый следующими слоями (БД/UI).
      - При ошибке подключения kb_search логирует, но не падает.

    :param model: LiteLlm — модель для LlmAgent
    :return: LlmAgent — настроенный агент для поиска документов
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
6. При mode=document_list пользователю список не показываешь ты: JSON уходит в БД, первую порцию и кнопки рисует UI бота. Поле message можно оставить пустой строкой или заполнить служебно — на экран оно не выводится как список документов.

Формат ответа (для document_list обязателен массив results — полный список документов):
{
  "status": "ok",
  "mode": "document_list",
  "message": "",
  "results": [
    {"document_id": "...", "source_name": "...", "source_path": null, "snippet": "..."}
  ]
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