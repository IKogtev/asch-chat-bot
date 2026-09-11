from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable, Mapping


CLIENT_TYPES_PROFILE_COLUMN = "profile_name"
CLIENT_TYPE_CODE_COLUMN = "client_type_code"
CLIENT_TYPES_DESCRIPTION_ROW_LABEL = "Тип профиля"
CLIENT_TYPES_PROFILE_COLUMNS = (
    "profile_name",
    "client_goal",
    "capital_loss_tolerance",
    "investment_horizon",
    "dependents",
    "expected_return_percent",
    "min_amount",
)
CLIENT_TYPES_RULE_COLUMNS = (
    "required_properties",
    "preferred_properties",
    "acceptable_compromises",
    "contraindications",
)
CLIENT_TYPES_IGNORED_COLUMNS = (
    "notes",
)
CLIENT_TYPES_EXPECTED_COLUMNS = (
    *CLIENT_TYPES_PROFILE_COLUMNS,
    *CLIENT_TYPES_RULE_COLUMNS,
    *CLIENT_TYPES_IGNORED_COLUMNS,
)
CLIENT_TYPES_RULE_PROPERTY_MAP = {
    "статус": "is_active",
    "активный продукт": "is_active",
    "тип продукта": "product_type",
    "срок": "term",
    "срок продукта": "term",
    "риск потери капитала": "capital_loss_risk",
    "уровень риска": "product_risk_level",
    "уровень риска продукта": "product_risk_level",
    "доход": "income",
    "тип взноса": "contribution_type",
    "тип выплат": "payout_type",
    "ликвидность": "liquidity",
    "валюта": "currency",
}


@dataclass(frozen=True)
class ClientTypeRule:
    product_column: str
    expected_values: tuple[str, ...]


def is_meaningful_client_type_value(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, float) and math.isnan(value):
        return False
    return bool(str(value).strip())


def validate_client_type_source_columns(columns: Iterable[Any]) -> None:
    actual = tuple(str(column) for column in columns)
    if actual != CLIENT_TYPES_EXPECTED_COLUMNS:
        raise ValueError(
            "Client Types schema mismatch: "
            f"expected {list(CLIENT_TYPES_EXPECTED_COLUMNS)}, got {list(actual)}"
        )


def parse_client_type_rule_cell(
    value: Any,
    *,
    profile_name: str = "",
    rule_column: str = "",
) -> tuple[ClientTypeRule, ...]:
    if not is_meaningful_client_type_value(value):
        return ()

    parsed: list[ClientTypeRule] = []
    for raw_expression in str(value).split(";"):
        expression = raw_expression.strip()
        if not expression:
            continue
        if ":" not in expression:
            raise ValueError(
                "Invalid Client Types rule expression "
                f"for profile {profile_name!r}, column {rule_column!r}: "
                f"{expression!r}"
            )
        raw_property, raw_value = expression.split(":", 1)
        property_label = " ".join(raw_property.split()).casefold()
        product_column = CLIENT_TYPES_RULE_PROPERTY_MAP.get(property_label)
        if product_column is None:
            raise ValueError(
                "Unknown Client Types product property "
                f"for profile {profile_name!r}, column {rule_column!r}: "
                f"{raw_property.strip()!r}"
            )
        expected_values = tuple(
            part.strip()
            for part in re.split(r"\s+или\s+", raw_value.strip(), flags=re.IGNORECASE)
            if part.strip()
        )
        if not expected_values:
            raise ValueError(
                "Client Types rule value must not be empty "
                f"for profile {profile_name!r}, column {rule_column!r}"
            )
        parsed.append(ClientTypeRule(product_column, expected_values))
    return tuple(parsed)


def parse_client_type_rules(
    row: Mapping[str, Any],
) -> dict[str, tuple[ClientTypeRule, ...]]:
    profile_name = str(row.get(CLIENT_TYPES_PROFILE_COLUMN) or "").strip()
    return {
        column: parse_client_type_rule_cell(
            row.get(column),
            profile_name=profile_name,
            rule_column=column,
        )
        for column in CLIENT_TYPES_RULE_COLUMNS
    }
