from __future__ import annotations

import json
import re
from typing import Any, Literal, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from agent.advisor_profile import AdvisorClientProfile, merge_advisor_profile
from agent.advisor_profile_matcher import (
    AdvisorClientTypeDefinition,
    AdvisorClientTypeSelection,
    validate_client_type_selection,
)
from agent.advisor_ranking_service import (
    ACTIVE_PRODUCT_STATUS,
    AdvisorProductFacts,
    AdvisorRankingResult,
)
from utils.client_types import CLIENT_TYPES_DESCRIPTION_ROW_LABEL

from .validation_utils import build_validation_error


# Непустая строка после удаления пробелов по краям. Пример: «products».
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Фиксированные доверенные таблицы, которые должны быть запрошены в текущем запуске.
CLIENT_TYPES_TABLE = "typical_client_profiles"
PRODUCTS_TABLE = "products"
# Тип Pydantic-модели, читаемой из validation context.
ModelT = TypeVar("ModelT", bound=BaseModel)


class AdvisorCandidateProduct(BaseModel):
    """Хранит проверенные факты продукта до детерминированного ранжирования."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Код продукта из результата SQL. Пример: «2832».
    code: NonEmptyText
    # Наименование продукта из результата SQL.
    name: NonEmptyText
    # Статус продукта из результата SQL. Пример: «Действующий».
    is_active: NonEmptyText
    # Свойства, запрошенные для правил выбранного типа клиента.
    attributes: dict[str, Any] = Field(default_factory=dict)
    # Явное семейство продукта для будущего diversity-шага ранжирования.
    family: str | None = None
    # Стабильный приоритет для разрешения равенства баллов.
    tie_break_priority: int = 100

    def to_product_facts(self) -> AdvisorProductFacts:
        """Преобразует кандидата в модель детерминированного ранжирования."""
        return AdvisorProductFacts(
            code=self.code,
            name=self.name,
            is_active=self.is_active,
            attributes=self.attributes,
            family=self.family,
            tie_break_priority=self.tie_break_priority,
        )


class AdvisorContentResult(BaseModel):
    """Задает строгий внутренний ответ advisor content agent."""

    model_config = ConfigDict(extra="forbid")

    # Режим обработки: уточнение, кандидаты или подтвержденное отсутствие данных.
    mode: Literal["needs_clarification", "candidates", "no_data"]
    # Только поля профиля, извлеченные из текущего сообщения.
    profile_patch: AdvisorClientProfile = Field(default_factory=AdvisorClientProfile)
    # Проверенные строки Client Types, использованные для выбора.
    client_types: tuple[AdvisorClientTypeDefinition, ...]
    # Выбор основного и необязательного дополнительного типа клиента.
    client_type_selection: AdvisorClientTypeSelection | None = None
    # Недостающие поля; должны совпадать с `client_type_selection`.
    missing_fields: list[str] = Field(default_factory=list)
    # Единственный вопрос для режима уточнения.
    clarification_question: str | None = None
    # SQL-grounded факты продуктов до детерминированного ранжирования.
    products: tuple[AdvisorCandidateProduct, ...] = ()
    # Причина безопасного ответа без данных.
    no_data_reason: str | None = None


class AdvisorFinalProduct(BaseModel):
    """Представляет неизменяемую идентичность продукта в финальном ответе."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Код рекомендованного продукта.
    code: NonEmptyText
    # Наименование рекомендованного продукта.
    name: NonEmptyText
    # Статус рекомендованного продукта.
    is_active: NonEmptyText


class AdvisorFinalResult(BaseModel):
    """Задает строгий пользовательский ответ advisor format agent."""

    model_config = ConfigDict(extra="forbid")

    # Итоговый режим форматирования.
    mode: Literal["recommendation", "needs_clarification", "no_data"]
    # Готовый пользовательский текст на русском языке.
    message: NonEmptyText
    # Основной тип клиента, скопированный из проверенного ranking result.
    primary_client_type: str | None = None
    # Необязательный дополнительный тип клиента.
    secondary_client_type: str | None = None
    # Идентичности продуктов строго в порядке детерминированного TOP.
    products: tuple[AdvisorFinalProduct, ...] = ()


def _context_model(
    context: Mapping[str, Any],
    key: str,
    model_type: type[ModelT],
    default: ModelT | None = None,
) -> ModelT:
    """Читает Pydantic-модель из контекста без ослабления ее строгой схемы."""
    value = context.get(key)
    if value is None:
        if default is not None:
            return default
        raise ValueError(f"Validation context requires {key!r}")
    if isinstance(value, model_type):
        return value
    return model_type.model_validate(value)


def _executed_sql_texts(context: Mapping[str, Any]) -> tuple[str, ...]:
    """Извлекает SQL текущего запуска из диагностических событий DBHub.

    Для изолированных contract-тестов также поддерживается явный ключ
    `_advisor_executed_sql`; production runner передает `_adk_tool_event_summaries`.
    """
    explicit = context.get("_advisor_executed_sql") or ()
    texts = [str(item) for item in explicit if str(item).strip()]
    events = context.get("_adk_tool_event_summaries") or ()
    for event in events:
        if not isinstance(event, Mapping):
            continue
        if event.get("type") != "call" or event.get("name") != "execute_sql":
            continue
        preview = str(event.get("args_preview") or "").strip()
        if not preview:
            continue
        try:
            parsed = json.loads(preview)
        except json.JSONDecodeError:
            texts.append(preview)
            continue
        if isinstance(parsed, Mapping):
            sql = parsed.get("sql") or parsed.get("query")
            if sql:
                texts.append(str(sql))
        else:
            texts.append(preview)
    return tuple(texts)


def _require_current_sql(context: Mapping[str, Any], table: str) -> None:
    """Проверяет, что текущий запуск выполнил SQL по указанной таблице."""
    pattern = re.compile(rf"(?<![A-Za-z0-9_]){re.escape(table)}(?![A-Za-z0-9_])", re.I)
    if not any(pattern.search(sql) for sql in _executed_sql_texts(context)):
        raise ValueError(f"Current run requires execute_sql for table {table!r}")


def _merged_profile(
    result: AdvisorContentResult,
    context: Mapping[str, Any],
) -> AdvisorClientProfile:
    """Объединяет сохраненный профиль и patch для проверки доказательств выбора."""
    current = _context_model(
        context,
        "advisor_client_profile",
        AdvisorClientProfile,
        AdvisorClientProfile(),
    )
    merge_result = merge_advisor_profile(current, result.profile_patch)
    if merge_result.conflicts:
        fields = [conflict.field_name for conflict in merge_result.conflicts]
        raise ValueError(f"Profile patch contains unresolved conflicts: {fields}")
    return merge_result.profile


def _validate_content_semantics(
    result: AdvisorContentResult,
    context: Mapping[str, Any],
) -> None:
    """Проверяет SQL-grounding, выбор типа и поля каждого режима content agent."""
    if not result.client_types:
        raise ValueError("At least one validated Client Types row is required")
    if any(
        row.profile_name == CLIENT_TYPES_DESCRIPTION_ROW_LABEL
        for row in result.client_types
    ):
        raise ValueError("Client Types description row cannot be used as data")
    _require_current_sql(context, CLIENT_TYPES_TABLE)

    selection = result.client_type_selection
    if selection is None:
        if result.mode != "no_data":
            raise ValueError(f"mode={result.mode!r} requires client_type_selection")
    else:
        minimum_confidence = context.get("advisor_minimum_client_type_confidence")
        if minimum_confidence is None:
            raise ValueError("Validation context requires advisor_minimum_client_type_confidence")
        validate_client_type_selection(
            selection,
            client_profile=_merged_profile(result, context),
            client_types=list(result.client_types),
            minimum_confidence=float(minimum_confidence),
        )
        if result.missing_fields != selection.missing_fields:
            raise ValueError("Top-level missing_fields must match client_type_selection")
        if (result.clarification_question or "").strip() != (
            selection.clarification_question or ""
        ).strip():
            raise ValueError(
                "Top-level clarification_question must match client_type_selection"
            )

    if result.mode == "needs_clarification":
        if selection is None or selection.mode != "needs_clarification":
            raise ValueError("Clarification content requires clarification selection mode")
        if result.products:
            raise ValueError("Clarification content must stop before product retrieval")
        if result.no_data_reason:
            raise ValueError("Clarification content must not contain no_data_reason")
        return

    if result.mode == "no_data":
        if not (result.no_data_reason or "").strip():
            raise ValueError("No-data content requires no_data_reason")
        if result.products:
            raise ValueError("No-data content must not contain products")
        return

    if selection is None or selection.mode != "selected":
        raise ValueError("Candidate content requires a selected Client Type")
    if not result.products:
        raise ValueError("Candidate content requires products")
    if result.no_data_reason:
        raise ValueError("Candidate content must not contain no_data_reason")
    _require_current_sql(context, PRODUCTS_TABLE)

    selected_name = selection.primary_type.profile_name if selection.primary_type else ""
    selected_row = next(
        row for row in result.client_types if row.profile_name == selected_name
    )
    required_columns = {
        rule.product_column
        for rule_column in (
            selected_row.required_properties,
            selected_row.preferred_properties,
            selected_row.acceptable_compromises,
            selected_row.contraindications,
        )
        for rule in rule_column
        if rule.product_column != "is_active"
    }
    for product in result.products:
        missing_columns = required_columns - set(product.attributes)
        if missing_columns:
            raise ValueError(
                f"Product {product.code!r} is missing rule attributes: "
                f"{sorted(missing_columns)}"
            )


def validate_advisor_content_result(
    data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Проверяет и нормализует внутренний результат advisor content agent.

    Неизвестные поля отклоняются Pydantic-схемой. Семантическая проверка затем
    подтверждает SQL текущего запуска по фиксированным таблицам, доказательства выбора
    типа клиента и полноту фактов, необходимых Phase 1 ranking service.
    """
    try:
        result = AdvisorContentResult.model_validate(data)
        _validate_content_semantics(result, context or {})
    except Exception as exc:
        raise build_validation_error(
            agent="advisor_content_agent",
            stage="contract",
            problem=str(exc),
            data=data,
            fields=("mode", "client_type_selection", "products"),
        ) from exc
    return result.model_dump(mode="json")


def validate_advisor_final_result(
    data: dict[str, Any],
    context: dict[str, Any],
) -> dict[str, Any]:
    """Проверяет финальный текст и запрещает менять детерминированный TOP.

    В режиме рекомендации типы клиента и полные идентичности продуктов должны
    в точности совпасть с `advisor_ranking_result`, включая порядок элементов.
    """
    try:
        result = AdvisorFinalResult.model_validate(data)
        if result.mode == "recommendation":
            ranking = _context_model(
                context or {},
                "advisor_ranking_result",
                AdvisorRankingResult,
            )
            expected_products = tuple(
                AdvisorFinalProduct(
                    code=item.product.code,
                    name=item.product.name,
                    is_active=item.product.is_active,
                )
                for item in ranking.top_products
            )
            if not expected_products:
                raise ValueError("Recommendation mode requires a non-empty ranked TOP")
            if any(
                item.product.is_active != ACTIVE_PRODUCT_STATUS
                for item in ranking.top_products
            ):
                raise ValueError("Final recommendation may contain only active products")
            if result.products != expected_products:
                raise ValueError("Final products must preserve exact ranked TOP identities and order")
            if result.primary_client_type != ranking.primary_client_type:
                raise ValueError("Final primary Client Type must match ranking result")
            if result.secondary_client_type != ranking.secondary_client_type:
                raise ValueError("Final secondary Client Type must match ranking result")
        elif result.products:
            raise ValueError(f"mode={result.mode!r} must not contain products")
    except Exception as exc:
        raise build_validation_error(
            agent="advisor_format_agent",
            stage="contract",
            problem=str(exc),
            data=data,
            fields=("mode", "primary_client_type", "products"),
        ) from exc
    return result.model_dump(mode="json")
