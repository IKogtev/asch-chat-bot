from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any, Literal, Mapping, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from agent.advisor_profile import AdvisorClientProfile, merge_advisor_profile
from agent.advisor_profile_matcher import (
    AdvisorSelectedClientType,
    validate_selected_client_type,
)
from agent.advisor_ranking_service import (
    ACTIVE_PRODUCT_STATUS,
    AdvisorProductFacts,
    AdvisorRankingResult,
    validate_product_age_bounds,
)
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
    # Единственный выбранный тип вместе с полной строкой из текущего SQL.
    selected_client_type: AdvisorSelectedClientType | None = None
    # Недостающие поля профиля для режима уточнения.
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
    # Идентичности продуктов строго в порядке детерминированного TOP.
    products: tuple[AdvisorFinalProduct, ...] = ()


ADVISOR_PRODUCT_LINE_RE = re.compile(
    r"^\s*(?P<number>\d+)\.\s+(?P<code>\S+)\s+"
    r"(?P<name>.+?)\s+\(КВ\s+[^)\r\n]+%\)\s*$",
    re.MULTILINE,
)


def _validate_recommendation_message_products(
    message: str,
    expected_products: tuple[AdvisorFinalProduct, ...],
) -> None:
    """Проверяет, что текст показывает ровно структурированный TOP."""
    displayed = list(ADVISOR_PRODUCT_LINE_RE.finditer(message))
    if len(displayed) != len(expected_products):
        raise ValueError(
            "Recommendation message must display every ranked TOP product exactly once"
        )
    for position, (match, expected) in enumerate(
        zip(displayed, expected_products),
        start=1,
    ):
        if int(match.group("number")) != position:
            raise ValueError("Recommendation product numbering must be contiguous")
        if (
            match.group("code") != expected.code
            or match.group("name").strip() != expected.name
        ):
            raise ValueError(
                "Recommendation message products must preserve exact ranked TOP identities and order"
            )


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


def _validate_products_sql(context: Mapping[str, Any]) -> None:
    """Требует узкий SQL только по действующим продуктам-кандидатам."""
    table_pattern = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(PRODUCTS_TABLE)}(?![A-Za-z0-9_])",
        re.I,
    )
    active_pattern = re.compile(
        rf"(?:\b[A-Za-z_][A-Za-z0-9_]*\.)?is_active\s*=\s*"
        rf"(['\"]){re.escape(ACTIVE_PRODUCT_STATUS)}\1",
        re.I,
    )
    product_sql = [
        sql for sql in _executed_sql_texts(context) if table_pattern.search(sql)
    ]
    for sql in product_sql:
        select_match = re.search(
            r"\bselect\b(?P<columns>.*?)\bfrom\b",
            sql,
            re.I | re.S,
        )
        if select_match is None or "*" in select_match.group("columns"):
            raise ValueError("Advisor products SQL must not use wildcard projection")
        if active_pattern.search(sql) is None:
            raise ValueError(
                "Advisor products SQL must filter is_active by the active status"
            )


def _require_products_sql_columns(
    context: Mapping[str, Any],
    required_columns: set[str],
) -> None:
    """Проверяет наличие обязательных технических колонок в выборке `products`."""
    if not required_columns:
        return
    table_pattern = re.compile(
        rf"(?<![A-Za-z0-9_]){re.escape(PRODUCTS_TABLE)}(?![A-Za-z0-9_])",
        re.I,
    )
    product_sql = [
        sql for sql in _executed_sql_texts(context) if table_pattern.search(sql)
    ]
    projected_columns = []
    for sql in product_sql:
        select_match = re.search(r"\bselect\b(?P<columns>.*?)\bfrom\b", sql, re.I | re.S)
        if select_match is not None:
            projected_columns.append(select_match.group("columns"))
    if not any(
        all(
            re.search(
                rf"(?<![A-Za-z0-9_]){re.escape(column)}(?![A-Za-z0-9_])",
                columns,
                re.I,
            )
            for column in required_columns
        )
        for columns in projected_columns
    ):
        raise ValueError(
            "Advisor products SQL is missing required columns: "
            f"{sorted(required_columns)}"
        )


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
    # Происхождение данных проверяется до объединения, поэтому явное значение
    # текущего фрагмента считается доверенным исправлением сохраненного сценария.
    correction_fields = set(result.profile_patch.explicit_values())
    merge_result = merge_advisor_profile(
        current,
        result.profile_patch,
        correction_fields=correction_fields,
    )
    if merge_result.conflicts:
        fields = [conflict.field_name for conflict in merge_result.conflicts]
        raise ValueError(f"Profile patch contains unresolved conflicts: {fields}")
    return merge_result.profile


def _validate_profile_patch_provenance(
    result: AdvisorContentResult,
    context: Mapping[str, Any],
) -> None:
    supplied = result.profile_patch.supplied_fields()
    if not supplied:
        return
    expected_source_turn = str(context.get("advisor_source_turn") or "").strip()
    expected_updated_at_raw = str(context.get("advisor_updated_at") or "").strip()
    if not expected_source_turn or not expected_updated_at_raw:
        raise ValueError("Advisor provenance requires source turn and updated_at context")
    expected_updated_at = datetime.fromisoformat(
        expected_updated_at_raw.replace("Z", "+00:00")
    )
    for field_name, field in supplied.items():
        if field.source_turn != expected_source_turn:
            raise ValueError(
                f"Profile field {field_name!r} must use the current advisor source turn"
            )
        if field.updated_at != expected_updated_at:
            raise ValueError(
                f"Profile field {field_name!r} must use the current advisor updated_at"
            )


def _validate_content_semantics(
    result: AdvisorContentResult,
    context: Mapping[str, Any],
) -> None:
    """Проверяет SQL-grounding, выбор типа и поля каждого режима content agent."""
    _require_current_sql(context, CLIENT_TYPES_TABLE)
    _validate_profile_patch_provenance(result, context)
    merged_profile = _merged_profile(result, context)

    selected = result.selected_client_type

    if result.mode == "needs_clarification":
        if selected is not None:
            raise ValueError("Clarification content must not select a Client Type")
        supplied_fields = merged_profile.supplied_fields()
        if not result.missing_fields:
            raise ValueError("Clarification content requires at least one missing field")
        for field_name in result.missing_fields:
            if field_name not in AdvisorClientProfile.model_fields:
                raise ValueError(f"Unknown missing client field: {field_name!r}")
            if field_name in supplied_fields:
                raise ValueError(
                    f"Client field {field_name!r} is supplied and cannot be missing"
                )
        question = (result.clarification_question or "").strip()
        if not question or question.count("?") != 1 or "\n" in question:
            raise ValueError("Clarification mode requires exactly one question")
        if result.products:
            raise ValueError("Clarification content must stop before product retrieval")
        if result.no_data_reason:
            raise ValueError("Clarification content must not contain no_data_reason")
        return

    if result.mode == "no_data":
        if selected is not None:
            raise ValueError("No-data content must not select a Client Type")
        if result.missing_fields or result.clarification_question:
            raise ValueError("No-data content must not contain clarification fields")
        if not (result.no_data_reason or "").strip():
            raise ValueError("No-data content requires no_data_reason")
        if result.products:
            raise ValueError("No-data content must not contain products")
        return

    if selected is None:
        raise ValueError("Candidate content requires a selected Client Type")
    if result.missing_fields or result.clarification_question:
        raise ValueError("Candidate content must not contain clarification fields")
    minimum_confidence = context.get("advisor_minimum_client_type_confidence")
    if minimum_confidence is None:
        raise ValueError("Validation context requires advisor_minimum_client_type_confidence")
    validate_selected_client_type(
        selected,
        client_profile=merged_profile,
        minimum_confidence=float(minimum_confidence),
    )
    if not result.products:
        raise ValueError("Candidate content requires products")
    if result.no_data_reason:
        raise ValueError("Candidate content must not contain no_data_reason")
    _require_current_sql(context, PRODUCTS_TABLE)
    _validate_products_sql(context)

    selected_row = selected.definition
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
    age_field = merged_profile.age
    if age_field is not None and age_field.explicit:
        required_columns.update({"age_min", "age_max"})
        _require_products_sql_columns(context, {"age_min", "age_max"})
    for product in result.products:
        if product.is_active != ACTIVE_PRODUCT_STATUS:
            raise ValueError("Advisor candidates may contain only active products")
        missing_columns = required_columns - set(product.attributes)
        if missing_columns:
            raise ValueError(
                f"Product {product.code!r} is missing rule attributes: "
                f"{sorted(missing_columns)}"
            )
        if age_field is not None and age_field.explicit:
            validate_product_age_bounds(product.to_product_facts())


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
            fields=("mode", "selected_client_type", "products"),
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
                raise ValueError(
                    "Final products must preserve exact ranked TOP identities and order"
                )
            _validate_recommendation_message_products(
                result.message,
                expected_products,
            )
            if result.primary_client_type != ranking.primary_client_type:
                raise ValueError("Final primary Client Type must match ranking result")
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
