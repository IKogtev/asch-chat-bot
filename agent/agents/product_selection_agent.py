from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from google.genai.types import GenerateContentConfig
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from utils.logger import setup_logger
from ..config import DBHUB_MCP_TIMEOUT_SEC, DBHUB_MCP_TOKEN, DBHUB_MCP_URL, PRODUCT_SELECTION_TEMPERATURE
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from ..tools.refreshing_mcp_toolset import RefreshingMcpToolset
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
    "product_attribute_values",
    "needs_clarification",
    "no_data",
}

PRODUCT_FIELD_KEYS = ("code", "name", "term", "currency", "folder_kit")
CLARIFICATION_OPTION_FIELD_KEYS = ("code", "name", "term", "currency")
PRODUCT_LIST_FIELD_KEYS = ("code", "name", "term", "currency", "folder_kit", "is_active")
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


def _normalize_product(
    value: Any,
    field_keys: tuple[str, ...] = PRODUCT_FIELD_KEYS,
) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"expected dict, got {type(value).__name__}")

    normalized = {}
    for key in field_keys:
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

    # Разрешаем не вызывать execute_sql, если данные уже резолвлены кодом (через product_resolver)
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


def create_product_selection_agent(model: LiteLlm) -> LlmAgent:
    tools = []

    if DBHUB_MCP_URL:
        try:
            headers = {"Authorization": f"Bearer {DBHUB_MCP_TOKEN}"} if DBHUB_MCP_TOKEN else None
            dbhub_toolset = RefreshingMcpToolset(
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
Use state variable {from_glossary} as a dictionary of terms already found by code.
Do not invent additional expansions.
For product_selection_search_query, product and abbreviation substitutions are already applied in code.
Use {from_glossary} by category:
- product and abbreviation: do not rewrite product_selection_search_query again;
- term: use definition from {from_glossary} for filters and answer wording.
For product name search in `name`, use the canonical product name from product_selection_search_query, not Cyrillic abbreviations or synonyms from user_query (e.g. use Fort Knox, not FK or Fort Noks in Cyrillic).
Use pg_trgm word similarity for product name search: filter with the indexed operator `'Fort Knox' <% name` and order by `word_similarity('Fort Knox', name) DESC`; do not use name ILIKE, name LIKE, or name = for product name search. The operand order is mandatory: the short canonical product text must be on the left and the `name` column must be on the right; never write `name <% 'Fort Knox'`.
In word_similarity include only the product name token; put service words (list, archive, products) into other filters such as is_active.
Example: user_query=products FK, product_selection_search_query=list products Fort Knox -> `'Fort Knox' <% name ORDER BY word_similarity('Fort Knox', name) DESC`, not FK and not the full service phrase.
If execute_sql returns 0 rows for product name search, retry only after checking that the search phrase contains the canonical product name without service words.
If multiple definitions are present and the product context does not disambiguate them, return mode="no_data" instead of guessing.

You are product_selection_agent.
Return only JSON, without markdown fences.

State variables: `user_query`, `product_selection_search_query`, `product_selection_intent`, `from_glossary`, `product_resolution`, `product_resolutions`, `product_filter_resolution`.

Current values:
- `user_query`: {user_query}
- `product_selection_search_query`: {product_selection_search_query}
- `product_selection_intent`: {product_selection_intent}
- `from_glossary`: {from_glossary}
- `product_resolution`: {product_resolution}
- `product_resolutions`: {product_resolutions}

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
- For product_filter, always include is_active in SQL when showing a product list; for rows with is_active="Архивный", format list lines as `CODE - **Архивный**. NAME (...)`; do not mark active products; end message with a clarification question about showing product parameters or sending the document kit, and fill products with shown rows.
- For product_attribute_values, show only user-facing values as a list, do not show technical table or column names, end message with the exact question: "Могу показать продукты с этими свойствами. Какое свойство вас интересует ?", fill attribute_name and attribute_values, and fill attribute_column only for internal follow-up routing when the catalog confirmed it.
- If mode="needs_clarification", clarification_options must be a non-empty array of objects.
- Each clarification option must use only code, name, term, and currency fields; do not return options as strings.
- For product_card and product_kit, use product_resolution prepared by code; do not resolve product names yourself.
- For product_compare, use product_resolutions prepared by code; do not resolve product names yourself.
- For product_card and product_compare, include every confirmed user-facing field in SELECT and render it in this exact order: code, name, is_active, insurance_type, product_type, term, capital_loss_risk, product_risk_level, income, commission, commission_condition, input_date, contribution_type, payout_type, liquidity, currency, fx_protection, segment, client_goal, taxes, tax_benefits, in_focus, in_focus_condition.
- Render differences only in product-first order: first the first product heading and all its different fields, then the second product heading and all its different fields. Never group differences by property, number properties, or put product names inside a property description.
- Compare all fields in the specified order except code and name. Show equal non-empty values once under `Одинаковые свойства`; show different non-empty values under both products; for one empty value show `нет данных` on that side; omit a field only when both values are empty.
- Required positive format example; the values are synthetic and must not be copied as answer data:
  `У двух продуктов отличаются следующие свойства:\n\n1001 - Продукт А\n• Доход: Отсутствует\n\n1002 - Продукт Б\n• Доход: Гарантированный\n\nОдинаковые свойства:\n• Тип страхования: НСЖ\n• КВ: 12,5%`
- Forbidden negative format example:
  `1. Доход:\n   Продукт А - Отсутствует\n   Продукт Б - Гарантированный`
- Apply this single canonical `commission` rule everywhere `КВ` is rendered. Before comparing or rendering, normalize each non-empty value: it is already expressed as a percentage, so do not multiply or divide by 100; accept a number or string; for a string trim surrounding whitespace and one trailing `%`; round to at most two decimal places; remove insignificant zeroes; replace the decimal point with a comma; append exactly one `%`. Examples: `2.35` -> `2,35%`; `"2.35"` -> `2,35%`; `"2.35%"` -> `2,35%`; `7.0` -> `7%`; `"2,35%"` -> `2,35%`. For product_compare, compare only normalized values. `commission != null` makes the `КВ` line mandatory: equal values go under `Одинаковые свойства`; different values go under both products. A decimal point between digits in a final `КВ` value is forbidden: `КВ: 2,35%` is correct; `КВ: 2.35%` is forbidden.
- A product_compare final JSON is allowed only when the number of distinct listed fields that are non-empty for at least one SQL row, excluding code and name, equals the number of distinct user-facing property names shown in message. Do not add fields to the JSON contract for this check.
- For product_compare, return resolved_product=null and products=[].
- For product_kit, include resolved_product.folder_kit when the SQL result has a folder_kit column.
- Write message in Russian.
- Do not include source in JSON.

Response format:
{
  "status": "ok",
  "mode": "product_filter",
  "message": "answer for the user",
  "used_tables": ["products"],
  "resolved_product": null,
  "clarification_options": [],
  "products": [],
  "attribute_name": "",
  "attribute_column": "",
  "attribute_values": []
}
"""
    prompt_file = "product_selection_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    name = "product_selection_agent"
    if PRODUCT_SELECTION_TEMPERATURE != -1:
        logger.debug(f"Agent {name} it's temperature: {PRODUCT_SELECTION_TEMPERATURE}")
        agent = LlmAgent(
            name=name,
            model=model,
            instruction=instruction,
            tools=tools,
            output_key="product_selection_result_json",
            generate_content_config=GenerateContentConfig(
                temperature=PRODUCT_SELECTION_TEMPERATURE,
            )
        )
    else:
        logger.debug(f"Agent {name} temperature set to -1 so google adk decide himself")
        agent = LlmAgent(
            name=name,
            model=model,
            instruction=instruction,
            tools=tools,
            output_key="product_selection_result_json"
        )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
