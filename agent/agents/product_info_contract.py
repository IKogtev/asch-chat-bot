import json
from typing import Any, Dict, Literal

from pydantic import BaseModel, Field, field_validator

from utils.logger import setup_logger
from .product_result_validation import normalize_tool_calls, parse_product_result
from .validation_utils import build_validation_error


logger = setup_logger("product_info_contract", "agent.log")

PRODUCT_INFO_MODES = {"product_card", "product_kit", "needs_clarification", "no_data"}


class ProductInfoResponseSchema(BaseModel):
    mode: Literal["product_card", "product_kit", "needs_clarification", "no_data"] = Field(
        description=(
            "Итог обработки: product_card — карточка продукта; product_kit — "
            "комплект документов; needs_clarification — требуется выбор продукта; "
            "no_data — подтверждённые данные не найдены."
        )
    )
    message: str = Field(
        min_length=1,
        description=(
            "Непустой финальный ответ пользователю на русском языке, составленный "
            "только по данным content-агента."
        ),
    )
    resolved_product: dict[str, str] | None = Field(
        default=None,
        description=(
            "Подтверждённый продукт только с полями code, name и необязательным "
            "folder_kit. Обязателен для product_card и product_kit; иначе null."
        ),
    )
    clarification_options: list[dict[str, str]] = Field(
        default_factory=list,
        description=(
            "Варианты выбора только с полями code, name и необязательными term, "
            "currency. Непустой список обязателен только при needs_clarification."
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
    def parse_resolved_product(cls, value: Any) -> Any:
        if not isinstance(value, str):
            return value

        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError("resolved_product must be a JSON object") from exc

        if not isinstance(parsed, dict):
            raise ValueError("resolved_product must be a JSON object")
        return parsed

    @field_validator("resolved_product")
    @classmethod
    def validate_resolved_product(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        allowed_fields = {"code", "name", "folder_kit"}
        if not set(value).issubset(allowed_fields):
            raise ValueError("resolved_product contains unsupported fields")
        return {key: item.strip() for key, item in value.items() if item.strip()}

    @field_validator("clarification_options")
    @classmethod
    def validate_clarification_options(
        cls,
        value: list[dict[str, str]],
    ) -> list[dict[str, str]]:
        allowed_fields = {"code", "name", "term", "currency"}
        normalized: list[dict[str, str]] = []
        for option in value:
            if not set(option).issubset(allowed_fields):
                raise ValueError("clarification option contains unsupported fields")
            item = {key: field.strip() for key, field in option.items() if field.strip()}
            if not item.get("code") or not item.get("name"):
                raise ValueError("clarification option requires non-empty code and name")
            normalized.append(item)
        return normalized


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
