from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger
from ..config import (
    DBHUB_MCP_TIMEOUT_SEC,
    DBHUB_MCP_TOKEN,
    DBHUB_MCP_URL,
    PRODUCT_INFO_TEMPERATURE,
)
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset


logger = setup_logger("product_info_content_agent", "agent.log")

PRODUCT_INFO_TOOL_FILTER = [
    "search_table",
    "search_column",
    "search_analytic",
    "search_semantic_template",
    "search_objects",
    "execute_sql",
]


def create_product_info_content_agent(model: LiteLlm) -> LlmAgent:
    tools = []
    if DBHUB_MCP_URL:
        try:
            headers = (
                {"Authorization": f"Bearer {DBHUB_MCP_TOKEN}"}
                if DBHUB_MCP_TOKEN
                else None
            )
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
            logger.info(
                "MCP dbhub connected to product_info_content_agent: %s",
                DBHUB_MCP_URL,
            )
        except Exception as exc:
            logger.error(
                "Failed to connect MCP dbhub for product_info_content_agent: %s",
                exc,
                exc_info=True,
            )
    else:
        logger.warning(
            "DBHUB_MCP_URL is empty; dbhub MCP is not connected to product_info_content_agent"
        )

    fallback = """
You are product_info_content_agent. Return one internal JSON object only, without markdown fences.
Use state variables {user_query}, {product_info_search_query}, {product_info_intent}, {from_glossary}, and {product_resolution}.
The product and abbreviation substitutions in product_info_search_query are already applied in code. Do not invent facts, tables, fields, values, or product names.
First call search_semantic_template, then inspect the catalog and execute the smallest read-only SQL query. Use only rows returned in this run for a card or product details.
For product_card and product_kit use product_resolution only to identify the product code; it is not a source of card facts. Preserve the exact SQL rows, used tables, resolved product, and clarification evidence for the format agent. Do not write the final user-facing answer.
"""
    prompt_file = "product_info_content_agent_prompt.md"
    config_params = {}
    if PRODUCT_INFO_TEMPERATURE != -1:
        config_params["temperature"] = PRODUCT_INFO_TEMPERATURE
    agent = LlmAgent(
        name="product_info_content_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=tools,
        output_key="product_info_content_result_json",
        generate_content_config=(
            GenerateContentConfig(**config_params) if config_params else None
        ),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
