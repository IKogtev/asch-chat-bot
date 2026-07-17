from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Dict, Literal, Sequence

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from pydantic import BaseModel, Field

from utils.logger import setup_logger
from ..config import (
    DBHUB_MCP_TIMEOUT_SEC,
    DBHUB_MCP_TOKEN,
    DBHUB_MCP_URL,
    PRODUCT_SELECTION_TEMPERATURE,
    build_generate_content_config,
)
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset
from .validation_utils import build_validation_error

logger = setup_logger("product_selection_agent", "agent.log")

CARD_KIT_TOOL_FILTER = [
    "search_column",
    "execute_sql",
]

FILTER_TOOL_FILTER = [
    "search_table",
    "search_column",
    "search_analytic",
    "search_semantic_template",
    "execute_sql",
]

COMPARE_TOOL_FILTER = [
    "search_column",
    "execute_sql",
]

# Совместимость со старыми тестами / импортами.
PRODUCT_SELECTION_TOOL_FILTER = FILTER_TOOL_FILTER

PRODUCT_SELECTION_MODES = {
    "product_card",
    "product_kit",
    "product_filter",
    "product_compare",
    "product_attribute_values",
    "needs_clarification",
    "no_data",
}

PRODUCT_FIELD_KEYS = ("code", "name", "term", "currency", "folder_kit")
CLARIFICATION_OPTION_FIELD_KEYS = ("code", "name", "term", "currency")
PRODUCT_LIST_FIELD_KEYS = ("code", "name", "term", "currency", "folder_kit", "is_active")
PRODUCT_SELECTION_REQUIRED_TOOL = "execute_sql"

# ADK set_model_response (как у kb_answer): только плоские типы.
# Nested models / Literal['ok'] / $ref ломают automatic function calling.


class ProductSelectionCardKitResponseSchema(BaseModel):
    status: str = Field(description="Всегда ok")
    mode: Literal["product_card", "product_kit", "needs_clarification", "no_data"] = Field(
        description="Режим ответа card/kit"
    )
    message: str = Field(description="Текст ответа на русском")
    used_tables: str = Field(default="", description="Таблицы через запятую, например products")
    resolved_product_code: str = Field(default="", description="Код продукта")
    resolved_product_name: str = Field(default="", description="Название продукта")
    resolved_product_folder_kit: str = Field(default="", description="Папка комплекта")
    clarification_options_json: str = Field(
        default="[]",
        description='JSON-массив [{code,name,term,currency}, ...]',
    )
    products_json: str = Field(
        default="[]",
        description='JSON-массив продуктов [{code,name,...}, ...]',
    )
    attribute_name: str = ""
    attribute_column: str = ""
    attribute_values: str = Field(
        default="",
        description="Значения признака через запятую или JSON-массив строк",
    )


class ProductSelectionFilterResponseSchema(BaseModel):
    status: str = Field(description="Всегда ok")
    mode: Literal[
        "product_filter",
        "product_attribute_values",
        "needs_clarification",
        "no_data",
    ] = Field(description="Режим ответа filter/attribute_values")
    message: str = Field(description="Текст ответа на русском")
    used_tables: str = Field(default="", description="Таблицы через запятую, например products")
    resolved_product_code: str = Field(default="", description="Код продукта")
    resolved_product_name: str = Field(default="", description="Название продукта")
    resolved_product_folder_kit: str = Field(default="", description="Папка комплекта")
    clarification_options_json: str = Field(
        default="[]",
        description='JSON-массив [{code,name,term,currency}, ...]',
    )
    products_json: str = Field(
        default="[]",
        description='JSON-массив продуктов [{code,name,...}, ...]',
    )
    attribute_name: str = ""
    attribute_column: str = ""
    attribute_values: str = Field(
        default="",
        description="Значения признака через запятую или JSON-массив строк",
    )


class ProductSelectionCompareResponseSchema(BaseModel):
    status: str = Field(description="Всегда ok")
    mode: Literal["product_compare", "needs_clarification", "no_data"] = Field(
        description="Режим ответа compare"
    )
    message: str = Field(description="Текст ответа на русском")
    used_tables: str = Field(default="", description="Таблицы через запятую, например products")
    resolved_product_code: str = Field(default="", description="Код продукта")
    resolved_product_name: str = Field(default="", description="Название продукта")
    resolved_product_folder_kit: str = Field(default="", description="Папка комплекта")
    clarification_options_json: str = Field(
        default="[]",
        description='JSON-массив [{code,name,term,currency}, ...]',
    )
    products_json: str = Field(
        default="[]",
        description='JSON-массив продуктов [{code,name,...}, ...]',
    )
    attribute_name: str = ""
    attribute_column: str = ""
    attribute_values: str = Field(
        default="",
        description="Значения признака через запятую или JSON-массив строк",
    )


def _unwrap_json_encoded(value: Any, *, max_depth: int = 3) -> Any:
    """Снимает лишнее JSON-кодирование от set_model_response ('\"8957\"', '\"[]\"')."""
    current = value
    for _ in range(max_depth):
        if not isinstance(current, str):
            return current
        text = current.strip()
        if not text:
            return ""
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return text
        if parsed is current or (isinstance(parsed, str) and parsed == text):
            return parsed
        current = parsed
    return current


def _parse_json_list(value: Any, field_name: str) -> list[Any]:
    if value is None:
        return []
    unwrapped = _unwrap_json_encoded(value)
    if isinstance(unwrapped, list):
        return unwrapped
    if unwrapped is None or unwrapped == "":
        return []
    if isinstance(unwrapped, str):
        text = unwrapped.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{field_name} must be a JSON array: {exc}") from exc
        if not isinstance(parsed, list):
            raise ValueError(f"{field_name} must be a JSON array")
        return parsed
    raise ValueError(f"{field_name} must be a JSON array")


def _parse_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    unwrapped = _unwrap_json_encoded(value)
    if isinstance(unwrapped, list):
        return [str(item).strip() for item in unwrapped if str(item).strip()]
    text = str(unwrapped or "").strip()
    if not text:
        return []
    if text.startswith("["):
        parsed = _parse_json_list(text, "attribute_values")
        return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def coerce_product_selection_schema_payload(data: Dict[str, Any]) -> Dict[str, Any]:
    """Преобразует плоский ADK output_schema в контракт валидатора."""
    payload = dict(data)

    status = str(_unwrap_json_encoded(payload.get("status")) or "").strip()
    mode = str(_unwrap_json_encoded(payload.get("mode")) or "").strip()
    # Модель иногда кладёт mode в status (status=needs_clarification, mode=product_compare).
    content_modes = {
        "product_card",
        "product_kit",
        "product_filter",
        "product_compare",
        "product_attribute_values",
    }
    if status in PRODUCT_SELECTION_MODES and status != "ok":
        if status in {"needs_clarification", "no_data"} and mode in content_modes:
            payload["mode"] = status
        elif mode not in PRODUCT_SELECTION_MODES:
            payload["mode"] = status
        payload["status"] = "ok"
    elif not status:
        payload["status"] = "ok"
    else:
        payload["status"] = status
        if mode:
            payload["mode"] = mode

    if "message" in payload:
        payload["message"] = str(_unwrap_json_encoded(payload.get("message")) or "").strip()

    for key in (
        "attribute_name",
        "attribute_column",
        "resolved_product_code",
        "resolved_product_name",
        "resolved_product_folder_kit",
    ):
        if key in payload:
            payload[key] = str(_unwrap_json_encoded(payload.get(key)) or "").strip()

    if payload.get("resolved_product") is None:
        code = str(payload.get("resolved_product_code") or "").strip()
        name = str(payload.get("resolved_product_name") or "").strip()
        folder_kit = str(payload.get("resolved_product_folder_kit") or "").strip()
        if code or name or folder_kit:
            payload["resolved_product"] = {
                "code": code,
                "name": name,
                "folder_kit": folder_kit,
            }

    if not payload.get("clarification_options"):
        payload["clarification_options"] = _parse_json_list(
            payload.get("clarification_options_json"),
            "clarification_options_json",
        )

    if not payload.get("products"):
        payload["products"] = _parse_json_list(
            payload.get("products_json"),
            "products_json",
        )

    if "used_tables" in payload and not isinstance(payload.get("used_tables"), list):
        tables = _unwrap_json_encoded(payload.get("used_tables"))
        if isinstance(tables, list):
            payload["used_tables"] = [
                str(item).strip() for item in tables if str(item).strip()
            ]
        else:
            tables_text = str(tables or "").strip()
            if not tables_text:
                payload["used_tables"] = []
            elif tables_text.startswith("["):
                payload["used_tables"] = [
                    str(item).strip()
                    for item in _parse_json_list(tables_text, "used_tables")
                    if str(item).strip()
                ]
            else:
                payload["used_tables"] = [
                    part.strip() for part in tables_text.split(",") if part.strip()
                ]

    if "attribute_values" in payload and not isinstance(
        payload.get("attribute_values"), list
    ):
        payload["attribute_values"] = _parse_text_list(payload.get("attribute_values"))

    return payload


@dataclass(frozen=True)
class ProductSelectionAgents:
    card_kit: LlmAgent
    filter: LlmAgent
    compare: LlmAgent


def _normalize_used_tables(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_product(
    value: Any,
    field_keys: tuple[str, ...] = PRODUCT_FIELD_KEYS,
) -> dict[str, str] | None:
    if value is None:
        return None
    if isinstance(value, BaseModel):
        value = value.model_dump()
    if not isinstance(value, dict):
        raise TypeError(f"expected dict, got {type(value).__name__}")

    # Алиасы из product_resolver / set_model_response.
    aliased = dict(value)
    if not str(aliased.get("code") or "").strip():
        aliased["code"] = aliased.get("product_code") or ""
    if not str(aliased.get("name") or "").strip():
        aliased["name"] = (
            aliased.get("canonical_name")
            or aliased.get("alias")
            or aliased.get("product_name")
            or ""
        )
    if not str(aliased.get("folder_kit") or "").strip():
        aliased["folder_kit"] = aliased.get("folder") or ""

    normalized = {}
    for key in field_keys:
        item = str(aliased.get(key, "")).strip()
        if item:
            normalized[key] = item

    return normalized or None


def _normalize_clarification_options(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"expected list, got {type(value).__name__}")

    options: list[dict[str, str]] = []
    for item in value:
        normalized = _normalize_product(item, CLARIFICATION_OPTION_FIELD_KEYS)
        if normalized is None:
            raise ValueError("clarification option must not be empty")
        options.append(normalized)

    return options


def _normalize_products(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"expected list, got {type(value).__name__}")

    products: list[dict[str, str]] = []
    for item in value:
        normalized = _normalize_product(item, PRODUCT_LIST_FIELD_KEYS)
        if normalized is None:
            raise ValueError("product item must not be empty")
        products.append(normalized)

    return products


def _normalize_text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field_name} expected list, got {type(value).__name__}")

    return [str(item).strip() for item in value if str(item).strip()]


def _normalize_tool_calls(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        item = value.strip()
        return {item} if item else set()
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if str(value).strip() else set()


def validate_product_selection_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = "product_selection_agent"
    tool_calls = _normalize_tool_calls(context.get("_adk_tool_calls"))

    if isinstance(data, BaseModel):
        data = data.model_dump()

    if not isinstance(data, dict):
        raise build_validation_error(
            agent=agent_name,
            stage="payload_type",
            problem=f"expected dict, got {type(data).__name__}",
        )

    try:
        data = coerce_product_selection_schema_payload(data)
    except ValueError as exc:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=str(exc),
            data=data,
            fields=("clarification_options_json", "products_json", "attribute_values"),
        ) from exc

    status = str(data.get("status", "")).strip()
    mode = str(data.get("mode", "")).strip()
    message = str(data.get("message", "")).strip()
    used_tables = _normalize_used_tables(data.get("used_tables"))
    attribute_name = str(data.get("attribute_name", "")).strip()
    attribute_column = str(data.get("attribute_column", "")).strip()

    try:
        resolved_product = _normalize_product(data.get("resolved_product"))
        clarification_options = _normalize_clarification_options(
            data.get("clarification_options")
        )
        products = _normalize_products(data.get("products"))
        attribute_values = _normalize_text_list(
            data.get("attribute_values"),
            "attribute_values",
        )
    except (TypeError, ValueError) as exc:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=str(exc),
            data=data,
            fields=("resolved_product", "clarification_options", "products", "attribute_values"),
        ) from exc

    if status != "ok":
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid status {status!r}, expected 'ok'",
            data=data,
            fields=("status", "mode"),
        )

    if mode not in PRODUCT_SELECTION_MODES:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid mode {mode!r}, expected one of {sorted(PRODUCT_SELECTION_MODES)}",
            data=data,
            fields=("status", "mode"),
        )

    if not message:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem="message is required",
            data=data,
            fields=("mode", "message"),
        )

    logger.debug(
        "product_selection validation context: mode=%s resolved_product=%s "
        "clarification_options_count=%s used_tables=%s tool_calls=%s tool_events=%s",
        mode,
        resolved_product,
        len(clarification_options),
        used_tables,
        sorted(tool_calls),
        context.get("_adk_tool_event_summaries") or [],
    )

    if mode in {"product_card", "product_kit"} and not resolved_product:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem=f"mode={mode!r} requires resolved_product",
            data=data,
            fields=("mode", "resolved_product"),
        )

    if mode == "product_kit" and not resolved_product.get("code"):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='product_kit' requires resolved_product.code",
            data=data,
            fields=("mode", "resolved_product"),
        )

    if mode == "product_attribute_values" and not attribute_values:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='product_attribute_values' requires attribute_values",
            data=data,
            fields=("mode", "attribute_values"),
        )

    if mode == "needs_clarification" and not clarification_options:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='needs_clarification' requires clarification_options",
            data=data,
            fields=("mode", "clarification_options"),
        )

    is_kit_resolved = (
        mode == "product_kit"
        and resolved_product
        and resolved_product.get("folder_kit")
    )
    is_clarification_ready = (
        mode == "needs_clarification"
        and clarification_options
    )

    if (
        mode != "no_data"
        and not is_kit_resolved
        and not is_clarification_ready
        and PRODUCT_SELECTION_REQUIRED_TOOL not in tool_calls
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="tool_usage",
            problem=f"required tool {PRODUCT_SELECTION_REQUIRED_TOOL!r} was not called",
            data=data,
            fields=("mode", "used_tables"),
        )

    return {
        "status": status,
        "mode": mode,
        "message": message,
        "used_tables": used_tables,
        "resolved_product": resolved_product,
        "clarification_options": clarification_options,
        "products": products,
        "attribute_name": attribute_name,
        "attribute_column": attribute_column,
        "attribute_values": attribute_values,
    }


def _build_dbhub_tools(tool_filter: Sequence[str], agent_label: str) -> list[Any]:
    tools: list[Any] = []
    if not DBHUB_MCP_URL:
        logger.warning(
            "DBHUB_MCP_URL is empty; dbhub MCP is not connected to %s",
            agent_label,
        )
        return tools

    try:
        headers = {"Authorization": f"Bearer {DBHUB_MCP_TOKEN}"} if DBHUB_MCP_TOKEN else None
        dbhub_toolset = RefreshingMcpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=DBHUB_MCP_URL,
                headers=headers,
                timeout=DBHUB_MCP_TIMEOUT_SEC,
            ),
            tool_filter=list(tool_filter),
        )
        tools.append(dbhub_toolset)
        logger.info("MCP dbhub connected to %s: %s", agent_label, DBHUB_MCP_URL)
    except Exception as e:
        logger.error(
            "Failed to connect MCP dbhub for %s: %s",
            agent_label,
            e,
            exc_info=True,
        )
    return tools


def _create_leaf_agent(
    *,
    model: LiteLlm,
    name: str,
    prompt_file: str,
    fallback: str,
    tool_filter: Sequence[str],
    output_schema: type[BaseModel],
) -> LlmAgent:
    tools = _build_dbhub_tools(tool_filter, name)
    instruction = load_prompt(prompt_file, fallback)
    if PRODUCT_SELECTION_TEMPERATURE != -1:
        logger.debug("Agent %s temperature: %s", name, PRODUCT_SELECTION_TEMPERATURE)
    else:
        logger.debug("Agent %s temperature set to -1 so google adk decide himself", name)

    agent = LlmAgent(
        name=name,
        model=model,
        instruction=instruction,
        tools=tools,
        output_key="product_selection_result_json",
        output_schema=output_schema,
        generate_content_config=build_generate_content_config(PRODUCT_SELECTION_TEMPERATURE),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent


_CARD_KIT_FALLBACK = """
Ты - product_selection_card_kit_agent.
Верни только JSON по output_schema, без markdown.

State:
- user_query: {user_query}
- product_selection_search_query: {product_selection_search_query}
- product_selection_intent: {product_selection_intent}
- product_resolution: {product_resolution}
- from_glossary: {from_glossary}

Сценарии: product_card и product_kit.
Используй только product_resolution для кода продукта. Не резолви имена сам.

Алгоритм:
1. Если product_resolution.status=ambiguous -> mode=needs_clarification с options.
2. Если not_found/error -> mode=no_data.
3. Если resolved: при необходимости search_column(products), затем execute_sql.
4. code всегда как строка: code = '8914'.
5. После успешного SQL сразу финальный JSON. Не повторяй тот же SQL.

product_card SELECT: подтвержденные колонки карточки; message на русском из SQL-строки; resolved_product.
product_kit SELECT: code, name, folder_kit; если folder_kit пуст/не найден -> no_data.
"""

_FILTER_FALLBACK = """
Ты - product_selection_filter_agent.
Верни только JSON по output_schema, без markdown.

State:
- user_query: {user_query}
- product_selection_search_query: {product_selection_search_query}
- product_selection_intent: {product_selection_intent}
- product_filter_resolution: {product_filter_resolution}
- from_glossary: {from_glossary}

Сценарии: product_filter и product_attribute_values.

Алгоритм:
1. search_semantic_template
2. search_column (и search_table при необходимости)
3. search_analytic для категориальных фильтров
4. execute_sql
5. После успешного SQL сразу финальный JSON. Не повторяй тот же SQL.

Правила:
- факты только из SQL текущего запуска;
- code всегда строковый литерал;
- для product_filter включай is_active; архивные помечай **Архивный**.;
- для attribute_values заполни attribute_name/attribute_values (attribute_column для follow-up).
"""

_COMPARE_FALLBACK = """
Ты - product_selection_compare_agent.
Верни только JSON по output_schema, без markdown.

State:
- user_query: {user_query}
- product_selection_search_query: {product_selection_search_query}
- product_selection_intent: {product_selection_intent}
- product_resolutions: {product_resolutions}
- from_glossary: {from_glossary}

Сценарий: product_compare.
Используй только product_resolutions для двух кодов. Не резолви имена сам.

Алгоритм:
1. Если не ровно 2 resolved кода -> needs_clarification или no_data.
2. search_column(products) при необходимости.
3. execute_sql WHERE code IN ('...','...') со всеми полями сравнения.
4. После успешного SQL с 2 строками сразу финальный JSON. Не повторяй тот же SQL.

code всегда строковый литерал. message на русском: блоки двух продуктов + одинаковые/разные свойства.
"""


def create_product_selection_card_kit_agent(model: LiteLlm) -> LlmAgent:
    return _create_leaf_agent(
        model=model,
        name="product_selection_card_kit_agent",
        prompt_file="product_selection_agent_card_kit_prompt.md",
        fallback=_CARD_KIT_FALLBACK,
        tool_filter=CARD_KIT_TOOL_FILTER,
        output_schema=ProductSelectionCardKitResponseSchema,
    )


def create_product_selection_filter_agent(model: LiteLlm) -> LlmAgent:
    return _create_leaf_agent(
        model=model,
        name="product_selection_filter_agent",
        prompt_file="product_selection_agent_filter_prompt.md",
        fallback=_FILTER_FALLBACK,
        tool_filter=FILTER_TOOL_FILTER,
        output_schema=ProductSelectionFilterResponseSchema,
    )


def create_product_selection_compare_agent(model: LiteLlm) -> LlmAgent:
    return _create_leaf_agent(
        model=model,
        name="product_selection_compare_agent",
        prompt_file="product_selection_agent_compare_prompt.md",
        fallback=_COMPARE_FALLBACK,
        tool_filter=COMPARE_TOOL_FILTER,
        output_schema=ProductSelectionCompareResponseSchema,
    )


def create_product_selection_agents(model: LiteLlm) -> ProductSelectionAgents:
    return ProductSelectionAgents(
        card_kit=create_product_selection_card_kit_agent(model),
        filter=create_product_selection_filter_agent(model),
        compare=create_product_selection_compare_agent(model),
    )


def create_product_selection_agent(model: LiteLlm) -> LlmAgent:
    """Обратная совместимость: возвращает filter-агент (полный catalog toolset)."""
    return create_product_selection_filter_agent(model)


def select_product_selection_agent(
    intent: str,
    agents: ProductSelectionAgents | None = None,
    *,
    card_kit: LlmAgent | None = None,
    filter_agent: LlmAgent | None = None,
    compare: LlmAgent | None = None,
) -> LlmAgent:
    """Выбирает leaf-агент product_selection по intent."""
    if agents is not None:
        card_kit = agents.card_kit
        filter_agent = agents.filter
        compare = agents.compare

    if card_kit is None or filter_agent is None or compare is None:
        raise ValueError("product selection agents are not configured")

    normalized = str(intent or "").strip()
    if normalized in {"product_card", "product_kit"}:
        return card_kit
    if normalized == "product_compare":
        return compare
    return filter_agent
