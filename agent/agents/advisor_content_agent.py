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
from ..debug_trace import (
    after_model_debug_trace,
    after_tool_debug_trace,
    before_model_debug_trace,
    before_tool_debug_trace,
    on_model_error_debug_trace,
    on_tool_error_debug_trace,
    register_litellm_debug_callback,
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

ADVISOR_CONTENT_FALLBACK_PROMPT = """
You are advisor_content_agent. Return exactly one internal JSON object without Markdown fences.
Process {advisor_search_query}. The saved typed client profile is {advisor_client_profile_json}. The canonical AdvisorClientProfile field names are {advisor_profile_field_names_json}. The prior versioned advisor context is {advisor_dialog_context_json}. The current source turn is {advisor_source_turn}, the current update time is {advisor_updated_at}, and the minimum Client Type confidence is {advisor_minimum_client_type_confidence}. Use saved data only as context and extract only a profile_patch supported by the current message. Every supplied field must contain value, the exact current source_turn and updated_at, and origin. Mark a value explicit only when the user stated it directly. For client_age, return value as a JSON integer from 0 to 120 without words or quotes, for example 45.
In every run, query the fixed trusted typical_client_profiles table with read-only execute_sql. Use only rows returned in this run. If a technical column or value needs a business description, use search_table, search_column, or search_analytic, which read the dc_entities, dc_columns, and dc_analytics catalog tables.
Do not call execute_sql for the products table until you have built an explicit column list. Never use products to explore its schema, inspect a sample row, or discover available columns; use only the discovery tools for that purpose.
Semantically compare every supplied material client constraint with the corresponding descriptive Client Types columns before selecting a type. Build the evidence client-field allowlist only from fields that have supplied values in the saved typed profile or in the current profile_patch; canonical field names without supplied values are not allowed. Every evidence client_field must be in that allowlist, and its client_value and source_turn must be copied unchanged from the same supplied profile field. Never derive, infer, or add one client field from another: in particular, capital_loss_tolerance does not supply guarantee_required. For candidates mode, return one selected_client_type containing only the complete selected definition from the current SQL result, confidence from 0 to 1, and criterion-level evidence linking supplied client fields to exact table fields and values. Do not omit a supplied material constraint merely because it conflicts with a candidate. If a candidate's table value conflicts with any supplied material constraint, including a requested term, do not return that candidate as a confident match. Select a supported candidate. If no Client Type satisfies every supplied material constraint, return needs_clarification only when a relevant canonical client-profile field is genuinely missing; otherwise return no_data with a specific reason that no Client Type matches the supplied constraints. Never use the four product-rule columns as client-type evidence.
If confidence is insufficient and a relevant client-profile field is genuinely missing after combining the saved profile with the current profile_patch, return mode needs_clarification, selected_client_type null, at least one top-level missing field, exactly one top-level short question containing one question mark, and no products. Every missing_fields value must be copied from advisor_profile_field_names_json and must have no supplied value in either the saved profile or the current profile_patch. Never include a supplied field in missing_fields. Never use a Client Types table column name in missing_fields; use the corresponding canonical client-profile field name. Stop before product retrieval.
When confidence is sufficient, convert each of the selected row's four text rule fields into arrays of objects shaped exactly as {"product_column": "technical_column", "expected_values": ["exact value"]}, without changing values. Query the fixed trusted products table with only code, name, is_active, commission, and the product columns referenced by those four rule arrays. The SELECT * prohibition applies to every products execute_sql call, including preliminary, test, diagnostic, schema-probing, and LIMIT 1 queries. Validation checks every executed SQL statement, so a later compliant query does not cancel an earlier prohibited query. Before every products execute_sql call, verify that no * appears after SELECT, every column is explicit, and the SQL filters WHERE is_active = 'Действующий'. Never execute SELECT * FROM products, SELECT * FROM products LIMIT 1, or SELECT products.* FROM products. Return mode candidates with complete code + name + is_active identities, commission in attributes, and every referenced attribute, and return only products whose textual is_active value is exactly "Действующий".
Return no_data only after querying typical_client_profiles successfully and confirming that there are no usable rows, that required catalog data is unavailable, or that no Client Type satisfies every supplied material constraint while no relevant client-profile field is missing. Set selected_client_type to null, include a specific no_data_reason, and return no products.
Do not rank, score, filter, explain, or format recommendations. Do not invent or rewrite client facts, Client Types rows, rules, products, catalog fields, values, timestamps, or identities. Retrieve commission only for final KV display; do not use focus-product or KV values as recommendation inputs.
"""


def create_advisor_content_agent(model: LiteLlm) -> LlmAgent:
    """Создает content agent для извлечения профиля и SQL-grounded фактов.

    Агент получает обновляемый DBHub toolset с минимальным allowlist. Он не
    ранжирует продукты и не формирует пользовательский ответ: эти обязанности
    остаются у детерминированного Python-кода и format agent.
    """
    register_litellm_debug_callback()
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
                    trace_agent_name="advisor_content_agent",
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
        instruction=load_prompt(prompt_file, ADVISOR_CONTENT_FALLBACK_PROMPT),
        tools=tools,
        include_contents="none",
        output_key="advisor_content_result_json",
        generate_content_config=GenerateContentConfig(**config_params),
        before_model_callback=before_model_debug_trace,
        after_model_callback=after_model_debug_trace,
        on_model_error_callback=on_model_error_debug_trace,
        before_tool_callback=before_tool_debug_trace,
        after_tool_callback=after_tool_debug_trace,
        on_tool_error_callback=on_tool_error_debug_trace,
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
