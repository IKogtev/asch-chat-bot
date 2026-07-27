from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.genai.types import GenerateContentConfig
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset

from utils.logger import setup_logger
from ..config import KBSEARCH_MCP_URL, MCP_TIMEOUT_SEC, MCP_TOKEN, DOC_SEARCH_TEMPERATURE
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset
from ..doc_search_kb_context import allowed_document_ids
from ..doc_search_validation import DOC_SEARCH_MAX_ATTEMPTS, DocSearchRetryableValidationError
from .validation_utils import build_validation_error

logger = setup_logger("doc_search_agent", "agent.log")


def _parse_is_relevant(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def _parse_new_rank(value: Any) -> int | None:
    if value is None:
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 1 else None


def _kb_hits_from_context(context: Dict[str, Any]) -> list[Dict[str, Any]]:
    raw = context.get("_doc_search_kb_hits")
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, dict)]


def validate_doc_search_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет и нормализует результат `doc_search_agent`.

    Ожидаемый контракт:
    - `status="ok"`;
    - `mode` один из `document_list`, `no_data`, `info`, `app_command`;
    - для `document_list` обязателен непустой массив `results` с релевантными документами;
    - для остальных режимов обязателен непустой `message`, а `results` должен отсутствовать или быть пустым.

    Нормализация для `document_list`:
    - отбрасываются элементы не-`dict`;
    - обязательны `document_id`, `source_name`, `new_rank` (для релевантных);
    - `is_relevant` опционален: если поле отсутствует, элемент считается релевантным;
    - в итоговый список попадают только релевантные, отсортированные по `new_rank`;
    - каждый `document_id` должен быть из `_doc_search_kb_hits`, если kb_search вернул документы;
      при неверных id на попытке 1 — retry; на финальной попытке — отбрасываются;
    - `snippet` игнорируется (не сохраняется);
    - `source_path` приводится к строке или `None`.

    При нарушении контракта выбрасывает `ValueError` с диагностическим описанием,
    пригодным для логирования и локализации сбоя на этапе отладки.
    При пустом итоге при непустом kb_search — `DocSearchRetryableValidationError`.
    """
    agent_name = "doc_search_agent"
    kb_hits = _kb_hits_from_context(context)
    allowed_ids = allowed_document_ids(kb_hits)
    kb_was_nonempty = bool(allowed_ids)
    attempt = int(context.get("doc_search_attempt") or 1)
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

    if mode == "no_data" and kb_was_nonempty:
        raise DocSearchRetryableValidationError(
            "empty_relevant",
            "kb_search returned documents but mode=no_data",
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
        invalid_doc_ids: list[str] = []
        relevant_items: list[Dict[str, Any]] = []
        for index, item in enumerate(results_raw):
            if not isinstance(item, dict):
                invalid_reasons.append(f"item[{index}] is {type(item).__name__}, expected dict")
                continue

            if "is_relevant" in item:
                is_relevant = _parse_is_relevant(item.get("is_relevant"))
                if is_relevant is None:
                    invalid_reasons.append(
                        f"item[{index}] invalid is_relevant={item.get('is_relevant')!r}"
                    )
                    continue
            else:
                is_relevant = True

            if not is_relevant:
                continue

            if "new_rank" not in item:
                invalid_reasons.append(f"item[{index}] missing new_rank")
                continue
            new_rank = _parse_new_rank(item.get("new_rank"))

            if new_rank is None:
                invalid_reasons.append(
                    f"item[{index}] is_relevant=true requires new_rank>=1, got {item.get('new_rank')!r}"
                )
                continue

            document_id = str(item.get("document_id") or "").strip()
            source_name = str(item.get("source_name") or "").strip()
            if not document_id:
                invalid_reasons.append(f"item[{index}] missing document_id")
                continue
            if kb_was_nonempty and document_id not in allowed_ids:
                invalid_doc_ids.append(document_id)
                continue
            if not source_name:
                invalid_reasons.append(f"item[{index}] missing source_name")
                continue

            path_raw = item.get("source_path") or item.get("relative_path")
            relevant_items.append(
                {
                    "document_id": document_id,
                    "source_name": source_name,
                    "source_path": str(path_raw).strip() if path_raw else None,
                    "new_rank": new_rank,
                    "_order": index,
                }
            )

        unique_invalid_doc_ids = list(dict.fromkeys(invalid_doc_ids))
        if (
            unique_invalid_doc_ids
            and attempt < DOC_SEARCH_MAX_ATTEMPTS
            and not relevant_items
        ):
            raise DocSearchRetryableValidationError(
                "invalid_document_id",
                ", ".join(unique_invalid_doc_ids[:8]),
            )

        if unique_invalid_doc_ids:
            logger.warning(
                "doc_search_agent: dropped %s invalid document_id(s) on attempt %s: %s",
                len(unique_invalid_doc_ids),
                attempt,
                ", ".join(unique_invalid_doc_ids[:8]),
            )

        if invalid_reasons:
            raise build_validation_error(
                agent=agent_name,
                stage="results_normalization",
                problem="document_list validation failed: " + "; ".join(invalid_reasons[:8]),
                data=data,
                fields=("mode", "results"),
            )

        if not relevant_items:
            if kb_was_nonempty:
                raise DocSearchRetryableValidationError(
                    "empty_relevant",
                    "document_list has no relevant items after filtering",
                )
            raise build_validation_error(
                agent=agent_name,
                stage="results_normalization",
                problem="document_list has no items with is_relevant=true; use mode='no_data' instead",
                data=data,
                fields=("mode", "results"),
            )

        relevant_items.sort(key=lambda entry: (entry["new_rank"], entry["_order"]))
        validated = [
            {
                "document_id": item["document_id"],
                "source_name": item["source_name"],
                "source_path": item["source_path"],
            }
            for item in relevant_items
        ]

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
2. Передавай в `kb_search`:
   - `query` — нормализуй **от** `{doc_search_query}` (глоссарий + опечатки из промпта + убрать мусорные слова); не подставляй `Форт Нокс` вместо `Fort Knox`;
   - `collection` = `{doc_search_collection}` (строго, не `default_collection`);
   - `include_metadata=true`
   - `search_profile="doc_search"` (обязательно при каждом вызове kb_search)
3. Не бери название продукта для `query` из TEXT в CONTEXT — только из `doc_search_query` / `user_query` и таблицы канонов.
4. Не отвечай по памяти.
5. Возвращай только JSON без markdown fences.
6. При mode=document_list список пользователю не показываешь: JSON уходит в БД, первую порцию и кнопки рисует UI бота. Поле message можно оставить пустой строкой или заполнить служебно — на экран оно не выводится как список документов.
7. В results включай только релевантные документы из CONTEXT kb_search; document_id должен совпадать с DOCUMENT_ID из CONTEXT.
8. У каждого элемента results обязателен new_rank (целое ≥ 1); snippet и is_relevant не передавай.
9. Если пользователь указал тип материала (презентер, сториз, ПФ и т.д.), в results только файлы с совпадением типа в FILE_NAME.
10. Если {doc_search_rerank_only}=true — kb_search не вызывай, переранжируй по CONTEXT из предыдущего вызова. Причина повтора: {doc_search_retry_reason}.

Формат ответа:
{
  "status": "ok",
  "mode": "document_list",
  "message": "",
  "results": [
    {
      "document_id": "...",
      "source_name": "...",
      "source_path": null,
      "new_rank": 1
    }
  ]
}
"""

    prompt_file = "doc_search_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    name = "doc_search_agent"
    # Конфигурация генерации с принудительным JSON Output и схемой данных
    config_params = {}
    if DOC_SEARCH_TEMPERATURE != -1:
        logger.debug(f"Agent {name} it's temperature: {DOC_SEARCH_TEMPERATURE}")
        config_params["temperature"] = DOC_SEARCH_TEMPERATURE
    else:
        logger.debug(f"Agent {name} temperature set to -1 so google adk decide himself")
    agent = LlmAgent(
        name=name,
        model=model,
        instruction=instruction,
        tools=tools,
        output_key="doc_search_result_json",
        # output_schema=? TODO здесь можно добавить схему по которой будет модель работать
        generate_content_config=GenerateContentConfig(**config_params) if config_params else None
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
