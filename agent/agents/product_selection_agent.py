from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

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
    "product_filter",
    "product_compare",
    "product_recommendation",
    "product_explanation",
    "product_alternatives",
    "no_data",
}


def _normalize_used_tables(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        item = value.strip()
        return [item] if item else []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return [str(value).strip()] if str(value).strip() else []


def validate_product_selection_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = "product_selection_agent"
    _ = context

    if not isinstance(data, dict):
        raise build_validation_error(
            agent=agent_name,
            stage="payload_type",
            problem=f"expected dict, got {type(data).__name__}",
        )

    status = str(data.get("status", "")).strip()
    mode = str(data.get("mode", "")).strip()
    message = str(data.get("message", "")).strip()
    source = str(data.get("source", "")).strip()
    used_tables = _normalize_used_tables(data.get("used_tables"))

    if status != "ok":
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid status {status!r}, expected 'ok'",
            data=data,
            fields=("status", "mode", "source"),
        )

    if mode not in PRODUCT_SELECTION_MODES:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=f"invalid mode {mode!r}, expected one of {sorted(PRODUCT_SELECTION_MODES)}",
            data=data,
            fields=("status", "mode", "source"),
        )

    if not message:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem="message is required",
            data=data,
            fields=("mode", "message", "source"),
        )

    if source not in {"dbhub", "none"}:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem="source must be 'dbhub' or 'none'",
            data=data,
            fields=("mode", "source"),
        )

    if mode == "no_data" and source != "none":
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='no_data' requires source='none'",
            data=data,
            fields=("mode", "source"),
        )

    if mode != "no_data" and source == "none":
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="product result with data must use source='dbhub'",
            data=data,
            fields=("mode", "source"),
        )

    return {
        "status": status,
        "mode": mode,
        "message": message,
        "source": source,
        "used_tables": used_tables,
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
- {product_selection_intent}: one of product_filter, product_compare, product_recommendation, product_explanation, product_alternatives.

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
- Do not claim current availability unless the table has a status/version field that supports it.
- Do not expose internal fields unless the data explicitly allows using them in client text.
- Do not provide an investment recommendation as a final decision; provide decision support for a manager.
- If data is missing, return mode="no_data", source="none", used_tables=[].
- Write message in Russian.

Response format:
{
  "status": "ok",
  "mode": "product_filter",
  "message": "short answer for the user",
  "source": "dbhub",
  "used_tables": ["products"]
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
