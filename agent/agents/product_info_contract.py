from typing import Any, Dict

from utils.logger import setup_logger
from .product_result_validation import normalize_tool_calls, parse_product_result
from .validation_utils import build_validation_error


logger = setup_logger("product_info_contract", "agent.log")

PRODUCT_INFO_MODES = {"product_card", "product_kit", "needs_clarification", "no_data"}


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
    if mode in {"product_card", "product_kit"} and (
        not resolved_product.get("code") or not resolved_product.get("name")
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem=f"mode={mode!r} requires resolved_product.code and resolved_product.name",
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
    if mode == "needs_clarification" and any(
        not option.get("code") or not option.get("name")
        for option in clarification_options
    ):
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="clarification options require code and name",
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
            fields=("mode",),
        )

    logger.debug(
        "product_info validation context: mode=%s resolved_product=%s tool_calls=%s",
        mode,
        resolved_product,
        sorted(tool_calls),
    )
    return parsed
