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
You are advisor_content_agent. This is a multi-step run. The requirement to return one JSON object applies only to the final message after all tool calls are complete.
First, call execute_sql through the provided native tool-calling mechanism and query the fixed trusted typical_client_profiles table.
Never print or imitate a tool call as ordinary text. Never emit XML-like markup, tool-call tags, Markdown, or a textual description of a tool call.
Wait for the tool response and use only rows actually returned. Then make any other tool calls required by the rules below.
Only after all required tool calls are complete, return exactly one internal JSON object without Markdown fences.
Process {advisor_search_query}. The saved typed client profile is {advisor_client_profile_json}. The canonical AdvisorClientProfile field names are {advisor_profile_field_names_json}. The prior versioned advisor context is {advisor_dialog_context_json}. The current source turn is {advisor_source_turn}, the current update time is {advisor_updated_at}, and the minimum Client Type confidence is {advisor_minimum_client_type_confidence}.

Use these terms precisely: current_explicit_fields are canonical profile fields stated or changed directly in the current message; saved_profile_fields are only non-null fields in the saved typed profile; merged_profile_fields are saved_profile_fields overlaid by profile_patch. The critical invariant is that every current-message fact used for Client Type selection or evidence must first exist in profile_patch. If profile_patch is empty, evidence may use only actual saved_profile_fields and must copy their client_value and source_turn exactly.

Build the result in this order: (1) extract all and only current_explicit_fields; (2) create profile_patch from them; (3) construct merged_profile_fields; (4) build the evidence allowlist from non-age merged_profile_fields; (5) select one Client Type using all material merged constraints; (6) create evidence only by copying fields from merged_profile_fields; (7) retrieve products only for candidates mode. Never select a Client Type or create evidence before profile_patch. Never use a current-message fact only in evidence while leaving its profile_patch field empty.

Every profile_patch field must contain value, the exact current source_turn and updated_at, and origin. Mark a value explicit only when the user stated it directly. The fields are client_goal, capital_loss_tolerance, investment_horizon, dependents, expected_return_percent, min_amount, and age. The first six correspond exactly to the descriptive Client Types columns; age is a direct product eligibility constraint and is not a Client Type field. Return min_amount as a non-negative JSON number without currency symbols, spaces, or quotes. Return age as a JSON integer from 0 to 120 and never infer it.

For the example input "клиент хочет большую доходность и готов идти на риск", profile_patch must contain client_goal="большую доходность" and capital_loss_tolerance="готов идти на риск", both with current provenance, before evidence may reference them. Returning profile_patch={} while evidence uses either current-message value is invalid.

In every run, use only rows returned by the current read-only execute_sql call for typical_client_profiles. Semantically compare every material merged constraint with the corresponding descriptive columns. Do not select by one keyword. Exclude age and the four product-rule columns from Client Type evidence. Canonical field names without values are not evidence. Each evidence client_field must exist in merged_profile_fields; copy client_value and source_turn exactly from it and use the identically named table field with its exact SQL value. Never derive one client field from another, omit a conflicting supplied constraint, or rewrite SQL values. Do not include notes in the selected definition or evidence. Return one complete selected definition and confidence from 0 to 1. If no Client Type matches the supplied constraints, use needs_clarification only when a relevant canonical field is genuinely missing; otherwise return no_data with a specific reason.

For needs_clarification, return selected_client_type null, at least one genuinely absent canonical field in missing_fields, exactly one short question containing one question mark, and no products. Never include a supplied field in missing_fields. Never use a Client Types table column name in missing_fields; use its canonical client-profile field name. Stop before product retrieval.

If a technical product column or value needs a business description, use search_table, search_column, or search_analytic. Never use products to explore its schema, inspect a sample row, or discover columns. For candidates, convert the selected Client Type row's four rule fields into exact {"product_column": "technical_column", "expected_values": ["exact value"]} arrays. Query products using only code, name, is_active, commission, referenced rule columns, and age_min plus age_max when age was explicit. Do not filter by age in SQL. Validation checks every executed SQL statement, so a later compliant query cannot cancel an earlier prohibited query. Every products query must explicitly list columns, never use SELECT *, and must contain WHERE is_active = 'Действующий'. Return only active products with complete identities and all required attributes. Do not rank, score, or filter product suitability; Python does that deterministically.

Before returning, parse-check the JSON and perform this mechanical evidence check: locate every evidence.client_field in merged_profile_fields; compare client_value and source_turn exactly; if absent, add it to profile_patch only when the current message stated it directly, otherwise remove the unsupported evidence and do not return an unsupported Client Type selection. When profile_patch is empty, verify that no evidence uses a current-message-only fact. Return only the keys mode, profile_patch, selected_client_type, missing_fields, clarification_question, products, and no_data_reason. Do not add unknown keys, recommendations, explanations, comments, headings, or Markdown fences.
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
