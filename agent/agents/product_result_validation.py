"""Shared structural validation helpers for product-agent JSON responses."""

from typing import Any, Dict

from .validation_utils import build_validation_error


PRODUCT_FIELD_KEYS = ("code", "name", "term", "currency", "folder_kit")
CLARIFICATION_OPTION_FIELD_KEYS = ("code", "name", "term", "currency")
PRODUCT_LIST_FIELD_KEYS = ("code", "name", "term", "currency", "folder_kit", "is_active")


def normalize_optional_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def normalize_product(
    value: Any,
    field_keys: tuple[str, ...] = PRODUCT_FIELD_KEYS,
) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise TypeError(f"expected dict, got {type(value).__name__}")

    normalized = {}
    for key in field_keys:
        item = normalize_optional_text(value.get(key))
        if item:
            normalized[key] = item

    return normalized or None


def normalize_clarification_options(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"expected list, got {type(value).__name__}")

    options: list[dict[str, str]] = []
    for item in value:
        normalized = normalize_product(item, CLARIFICATION_OPTION_FIELD_KEYS)
        if normalized is None:
            raise ValueError("clarification option must not be empty")
        options.append(normalized)

    return options


def normalize_products(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"expected list, got {type(value).__name__}")

    products: list[dict[str, str]] = []
    for item in value:
        normalized = normalize_product(item, PRODUCT_LIST_FIELD_KEYS)
        if normalized is None:
            raise ValueError("product item must not be empty")
        products.append(normalized)

    return products


def normalize_text_list(value: Any, field_name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise TypeError(f"{field_name} expected list, got {type(value).__name__}")
    return [
        normalized
        for item in value
        if (normalized := normalize_optional_text(item))
    ]


def normalize_tool_calls(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        item = value.strip()
        return {item} if item else set()
    if isinstance(value, list):
        return {str(item).strip() for item in value if str(item).strip()}
    return {str(value).strip()} if str(value).strip() else set()


def parse_product_result(data: Dict[str, Any], agent_name: str) -> Dict[str, Any]:
    """Validate and normalize fields shared by both final product contracts."""
    if not isinstance(data, dict):
        raise build_validation_error(
            agent=agent_name,
            stage="payload_type",
            problem=f"expected dict, got {type(data).__name__}",
        )

    mode = normalize_optional_text(data.get("mode"))
    message = normalize_optional_text(data.get("message"))

    try:
        resolved_product = normalize_product(data.get("resolved_product"))
        clarification_options = normalize_clarification_options(
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

    if not message:
        raise build_validation_error(
            agent=agent_name,
            stage="basic_fields",
            problem="message is required",
            data=data,
            fields=("mode", "message"),
        )

    return {
        "mode": mode,
        "message": message,
        "resolved_product": resolved_product,
        "clarification_options": clarification_options,
    }
