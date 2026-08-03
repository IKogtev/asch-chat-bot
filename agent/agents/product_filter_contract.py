import re
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, field_validator

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


class ProductFilterResponseSchema(BaseModel):
    mode: Literal[
        "product_filter",
        "product_compare",
        "product_attribute_values",
        "needs_clarification",
        "no_data",
    ] = Field(
        description=(
            "Итог обработки: product_filter — список продуктов; product_compare — "
            "сравнение; product_attribute_values — значения свойства; "
            "needs_clarification — требуется выбор; no_data — данные не найдены."
        )
    )
    message: str = Field(
        min_length=1,
        description=(
            "Непустой финальный ответ пользователю на русском языке, составленный "
            "только по данным content-агента. Для product_filter: первая строка "
            "содержит количество, затем каждый продукт идет с новой строки по "
            "возрастанию code."
        ),
    )
    resolved_product: dict[str, str | None] | None = Field(
        default=None,
        description=(
            "Выбранный продукт только с непустыми code и name, когда ответ на "
            "product_compare однозначно выбирает один продукт; иначе null."
        ),
    )
    clarification_options: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Варианты выбора строго с непустыми code и name. Непустой список "
            "обязателен только при needs_clarification."
        ),
    )
    products: list[dict[str, str | None]] = Field(
        default_factory=list,
        description=(
            "Показанные продукты только с полями code, name, term, currency, "
            "folder_kit, is_active; code и name обязательны для каждого элемента."
        ),
    )
    attribute_name: str = Field(
        default="",
        description=(
            "Пользовательское название свойства; непустое только и обязательно "
            "при product_attribute_values."
        ),
    )
    attribute_column: str = Field(
        default="",
        description=(
            "Подтверждённое каталогом имя колонки; непустое только и обязательно "
            "при product_attribute_values."
        ),
    )
    attribute_values: list[str] = Field(
        default_factory=list,
        description=(
            "Непустые уникальные значения свойства; непустой список обязателен "
            "только при product_attribute_values."
        ),
    )

    @field_validator("message")
    @classmethod
    def validate_message(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("message must be non-empty")
        return value

    @field_validator("resolved_product", mode="before")
    @classmethod
    def normalize_absent_resolved_product(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
            return None
        return value

    @field_validator("resolved_product")
    @classmethod
    def validate_resolved_product(
        cls,
        value: dict[str, str | None] | None,
    ) -> dict[str, str] | None:
        if value is None:
            return None
        if set(value) != {"code", "name"}:
            raise ValueError("resolved_product must contain only code and name")
        normalized = {
            key: str(item or "").strip()
            for key, item in value.items()
        }
        if not normalized["code"] or not normalized["name"]:
            raise ValueError("resolved_product code and name must be non-empty")
        return normalized

    @field_validator("clarification_options", "attribute_values", mode="before")
    @classmethod
    def normalize_null_list_fields(cls, value: Any) -> Any:
        return [] if value is None else value

    @field_validator("clarification_options")
    @classmethod
    def validate_clarification_options(
        cls,
        value: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        normalized: list[dict[str, str]] = []
        for option in value:
            if set(option) != {"code", "name"}:
                raise ValueError(
                    "clarification option must contain only code and name"
                )
            code = option["code"].strip()
            name = option["name"].strip()
            if not code or not name:
                raise ValueError(
                    "clarification option code and name must be non-empty"
                )
            normalized.append({"code": code, "name": name})
        return normalized

    @field_validator("products")
    @classmethod
    def validate_products(
        cls,
        value: list[dict[str, str | None]],
    ) -> list[dict[str, str | None]]:
        allowed_fields = {
            "code",
            "name",
            "term",
            "currency",
            "folder_kit",
            "is_active",
        }
        normalized: list[dict[str, str | None]] = []
        for product in value:
            if not set(product).issubset(allowed_fields):
                raise ValueError("product contains unsupported fields")
            item = {
                key: field.strip() if isinstance(field, str) else field
                for key, field in product.items()
            }
            if not item.get("code") or not item.get("name"):
                raise ValueError("product requires non-empty code and name")
            normalized.append(item)
        return normalized

    @field_validator("attribute_name", "attribute_column", mode="before")
    @classmethod
    def normalize_nullable_attribute_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("attribute_values")
    @classmethod
    def normalize_attribute_values(cls, value: list[str]) -> list[str]:
        return list(dict.fromkeys(item.strip() for item in value if item.strip()))


def _product_code_sort_key(product: Dict[str, Any]) -> tuple[tuple[int, Any], ...]:
    parts = re.split(r"(\d+)", str(product.get("code") or "").casefold())
    code_key = tuple(
        (0, int(part)) if part.isdigit() else (1, part)
        for part in parts
        if part
    )
    return (*code_key, (2, str(product.get("name") or "").casefold()))


def _normalize_product_filter_list(
    message: str,
    products: list[Dict[str, Any]],
) -> tuple[str, list[Dict[str, Any]]]:
    """Normalize a product list to one sorted product per line."""
    header_match = re.match(
        r"^\s*Найдено продуктов:\s*(?P<count>\d+)\.\s*",
        message,
    )
    if not header_match:
        raise ValueError("message must start with 'Найдено продуктов: N.'")

    identities = [
        (
            str(product.get("code") or "").strip(),
            str(product.get("name") or "").strip(),
        )
        for product in products
    ]
    if (
        not identities
        or any(not code or not name for code, name in identities)
        or len(set(identities)) != len(identities)
    ):
        raise ValueError("products must contain unique non-empty code and name pairs")

    displayed_count = int(header_match.group("count"))
    if displayed_count != len(products):
        raise ValueError(
            "message product count must match the number of products"
        )

    codes = {code for code, _ in identities}

    code_pattern = "|".join(
        re.escape(code)
        for code in sorted(codes, key=len, reverse=True)
    )
    body = message[header_match.end():]
    markers = list(
        re.finditer(
            rf"(?<!\w)(?P<code>{code_pattern})\s+-\s+",
            body,
        )
    )
    if not markers or body[:markers[0].start()].strip():
        raise ValueError("message must contain only product lines after the header")

    remaining_identities = set(identities)
    lines_by_identity: Dict[tuple[str, str], str] = {}
    for index, marker in enumerate(markers):
        code = marker.group("code")
        end = markers[index + 1].start() if index + 1 < len(markers) else len(body)
        line = " ".join(body[marker.start():end].split())
        candidates = [
            identity
            for identity in remaining_identities
            if identity[0] == code and identity[1] in line
        ]
        if not candidates:
            raise ValueError(
                f"message line for code {code!r} must match an unused product name"
            )

        # Prefer the longest matching name when one product name contains another.
        identity = max(candidates, key=lambda item: len(item[1]))
        lines_by_identity[identity] = line
        remaining_identities.remove(identity)

    if remaining_identities or len(markers) != len(products):
        raise ValueError("message must contain exactly one line for every product")

    sorted_products = sorted(products, key=_product_code_sort_key)
    header = f"Найдено продуктов: {displayed_count}."
    normalized_message = "\n".join(
        [
            header,
            *(
                lines_by_identity[
                    (str(product["code"]), str(product["name"]))
                ]
                for product in sorted_products
            ),
        ]
    )
    return normalized_message, sorted_products


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
    if mode == "product_filter":
        try:
            parsed["message"], parsed["products"] = _normalize_product_filter_list(
                parsed["message"],
                parsed["products"],
            )
        except ValueError as exc:
            raise build_validation_error(
                agent=agent_name,
                stage="presentation",
                problem=str(exc),
                data=data,
                fields=("message", "products"),
            ) from exc
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
