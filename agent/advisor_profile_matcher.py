from __future__ import annotations

from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from agent.advisor_profile import AdvisorClientProfile
from utils.client_types import (
    CLIENT_TYPE_CODE_COLUMN,
    CLIENT_TYPES_DESCRIPTION_ROW_LABEL,
    CLIENT_TYPES_EXPECTED_COLUMNS,
    CLIENT_TYPES_PROFILE_COLUMN,
    CLIENT_TYPES_RULE_COLUMNS,
    ClientTypeRule,
    parse_client_type_rules,
    validate_client_type_source_columns,
)


# Непустая строка после удаления пробелов по краям. Пример: «Консервативный».
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Колонки таблицы Client Types, которые описывают клиента и могут использоваться LLM
# как доказательства выбора типа. Продуктовые правила в этот набор не входят.
CLIENT_TYPE_MATCHABLE_COLUMNS = frozenset(CLIENT_TYPES_EXPECTED_COLUMNS[1:16])
# Явное соответствие полей рабочего профиля колонкам Client Types.
# Например, `goal` можно сопоставлять только с `client_goal`, а не с `currency`.
PROFILE_TO_CLIENT_TYPE_COLUMNS = {
    "goal": frozenset({"client_goal"}),
    "term_months": frozenset({"term"}),
    "contribution_amount": frozenset(
        {"minimum_initial_contribution", "minimum_contribution"}
    ),
    "contribution_frequency": frozenset({"contribution_frequency"}),
    "currency": frozenset({"currency"}),
    "capital_loss_tolerance": frozenset({"capital_loss_tolerance"}),
    "guarantee_required": frozenset({"guarantee_importance"}),
    "liquidity_need": frozenset({"liquidity_need"}),
    "client_age": frozenset({"age_range"}),
    "insurance_need": frozenset({"insurance_protection_need"}),
    "investment_experience": frozenset({"investment_experience"}),
    "family_context": frozenset({"family_context"}),
    "income_stability": frozenset({"income_stability"}),
    "additional_context": frozenset({"additional_context"}),
}


class AdvisorClientTypeDefinition(BaseModel):
    """Представляет одну проверенную строку из таблицы Client Types."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Стабильный код типа клиента, сформированный загрузчиком. Пример: «CT-001».
    client_type_code: NonEmptyText | None
    # Название типа клиента из таблицы. Пример: «Консервативный».
    profile_name: NonEmptyText
    # Описательные характеристики клиента. Пример: `{"currency": "Рубли"}`.
    attributes: dict[str, Any]
    # Обязательные свойства продукта. Пример: «Статус: Действующий».
    required_properties: tuple[ClientTypeRule, ...]
    # Предпочтительные свойства продукта. Пример: «Ликвидность: Высокая».
    preferred_properties: tuple[ClientTypeRule, ...]
    # Допустимые компромиссы. Пример: «Срок: Среднесрочный».
    acceptable_compromises: tuple[ClientTypeRule, ...]
    # Свойства, при которых продукт противопоказан. Пример: «Уровень риска: Высокий».
    contraindications: tuple[ClientTypeRule, ...]

    @classmethod
    def from_mapping(cls, row: Mapping[str, Any]) -> "AdvisorClientTypeDefinition":
        """Создает тип клиента из строки загруженной таблицы.

        Функция проверяет точную 21-колоночную схему источника, извлекает
        описательные атрибуты и детерминированно разбирает четыре колонки
        продуктовых правил.

        Аргументы:
            row: Строка Client Types с техническими именами колонок и, при
                наличии, сгенерированным `client_type_code`.

        Возвращает:
            Неизменяемое типизированное определение типа клиента.
        """
        source_columns = [
            column
            for column in row
            if column != CLIENT_TYPE_CODE_COLUMN
        ]
        validate_client_type_source_columns(source_columns)
        profile_name = str(row.get(CLIENT_TYPES_PROFILE_COLUMN) or "").strip()
        if not profile_name:
            raise ValueError("Client Types profile_name must not be empty")
        parsed_rules = parse_client_type_rules(row)
        attributes = {
            column: row.get(column)
            for column in CLIENT_TYPES_EXPECTED_COLUMNS
            if column not in CLIENT_TYPES_RULE_COLUMNS
        }
        return cls(
            client_type_code=(
                str(row[CLIENT_TYPE_CODE_COLUMN]).strip()
                if row.get(CLIENT_TYPE_CODE_COLUMN)
                else None
            ),
            profile_name=profile_name,
            attributes=attributes,
            **parsed_rules,
        )


class AdvisorClientTypeEvidence(BaseModel):
    """Связывает один факт о клиенте с одной колонкой выбранного типа."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Поле рабочего профиля. Пример: `goal`.
    client_field: NonEmptyText
    # Значение факта из диалога. Пример: «Сохранение капитала».
    client_value: Any
    # Связанная колонка Client Types. Пример: `client_goal`.
    table_field: NonEmptyText
    # Исходное значение из выбранной строки таблицы. Пример: «Сохранение капитала».
    table_value: Any
    # Идентификатор реплики, из которой получен факт. Пример: `turn-12`.
    source_turn: NonEmptyText


class AdvisorSelectedClientType(BaseModel):
    """Хранит единственный выбранный тип клиента и доказательства выбора."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Полная строка выбранного типа из текущего SQL-ответа.
    definition: AdvisorClientTypeDefinition
    # Уверенность LLM от 0 до 1. Пример: 0.87.
    confidence: float = Field(ge=0.0, le=1.0)
    # Проверяемые связи между фактами клиента и строкой таблицы.
    evidence: tuple[AdvisorClientTypeEvidence, ...] = ()


def _same_evidence_value(actual: Any, claimed: Any) -> bool:
    """Сравнивает фактическое и заявленное значения доказательства.

    Строки сравниваются без учета регистра и крайних пробелов. Значения других
    типов сравниваются обычным точным сравнением Python.
    """
    if isinstance(actual, str) or isinstance(claimed, str):
        return str(actual).strip().casefold() == str(claimed).strip().casefold()
    return actual == claimed


def validate_selected_client_type(
    selected: AdvisorSelectedClientType,
    *,
    client_profile: AdvisorClientProfile,
    minimum_confidence: float,
) -> AdvisorSelectedClientType:
    """Детерминированно проверяет результат выбора типа клиента, сделанный LLM.

    Проверяются диапазон уверенности и связь каждого доказательства с
    заполненным полем профиля и правильной колонкой выбранного типа.

    Аргументы:
        selected: Предложенный LLM единственный тип клиента.
        client_profile: Текущий типизированный профиль клиента с provenance.
        minimum_confidence: Минимальная уверенность для выбора типа.

    Возвращает:
        Исходный результат выбора после успешной проверки.

    Исключения:
        ValueError: Если выбор или доказательства нарушают контракт.
    """
    if not 0.0 <= minimum_confidence <= 1.0:
        raise ValueError("minimum_confidence must be between 0 and 1")

    supplied_profile_fields = client_profile.supplied_fields()
    definition = selected.definition
    if definition.profile_name == CLIENT_TYPES_DESCRIPTION_ROW_LABEL:
        raise ValueError("Client Types description row cannot be selected")
    expected_attribute_fields = set(CLIENT_TYPES_EXPECTED_COLUMNS) - set(
        CLIENT_TYPES_RULE_COLUMNS
    )
    actual_attribute_fields = set(definition.attributes)
    if actual_attribute_fields != expected_attribute_fields:
        raise ValueError(
            "Selected Client Type definition has incomplete attributes: "
            f"missing={sorted(expected_attribute_fields - actual_attribute_fields)}, "
            f"extra={sorted(actual_attribute_fields - expected_attribute_fields)}"
        )
    if not _same_evidence_value(
        definition.attributes[CLIENT_TYPES_PROFILE_COLUMN],
        definition.profile_name,
    ):
        raise ValueError("Selected Client Type definition has inconsistent profile_name")
    if selected.confidence < minimum_confidence:
        raise ValueError("Low-confidence Client Type selection requires clarification")
    if not selected.evidence:
        raise ValueError(f"Client Type {definition.profile_name!r} requires evidence")

    for evidence in selected.evidence:
        profile_field = supplied_profile_fields.get(evidence.client_field)
        if profile_field is None:
            raise ValueError(
                f"Evidence references an unsupplied client field: {evidence.client_field!r}"
            )
        if not _same_evidence_value(profile_field.value, evidence.client_value):
            raise ValueError(
                f"Evidence value does not match client field {evidence.client_field!r}"
            )
        if evidence.source_turn != profile_field.source_turn:
            raise ValueError(
                f"Evidence source turn does not match client field {evidence.client_field!r}"
            )
        if evidence.table_field not in CLIENT_TYPE_MATCHABLE_COLUMNS:
            raise ValueError(
                f"Evidence references a non-client Client Types field: "
                f"{evidence.table_field!r}"
            )
        allowed_table_fields = PROFILE_TO_CLIENT_TYPE_COLUMNS[evidence.client_field]
        if evidence.table_field not in allowed_table_fields:
            raise ValueError(
                f"Client field {evidence.client_field!r} cannot support "
                f"Client Types field {evidence.table_field!r}"
            )
        actual_table_value = definition.attributes.get(evidence.table_field)
        if not _same_evidence_value(actual_table_value, evidence.table_value):
            raise ValueError(
                f"Evidence value does not match Client Types field "
                f"{evidence.table_field!r}"
            )

    return selected
