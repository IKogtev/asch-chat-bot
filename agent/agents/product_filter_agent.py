from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, field_validator
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import DBHUB_MCP_TIMEOUT_SEC, DBHUB_MCP_TOKEN, DBHUB_MCP_URL, PRODUCT_FILTER_TEMPERATURE
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset
from .product_result_validation import normalize_tool_calls, parse_product_result
from .validation_utils import build_validation_error


logger = setup_logger("product_filter_agent", "agent.log")

PRODUCT_FILTER_TOOL_FILTER = [
    "search_table",
    "search_column",
    "search_analytic",
    "search_semantic_template",
    "search_objects",
    "execute_sql",
]
PRODUCT_FILTER_MODES = {
    "product_filter",
    "product_compare",
    "product_attribute_values",
    "needs_clarification",
    "no_data",
}


class ProductFilterResponseSchema(BaseModel):
    mode: Literal[
        "product_filter",
        "product_compare",
        "product_attribute_values",
        "needs_clarification",
        "no_data",
    ] = Field(
        description=(
            "Режим: product_filter, product_compare, product_attribute_values, needs_clarification или no_data."
        )
    )
    message: str = Field(
        description="Краткий ответ пользователю на русском языке."
    )
    used_tables: list[str] = Field(
        default_factory=list,
        description="Таблицы, использованные в SQL текущего запуска.",
    )
    resolved_product: dict[str, str | None] | None = Field(
        default=None,
        description="Подтверждённые code и name конкретного продукта, если нужны ответу.",
    )
    clarification_options: list[dict[str, str]] = Field(
        default_factory=list,
        description="Варианты только с code и name; обязательны при needs_clarification.",
    )
    products: list[dict[str, str | None]] = Field(
        default_factory=list,
        description="Строки итогового списка: code, name, term, currency, folder_kit и is_active.",
    )
    attribute_name: str | None = Field(
        default="",
        description="Понятное пользователю название свойства.",
    )
    attribute_column: str | None = Field(
        default="",
        description="Подтверждённое каталогом техническое имя колонки свойства.",
    )
    attribute_values: list[str] = Field(
        default_factory=list,
        description="Значения свойства; непустой список обязателен при product_attribute_values.",
    )

    @field_validator("resolved_product", mode="before")
    @classmethod
    def normalize_absent_resolved_product(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
            return None
        return value

    @field_validator("clarification_options", "attribute_values", mode="before")
    @classmethod
    def normalize_null_list_fields(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("clarification_options")
    @classmethod
    def validate_clarification_options(
        cls,
        value: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for option in value:
            if set(option) != {"code", "name"}:
                raise ValueError(
                    "clarification option must contain only code and name"
                )
            code = option["code"].strip()
            name = option["name"].strip()
            if not code or not name:
                raise ValueError(
                    "clarification option code and name must be non-empty"
                )
            normalized.append({"code": code, "name": name})
        return normalized


def validate_product_filter_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = "product_filter_agent"
    parsed = parse_product_result(data, agent_name)
    mode = parsed["mode"]
    tool_calls = normalize_tool_calls((context or {}).get("_adk_tool_calls"))

    if mode not in PRODUCT_FILTER_MODES:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid mode {mode!r}, expected one of {sorted(PRODUCT_FILTER_MODES)}",
            data=data,
            fields=("mode",),
        )
    if mode == "product_attribute_values" and not parsed["attribute_values"]:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='product_attribute_values' requires attribute_values",
            data=data,
            fields=("mode", "attribute_values"),
        )
    if mode == "needs_clarification" and not parsed["clarification_options"]:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='needs_clarification' requires clarification_options",
            data=data,
            fields=("mode", "clarification_options"),
        )
    if (
        mode != "no_data"
        and not (mode == "needs_clarification" and parsed["clarification_options"])
        and "execute_sql" not in tool_calls
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="tool_usage",
            problem="required tool 'execute_sql' was not called",
            data=data,
            fields=("mode", "used_tables"),
        )

    logger.debug(
        "product_filter validation context: mode=%s products_count=%s tool_calls=%s",
        mode,
        len(parsed["products"]),
        sorted(tool_calls),
    )
    return parsed


def create_product_filter_content_agent(model: LiteLlm) -> LlmAgent:
    tools = []
    if DBHUB_MCP_URL:
        try:
            headers = {"Authorization": f"Bearer {DBHUB_MCP_TOKEN}"} if DBHUB_MCP_TOKEN else None
            tools.append(
                RefreshingMcpToolset(
                    connection_params=StreamableHTTPConnectionParams(
                        url=DBHUB_MCP_URL,
                        headers=headers,
                        timeout=DBHUB_MCP_TIMEOUT_SEC,
                    ),
                    tool_filter=PRODUCT_FILTER_TOOL_FILTER,
                )
            )
            logger.info("MCP dbhub connected to product_filter_content_agent: %s", DBHUB_MCP_URL)
        except Exception as exc:
            logger.error(
                "Failed to connect MCP dbhub for product_filter_content_agent: %s",
                exc,
                exc_info=True,
            )
    else:
        logger.warning(
            "DBHUB_MCP_URL is empty; dbhub MCP is not connected to product_filter_content_agent"
        )

    fallback = """
You are product_filter_content_agent. Return one internal JSON object only, without markdown fences.
Use state variables {user_query}, {product_filter_search_query}, {product_filter_intent}, {from_glossary}, {product_resolutions}, and {product_filter_resolution}.
The product and abbreviation substitutions in product_filter_search_query are already applied in code. Do not invent facts, tables, fields, values, product names, or comparison results.
First call search_semantic_template, inspect the data catalog, call search_analytic before every exact categorical filter, then execute the smallest read-only SQL query. Use only rows returned in this run.
For product filters preserve is_active and the total count from SQL. Unless the user explicitly requests archived products or all statuses, filter by is_active = 'Действующий'; confirm the exact categorical value with search_analytic first. If product_filter_resolution.status is partial, do not treat its product_codes as a complete result or ignore unmatched_terms. Preserve exact rows, used tables, resolver evidence, attribute metadata, and comparison columns for the format agent. Do not write the final user-facing answer.
"""
    prompt_file = "product_filter_content_agent_prompt.md"
    config_params = {}
    if PRODUCT_FILTER_TEMPERATURE != -1:
        config_params["temperature"] = PRODUCT_FILTER_TEMPERATURE
    agent = LlmAgent(
        name="product_filter_content_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=tools,
        output_key="product_filter_content_result_json",
        generate_content_config=GenerateContentConfig(**config_params) if config_params else None,
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent


def create_product_filter_format_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
You are product_filter_format_agent. Transform {product_filter_content_result_json} into one final JSON object matching the response schema.
Use {product_filter_intent} and {product_filter_format_correction}. Do not call tools, perform SQL or product selection, calculate new values, or add facts not present in the supplied content result.
Write the final user-facing message in Russian and return JSON only.
"""
    prompt_file = "product_filter_format_agent_prompt.md"
    # Formatting is intentionally tool-free and deterministic.
    agent = LlmAgent(
        name="product_filter_format_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=[],
        output_key="product_filter_result_json",
        output_schema=ProductFilterResponseSchema,
        generate_content_config=GenerateContentConfig(temperature=0.0),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
