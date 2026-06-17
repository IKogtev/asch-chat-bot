from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from utils.logger import setup_logger
from ..config import KBSEARCH_MCP_URL, MCP_TIMEOUT_SEC, MCP_TOKEN
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset
from .validation_utils import build_validation_error

logger = setup_logger("doc_search_agent", "agent.log")


def validate_doc_search_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет и нормализует результат `doc_search_agent`.

    Ожидаемый контракт:
    - `status="ok"`;
    - `mode` один из `document_list`, `no_data`, `info`, `app_command`;
    - для `document_list` обязателен непустой массив `results`;
    - для остальных режимов обязателен непустой `message`, а `results` должен отсутствовать или быть пустым.

    Нормализация для `document_list`:
    - отбрасываются элементы не-`dict`;
    - отбрасываются элементы с `is_relevant=false`;
    - обязательны `document_id` и `source_name`;
    - `snippet` обрезается до 500 символов;
    - `source_path` приводится к строке или `None`.

    При нарушении контракта выбрасывает `ValueError` с диагностическим описанием,
    пригодным для логирования и локализации сбоя на этапе отладки.
    """
    agent_name = "doc_search_agent"
    _ = context
    allowed_modes = ("document_list", "no_data", "info", "app_command")

    if not isinstance(data, dict):
        raise build_validation_error(
            agent=agent_name,
            stage="payload_type",
            problem=f"expected dict, got {type(data).__name__}",
        )

    mode = str(data.get("mode", "")).strip()
    if mode not in allowed_modes:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid mode {mode!r}, expected one of {list(allowed_modes)}",
            data=data,
            fields=("mode", "status"),
        )

    status = data.get("status")
    if status is None:
        status = "ok"
        data = {**data, "status": status}
    status = str(status).strip()

    if status != "ok":
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid status {status!r}, expected 'ok'",
            data=data,
            fields=("mode", "status"),
        )

    message = str(data.get("message", "")).strip()
    results_raw = data.get("results")
    validated: list[Dict[str, Any]] = []

    if mode != "document_list" and not message:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem=f"mode={mode!r} requires non-empty message",
            data=data,
            fields=("mode", "message"),
        )

    if mode != "document_list" and results_raw not in (None, []):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem=f"mode={mode!r} must not contain results",
            data=data,
            fields=("mode", "results"),
        )

    if mode == "document_list":
        if not isinstance(results_raw, list) or not results_raw:
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="mode='document_list' requires non-empty results array",
                data=data,
                fields=("mode", "results"),
            )

        invalid_reasons: list[str] = []
        for index, item in enumerate(results_raw):
            if not isinstance(item, dict):
                invalid_reasons.append(f"item[{index}] is {type(item).__name__}, expected dict")
                continue
            if item.get("is_relevant") is False:
                invalid_reasons.append(f"item[{index}] filtered by is_relevant=false")
                continue

            document_id = str(item.get("document_id") or "").strip()
            source_name = str(item.get("source_name") or "").strip()
            if not document_id:
                invalid_reasons.append(f"item[{index}] missing document_id")
                continue
            if not source_name:
                invalid_reasons.append(f"item[{index}] missing source_name")
                continue

            validated.append(
                {
                    "document_id": document_id,
                    "source_name": source_name,
                    "source_path": str(item["source_path"]).strip() if item.get("source_path") else None,
                    "snippet": str(item.get("snippet") or "").strip()[:500],
                }
            )

        if not validated:
            raise build_validation_error(
                agent=agent_name,
                stage="results_normalization",
                problem="document_list returned no valid items after normalization: " + "; ".join(invalid_reasons[:5]),
                data=data,
                fields=("mode",),
            )

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
      - Агент интегрируется с MCP kb_search через инструмент McpToolset, если задан KBSEARCH_MCP_URL.
      - Ожидает всегда контракт, подходящий под validate_doc_search_result.
      - Возвращаемый агент — только JSON, без текстового ответа для пользователя.
      - При ошибке подключения kb_search пишет ошибку в лог, но не падает.
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
            logger.info(f"MCP kbsearch подключен к doc_search_agent: {KBSEARCH_MCP_URL}")

        except Exception as e:
            logger.error(
                f"Ошибка подключения MCP kbsearch для doc_search_agent: {e}",
                exc_info=True,
            )
    else:
        logger.warning("KBSEARCH_MCP_URL не задан — MCP kbsearch не подключён к doc_search_agent")

    fallback = """
Use state variable {from_glossary} as a dictionary of terms already found by code.
Do not invent additional expansions.
If a user term is present in {from_glossary}, use its definition as extra context.
For document search, glossary context must not erase document type, product name, codes, or user wording.

Ты — doc_search_agent.

Тебе доступны переменные состояния:
- {user_query} — исходное сообщение пользователя
- {doc_search_query} — запрос с подставленными расшифровками из глоссария (если термины найдены)
- {doc_search_collection} — имя коллекции для поиска, его надо передавать в kb_search

Правила:
1. Для содержательного запроса на поиск документов обязательно вызови tool kb_search.
2. Передавай:
   - query={doc_search_query}
   - collection={doc_search_collection}
   - include_metadata=true
   - search_profile="doc_search" (обязательно при каждом вызове kb_search)
3. Сжатие и канонизация {doc_search_query} под `kb_search` — на твоей стороне; не удаляй тип документа, продукт, коды и слова пользователя из {user_query}.
4. Не отвечай по памяти.
5. Возвращай только JSON без markdown fences.
6. При mode=document_list список пользователю не показываешь: JSON уходит в БД, первую порцию и кнопки рисует UI бота. Поле message можно оставить пустой строкой или заполнить служебно — на экран оно не выводится как список документов.
7. В results — каждый документ из CONTEXT kb_search; отсев только через is_relevant: false, не укорачивай список.

Формат ответа:
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
