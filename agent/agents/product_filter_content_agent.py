from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger
from ..config import (
    DBHUB_MCP_TIMEOUT_SEC,
    DBHUB_MCP_TOKEN,
    DBHUB_MCP_URL,
    LLM_PRESENCE_PENALTY,
    PRODUCT_CONTENT_MAX_OUTPUT_TOKENS,
    PRODUCT_FILTER_TEMPERATURE,
)
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset


logger = setup_logger("product_filter_content_agent", "agent.log")

PRODUCT_FILTER_TOOL_FILTER = [
    "search_table",
    "search_column",
    "search_analytic",
    "search_semantic_template",
    "search_objects",
    "execute_sql",
]


def create_product_filter_content_agent(model: LiteLlm) -> LlmAgent:
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
                    tool_filter=PRODUCT_FILTER_TOOL_FILTER,
                )
            )
            logger.info(
                "MCP dbhub connected to product_filter_content_agent: %s",
                DBHUB_MCP_URL,
            )
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
The product and abbreviation substitutions in product_filter_search_query are already applied in code. Do not invent facts, tables, fields, values, product names, or comparison results. For property-only filters such as active or archived status, currency, risk, term, product type, or focus, ignore product_filter_resolution products and product codes.
First call search_semantic_template. For product_filter select the most specific matching product_list template, break equal-specificity ties by highest priority, and fall back to product_list_default. For product_attribute_values use only its matching intent template; do not apply another intent's template to product_compare.
Build product-list SQL only from the selected template's source_table, metric_main, required_filters, default_filters, display_columns, group_by_default, sort_rule, and top_n_default. Confirm the table and every referenced column with search_table and search_column. Confirm every exact non-identifier categorical value with search_analytic. Never invent or substitute catalog values.
Apply predicates in this precedence: resolver identity and status, explicit user constraints, required_filters, default_filters. Apply template grouping and sorting. Limit rows to min(top_n_default, 30), or 30 when the template limit is absent. Execute the smallest read-only SQL query and use only rows returned in this run.
For product filters preserve product identity, template-ordered display_values with business labels from search_column, and the total count from SQL. Exclude code, name, and is_active from display_values because they are structural identity fields. Do not add a default business property when display_columns is empty. The resolver has already completed an active-only pass and, only when it found no active match, an archived-only pass. When it returns products, preserve its selected status and query every product by the complete code + name + is_active tuple; never replace those predicates with code alone or code IN (...). Without resolved products, search is_active = 'Действующий' first and repeat the same query for is_active = 'Архивный' only when the active query returns zero rows. Confirm exact status values with search_analytic and never combine statuses. Treat products as distinct by their complete identity tuple, including comparisons where codes match. If product_filter_resolution.status is partial, do not treat its candidates as a complete result or ignore unmatched_terms. For comparisons return exactly two distinct identity-only products and one shared ordered properties array. Each property is {label, values}, where values contains exactly two SQL values aligned with product identity order. Do not classify properties as common or different. Never return per-product property arrays, raw SQL/tool responses, resolver evidence, catalog metadata, display_columns, or column_business_names. Do not write the final user-facing answer.
"""
    prompt_file = "product_filter_content_agent_prompt.md"
    config_params = {
        "presence_penalty": LLM_PRESENCE_PENALTY,
        "max_output_tokens": PRODUCT_CONTENT_MAX_OUTPUT_TOKENS,
    }
    if PRODUCT_FILTER_TEMPERATURE != -1:
        config_params["temperature"] = PRODUCT_FILTER_TEMPERATURE
    agent = LlmAgent(
        name="product_filter_content_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=tools,
        include_contents="none",
        output_key="product_filter_content_result_json",
        generate_content_config=(
            GenerateContentConfig(**config_params) if config_params else None
        ),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
