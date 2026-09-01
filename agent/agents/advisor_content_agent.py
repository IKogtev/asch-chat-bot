from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from google.genai.types import GenerateContentConfig

from utils.logger import setup_logger

from ..config import (
    ADVISOR_MAX_OUTPUT_TOKENS,
    ADVISOR_TEMPERATURE,
    DBHUB_MCP_TIMEOUT_SEC,
    DBHUB_MCP_TOKEN,
    DBHUB_MCP_URL,
    LLM_PRESENCE_PENALTY,
)
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset


logger = setup_logger("advisor_content_agent", "agent.log")

# Разрешены только discovery-инструменты DBHub и read-only выполнение SQL.
ADVISOR_TOOL_FILTER = [
    "search_table",
    "search_column",
    "search_analytic",
    "search_objects",
    "execute_sql",
]


def create_advisor_content_agent(model: LiteLlm) -> LlmAgent:
    """Создает content agent для извлечения профиля и SQL-grounded фактов.

    Агент получает обновляемый DBHub toolset с минимальным allowlist. Он не
    ранжирует продукты и не формирует пользовательский ответ: эти обязанности
    остаются у детерминированного Python-кода и format agent.
    """
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
                    tool_filter=ADVISOR_TOOL_FILTER,
                )
            )
            logger.info(
                "MCP dbhub connected to advisor_content_agent: %s",
                DBHUB_MCP_URL,
            )
        except Exception as exc:
            logger.error(
                "Failed to connect MCP dbhub for advisor_content_agent: %s",
                exc,
                exc_info=True,
            )
    else:
        logger.warning(
            "DBHUB_MCP_URL is empty; dbhub MCP is not connected to advisor_content_agent"
        )

    fallback = """
You are advisor_content_agent. Return exactly one internal JSON object without Markdown fences.
Process {advisor_search_query}. The saved typed client profile is {advisor_client_profile_json}. The prior versioned advisor context is {advisor_dialog_context_json}. The minimum Client Type confidence is {advisor_minimum_client_type_confidence}. Use saved data only as context and extract only a profile_patch supported by the current message. Every supplied field must contain value, source_turn, updated_at, and origin. Mark a value explicit only when the user stated it directly.
In every run, query the fixed trusted typical_client_profiles table with read-only execute_sql. Use only rows returned in this run.
Semantically compare all supplied client facts with the descriptive Client Types columns. Return a primary type and, only for a genuinely mixed profile, a distinct secondary type. For each type return confidence from 0 to 1 and evidence linking one supplied client field/value/source_turn to one exact table field/value. Never use the four product-rule columns as client-type evidence.
If confidence is insufficient, return mode needs_clarification, at least one missing field, exactly one short question containing one question mark, and no products. Stop before product retrieval.
When confidence is sufficient, deterministically preserve the selected row's already parsed required_properties, preferred_properties, acceptable_compromises, and contraindications. Query the fixed trusted products table and every product column referenced by those four rule arrays. Execute read-only SQL and return mode candidates with complete code + name + is_active identities and every referenced attribute. Only products whose textual is_active value is exactly "Действующий" are eligible for recommendation.
Return no_data only after querying typical_client_profiles successfully and either confirming no usable rows or confirming that required catalog data is unavailable. Include a specific no_data_reason and no products.
Do not rank, score, filter, explain, or format recommendations. Do not invent or rewrite client facts, Client Types rows, rules, products, catalog fields, values, timestamps, or identities. Do not use focus-product or KV values as recommendation inputs.
"""
    prompt_file = "advisor_content_agent_prompt.md"
    config_params = {
        "presence_penalty": LLM_PRESENCE_PENALTY,
        "max_output_tokens": ADVISOR_MAX_OUTPUT_TOKENS,
    }
    if ADVISOR_TEMPERATURE != -1:
        config_params["temperature"] = ADVISOR_TEMPERATURE

    agent = LlmAgent(
        name="advisor_content_agent",
        model=model,
        instruction=load_prompt(prompt_file, fallback),
        tools=tools,
        include_contents="none",
        output_key="advisor_content_result_json",
        generate_content_config=GenerateContentConfig(**config_params),
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
