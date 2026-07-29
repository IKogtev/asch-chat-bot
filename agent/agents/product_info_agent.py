import json
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, field_validator
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import DBHUB_MCP_TIMEOUT_SEC, DBHUB_MCP_TOKEN, DBHUB_MCP_URL, PRODUCT_INFO_TEMPERATURE
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset
from .product_result_validation import normalize_tool_calls, parse_product_result
from .validation_utils import build_validation_error


logger = setup_logger("product_info_agent", "agent.log")

PRODUCT_INFO_TOOL_FILTER = [
    "search_table",
    "search_column",
    "search_analytic",
    "search_semantic_template",
    "search_objects",
    "execute_sql",
]
PRODUCT_INFO_MODES = {"product_card", "product_kit", "needs_clarification", "no_data"}


class ProductInfoResponseSchema(BaseModel):
    mode: Literal["product_card", "product_kit", "needs_clarification", "no_data"] = Field(
        description="Режим: product_card, product_kit, needs_clarification или no_data."
    )
    message: str = Field(
        description="Краткий ответ пользователю на русском языке."
    )
    used_tables: list[str] = Field(
        default_factory=list,
        description="Таблицы, использованные в SQL текущего запуска.",
    )
    resolved_product: dict[str, str] | None = Field(
        default=None,
        description="Подтверждённые code, name и при наличии folder_kit; обязателен для карточки и комплекта.",
    )
    clarification_options: list[dict[str, str]] = Field(
        default_factory=list,
        description="Варианты с code, name, term и currency; обязателен при needs_clarification.",
    )
    products: list[dict[str, str]] = Field(
        default_factory=list,
        description="Не используется для карточки и комплекта: пустой список.",
    )
    attribute_name: str = Field(
        default="",
        description="Не используется для карточки и комплекта: пустая строка.",
    )
    attribute_column: str = Field(
        default="",
        description="Не используется для карточки и комплекта: пустая строка.",
    )
    attribute_values: list[str] = Field(
        default_factory=list,
        description="Не используется для карточки и комплекта: пустой список.",
    )

    @field_validator("resolved_product", mode="before")
    @classmethod
    def parse_resolved_product(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("resolved_product must be a JSON object") from exc

        if not isinstance(parsed, dict):
            raise ValueError("resolved_product must be a JSON object")
        return parsed


def validate_product_info_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = "product_info_agent"
    parsed = parse_product_result(data, agent_name)
    mode = parsed["mode"]
    tool_calls = normalize_tool_calls((context or {}).get("_adk_tool_calls"))

    if mode not in PRODUCT_INFO_MODES:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid mode {mode!r}, expected one of {sorted(PRODUCT_INFO_MODES)}",
            data=data,
            fields=("mode",),
        )

    resolved_product = parsed["resolved_product"]
    clarification_options = parsed["clarification_options"]
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
    if (
        mode != "no_data"
        and not is_kit_resolved
        and not (mode == "needs_clarification" and clarification_options)
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
        "product_info validation context: mode=%s resolved_product=%s tool_calls=%s",
        mode,
        resolved_product,
        sorted(tool_calls),
    )
    return parsed


def create_product_info_agent(model: LiteLlm) -> LlmAgent:
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
                    tool_filter=PRODUCT_INFO_TOOL_FILTER,
                )
            )
            logger.info("MCP dbhub connected to product_info_agent: %s", DBHUB_MCP_URL)
        except Exception as exc:
            logger.error("Failed to connect MCP dbhub for product_info_agent: %s", exc, exc_info=True)
    else:
        logger.warning("DBHUB_MCP_URL is empty; dbhub MCP is not connected to product_info_agent")

    fallback = """
You are product_info_agent. Return one JSON object only, without markdown fences.
Use state variables {user_query}, {product_info_search_query}, {product_info_intent}, {from_glossary}, and {product_resolution}.
The product and abbreviation substitutions in product_info_search_query are already applied in code. Do not invent facts, tables, fields, values, or product names.
First call search_semantic_template, then inspect the catalog and execute the smallest read-only SQL query. Use only rows returned in this run for a card or product details.
For product_card and product_kit use product_resolution only to identify the product code; it is not a source of card facts. If resolution is ambiguous, return needs_clarification with options. For product_kit include code and folder_kit when available. Write the user message in Russian.
"""
    prompt_file = "product_info_agent_prompt.md"
    config_params = {}
    if PRODUCT_INFO_TEMPERATURE != -1:
        config_params["temperature"] = PRODUCT_INFO_TEMPERATURE
    agent = LlmAgent(
        name="product_info_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=tools,
        output_key="product_info_result_json",
        output_schema=ProductInfoResponseSchema,
        generate_content_config=GenerateContentConfig(**config_params) if config_params else None,
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
