from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, field_validator

from utils.logger import setup_logger
from .product_result_validation import normalize_tool_calls, parse_product_result
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
            "Режим: product_filter, product_compare, product_attribute_values, needs_clarification или no_data."
        )
    )
    message: str = Field(
        description="Краткий ответ пользователю на русском языке."
    )
    used_tables: list[str] = Field(
        default_factory=list,
        description="Таблицы, использованные в SQL текущего запуска.",
    )
    resolved_product: dict[str, str | None] | None = Field(
        default=None,
        description="Подтверждённые code и name конкретного продукта, если нужны ответу.",
    )
    clarification_options: list[dict[str, str]] = Field(
        default_factory=list,
        description="Варианты только с code и name; обязательны при needs_clarification.",
    )
    products: list[dict[str, str | None]] = Field(
        default_factory=list,
        description="Строки итогового списка: code, name, term, currency, folder_kit и is_active.",
    )
    attribute_name: str | None = Field(
        default="",
        description="Понятное пользователю название свойства.",
    )
    attribute_column: str | None = Field(
        default="",
        description="Подтверждённое каталогом техническое имя колонки свойства.",
    )
    attribute_values: list[str] = Field(
        default_factory=list,
        description="Значения свойства; непустой список обязателен при product_attribute_values.",
    )

    @field_validator("resolved_product", mode="before")
    @classmethod
    def normalize_absent_resolved_product(cls, value: Any) -> Any:
        if isinstance(value, str) and value.strip().lower() in {"none", "null"}:
            return None
        return value

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


def validate_product_filter_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    agent_name = "product_filter_agent"
    parsed = parse_product_result(data, agent_name)
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
    if mode == "product_attribute_values" and not parsed["attribute_values"]:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='product_attribute_values' requires attribute_values",
            data=data,
            fields=("mode", "attribute_values"),
        )
    if mode == "needs_clarification" and not parsed["clarification_options"]:
        raise build_validation_error(
            agent=agent_name,
            stage="semantics",
            problem="mode='needs_clarification' requires clarification_options",
            data=data,
            fields=("mode", "clarification_options"),
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
            fields=("mode", "used_tables"),
        )

    logger.debug(
        "product_filter validation context: mode=%s products_count=%s tool_calls=%s",
        mode,
        len(parsed["products"]),
        sorted(tool_calls),
    )
    return parsed
