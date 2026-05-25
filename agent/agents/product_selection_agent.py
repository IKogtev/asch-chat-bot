from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import McpToolset, StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import DBHUB_MCP_TIMEOUT_SEC, DBHUB_MCP_TOKEN, DBHUB_MCP_URL
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from .validation_utils import build_validation_error

logger = setup_logger("product_selection_agent", "agent.log")

PRODUCT_SELECTION_TOOL_FILTER = [
    "search_table",
    "search_column",
    "search_analytic",
    "search_semantic_template",
    "search_objects",
    "execute_sql",
]

PRODUCT_SELECTION_MODES = {
    "product_card",
    "product_kit",
    "product_filter",
    "product_compare",
    "needs_clarification",
    "no_data",
}

PRODUCT_FIELD_KEYS = ("id", "name", "term", "currency")
PRODUCT_SELECTION_REQUIRED_TOOL = "execute_sql"


def _normalize_used_tables(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def _normalize_product(value: Any) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"expected dict, got {type(value).__name__}")

    normalized = {}
    for key in PRODUCT_FIELD_KEYS:
        item = str(value.get(key, "")).strip()
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
        normalized = _normalize_product(item)
        if normalized is None:
            raise ValueError("clarification option must not be empty")
        options.append(normalized)

    return options


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

    if not isinstance(data, dict):
        raise build_validation_error(
            agent=agent_name,
            stage="payload_type",
            problem=f"expected dict, got {type(data).__name__}",
        )

    status = str(data.get("status", "")).strip()
    mode = str(data.get("mode", "")).strip()
    message = str(data.get("message", "")).strip()
    used_tables = _normalize_used_tables(data.get("used_tables"))

    try:
        resolved_product = _normalize_product(data.get("resolved_product"))
        clarification_options = _normalize_clarification_options(
            data.get("clarification_options")
        )
    except (TypeError, ValueError) as exc:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=str(exc),
            data=data,
            fields=("resolved_product", "clarification_options"),
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

    if mode in {"product_card", "product_kit"} and not resolved_product:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem=f"mode={mode!r} requires resolved_product",
            data=data,
            fields=("mode", "resolved_product"),
        )

    if mode == "product_kit" and not resolved_product.get("id"):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='product_kit' requires resolved_product.id",
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

    if PRODUCT_SELECTION_REQUIRED_TOOL not in tool_calls:
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
    }


def create_product_selection_agent(model: LiteLlm) -> LlmAgent:
    tools = []

    if DBHUB_MCP_URL:
        try:
            headers = {"Authorization": f"Bearer {DBHUB_MCP_TOKEN}"} if DBHUB_MCP_TOKEN else None
            dbhub_toolset = McpToolset(
                connection_params=StreamableHTTPConnectionParams(
                    url=DBHUB_MCP_URL,
                    headers=headers,
                    timeout=DBHUB_MCP_TIMEOUT_SEC,
                ),
                tool_filter=PRODUCT_SELECTION_TOOL_FILTER,
            )
            tools.append(dbhub_toolset)
            logger.info("MCP dbhub connected to product_selection_agent: %s", DBHUB_MCP_URL)
        except Exception as e:
            logger.error(
                "Failed to connect MCP dbhub for product_selection_agent: %s",
                e,
                exc_info=True,
            )
    else:
        logger.warning("DBHUB_MCP_URL is empty; dbhub MCP is not connected to product_selection_agent")

    fallback = """
You are product_selection_agent.
Return only JSON, without markdown fences.

State variables:
- {user_query}: original user question.
- {product_selection_search_query}: normalized product search query.
- {product_selection_intent}: one of product_card, product_kit, product_filter, product_compare.

Mandatory workflow:
1. Call search_semantic_template to understand business terms and answer patterns.
2. Call search_table and choose the relevant product classifier table from the catalog.
3. Call search_column for the selected table.
4. For categorical filters, call search_analytic.
5. If the catalog is not enough, call search_objects to inspect structure.
6. Build the smallest read-only SQL query and run it with execute_sql.
7. Answer only from returned rows and catalog metadata.

Rules:
- Do not invent table names, column names, values, or product facts.
- Do not use SELECT * for final user-facing answers.
- Do not expose internal fields unless the data explicitly allows using them in client text.
- If data is missing, return mode="no_data", used_tables=[].
- If mode="needs_clarification", clarification_options must be a non-empty array of objects.
- Each clarification option must use only id, name, term, and currency fields; do not return options as strings.
- Write message in Russian.
- Do not include source in JSON.

Response format:
{
  "status": "ok",
  "mode": "product_filter",
  "message": "short answer for the user",
  "used_tables": ["products"],
  "resolved_product": null,
  "clarification_options": []
}
"""
    prompt_file = "product_selection_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    agent = LlmAgent(
        name="product_selection_agent",
        model=model,
        instruction=instruction,
        tools=tools,
        output_key="product_selection_result_json",
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
