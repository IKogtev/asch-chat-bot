from typing import Any, Dict

from utils.logger import setup_logger
from .product_result_validation import (
    normalize_optional_text,
    normalize_products,
    normalize_text_list,
    normalize_tool_calls,
    parse_product_result,
)
from .validation_utils import build_validation_error


logger = setup_logger("product_filter_contract", "agent.log")

PRODUCT_FILTER_MODES = {
    "product_filter",
    "product_compare",
    "product_attribute_values",
    "needs_clarification",
    "no_data",
}


def validate_product_filter_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = "product_filter_agent"
    parsed = parse_product_result(data, agent_name)
    try:
        parsed.update(
            products=normalize_products(data.get("products")),
            attribute_name=normalize_optional_text(data.get("attribute_name")),
            attribute_column=normalize_optional_text(data.get("attribute_column")),
            attribute_values=normalize_text_list(
                data.get("attribute_values"),
                "attribute_values",
            ),
        )
    except (TypeError, ValueError) as exc:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem=str(exc),
            data=data,
            fields=("products", "attribute_name", "attribute_column", "attribute_values"),
        ) from exc
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
    if mode == "product_filter" and not parsed["products"]:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='product_filter' requires products",
            data=data,
            fields=("mode", "products"),
        )
    if mode in {"product_filter", "product_compare"} and any(
        not product.get("code") or not product.get("name")
        for product in parsed["products"]
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="products require code and name",
            data=data,
            fields=("mode", "products"),
        )
    if mode == "product_compare" and (
        len(parsed["products"]) != 2
        or len({product["code"] for product in parsed["products"]}) != 2
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='product_compare' requires exactly two products with distinct codes",
            data=data,
            fields=("mode", "products"),
        )
    if mode == "product_attribute_values" and (
        not parsed["attribute_name"]
        or not parsed["attribute_column"]
        or not parsed["attribute_values"]
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem=(
                "mode='product_attribute_values' requires attribute_name, "
                "attribute_column, and attribute_values"
            ),
            data=data,
            fields=("mode", "attribute_name", "attribute_column", "attribute_values"),
        )
    if mode == "needs_clarification" and not parsed["clarification_options"]:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='needs_clarification' requires clarification_options",
            data=data,
            fields=("mode", "clarification_options"),
        )
    if mode == "needs_clarification" and any(
        set(option) != {"code", "name"}
        for option in parsed["clarification_options"]
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="clarification options require only code and name",
            data=data,
            fields=("mode", "clarification_options"),
        )
    if parsed["resolved_product"] and (
        not parsed["resolved_product"].get("code")
        or not parsed["resolved_product"].get("name")
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="resolved_product requires code and name",
            data=data,
            fields=("mode", "resolved_product"),
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
            fields=("mode",),
        )

    logger.debug(
        "product_filter validation context: mode=%s products_count=%s tool_calls=%s",
        mode,
        len(parsed["products"]),
        sorted(tool_calls),
    )
    return parsed
