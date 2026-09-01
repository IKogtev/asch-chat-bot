from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Annotated, Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field, StringConstraints


T = TypeVar("T")
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
PositiveInteger = Annotated[int, Field(gt=0)]
ClientAge = Annotated[int, Field(ge=0, le=120)]
NonNegativeDecimal = Annotated[Decimal, Field(ge=0)]
# Назначение: фиксирует происхождение значения поля профиля клиента.
# `explicit` — значение прямо сообщил пользователь; `inferred` — значение вывела LLM.
ProfileValueOrigin = Literal["explicit", "inferred"]


class AdvisorProfileField(BaseModel, Generic[T]):
    model_config = ConfigDict(extra="forbid", frozen=True)

    value: T
    source_turn: NonEmptyText
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    origin: ProfileValueOrigin

    @property
    def explicit(self) -> bool:
        """Проверяет, было ли значение явно сообщено пользователем."""
        return self.origin == "explicit"


class AdvisorClientProfile(BaseModel):
    """Хранит известные характеристики клиента вместе с происхождением значений."""

    model_config = ConfigDict(extra="forbid")

    # Финансовая цель клиента. Пример: «Накопить на образование ребенка».
    goal: AdvisorProfileField[NonEmptyText] | None = None
    # Плановый срок размещения в месяцах. Пример: 84 месяца (7 лет).
    term_months: AdvisorProfileField[PositiveInteger] | None = None
    # Сумма первоначального взноса. Пример: Decimal("500000").
    contribution_amount: AdvisorProfileField[NonNegativeDecimal] | None = None
    # Периодичность взносов. Пример: «Ежемесячно» или «Единовременно».
    contribution_frequency: AdvisorProfileField[NonEmptyText] | None = None
    # Валюта вложения. Пример: «Рубли».
    currency: AdvisorProfileField[NonEmptyText] | None = None
    # Допустимый риск потери капитала. Пример: «Допустима временная потеря до 5%».
    capital_loss_tolerance: AdvisorProfileField[NonEmptyText] | None = None
    # Обязательна ли гарантия возврата капитала. Пример: True.
    guarantee_required: AdvisorProfileField[bool] | None = None
    # Требуемая ликвидность продукта. Пример: «Высокая».
    liquidity_need: AdvisorProfileField[NonEmptyText] | None = None
    # Возраст клиента в полных годах. Пример: 42.
    client_age: AdvisorProfileField[ClientAge] | None = None
    # Потребность в страховой защите. Пример: «Высокая».
    insurance_need: AdvisorProfileField[NonEmptyText] | None = None
    # Инвестиционный опыт клиента. Пример: «Облигации и паевые фонды».
    investment_experience: AdvisorProfileField[NonEmptyText] | None = None
    # Семейный контекст, влияющий на рекомендацию. Пример: «Семья с детьми».
    family_context: AdvisorProfileField[NonEmptyText] | None = None
    # Стабильность дохода клиента. Пример: «Стабильный доход».
    income_stability: AdvisorProfileField[NonEmptyText] | None = None
    # Дополнительные значимые сведения. Пример: «Готов ждать восстановления рынка».
    additional_context: AdvisorProfileField[NonEmptyText] | None = None

    def supplied_fields(self) -> dict[str, AdvisorProfileField[object]]:
        """Возвращает только заполненные поля профиля с их метаданными."""
        return {
            name: value
            for name in type(self).model_fields
            if (value := getattr(self, name)) is not None
        }

    def explicit_values(self) -> dict[str, object]:
        """Возвращает значения, которые пользователь сообщил явно, без предположений LLM."""
        return {
            name: field.value
            for name, field in self.supplied_fields().items()
            if field.explicit
        }


class AdvisorProfileConflict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    field_name: NonEmptyText
    existing: AdvisorProfileField[object]
    incoming: AdvisorProfileField[object]


class AdvisorProfileMergeResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: AdvisorClientProfile
    changed_fields: list[str] = Field(default_factory=list)
    conflicts: list[AdvisorProfileConflict] = Field(default_factory=list)

    @property
    def requires_clarification(self) -> bool:
        """Показывает, требуется ли уточнение из-за конфликтующих значений."""
        return bool(self.conflicts)


def merge_advisor_profile(
    current: AdvisorClientProfile,
    patch: AdvisorClientProfile,
    *,
    correction_fields: set[str] | frozenset[str] = frozenset(),
) -> AdvisorProfileMergeResult:
    """Объединяет текущий профиль с новым фрагментом данных.

    Новые поля добавляются, явные значения заменяют ранее выведенные значения,
    а несовпадающие явные значения регистрируются как конфликт. Поля из
    `correction_fields` заменяются только явно сообщенными значениями.

    Аргументы:
        current: Текущий накопленный профиль клиента.
        patch: Поля, извлеченные из нового сообщения.
        correction_fields: Поля, которые пользователь явно исправил.

    Возвращает:
        Обновленный профиль, список измененных полей и обнаруженные конфликты.
    """
    unknown_corrections = set(correction_fields) - set(AdvisorClientProfile.model_fields)
    if unknown_corrections:
        raise ValueError(f"Unknown profile correction fields: {sorted(unknown_corrections)}")

    updates: dict[str, AdvisorProfileField[object]] = {}
    changed_fields: list[str] = []
    conflicts: list[AdvisorProfileConflict] = []

    for field_name, incoming in patch.supplied_fields().items():
        existing = getattr(current, field_name)
        if existing is None:
            updates[field_name] = incoming
            changed_fields.append(field_name)
            continue
        if existing.value == incoming.value:
            if incoming.explicit and not existing.explicit:
                updates[field_name] = incoming
                changed_fields.append(field_name)
            continue
        if field_name in correction_fields:
            if not incoming.explicit:
                raise ValueError(
                    f"Correction for {field_name!r} must come from an explicit value"
                )
            updates[field_name] = incoming
            changed_fields.append(field_name)
            continue
        if not existing.explicit and incoming.explicit:
            updates[field_name] = incoming
            changed_fields.append(field_name)
            continue
        if existing.explicit and not incoming.explicit:
            continue
        conflicts.append(
            AdvisorProfileConflict(
                field_name=field_name,
                existing=existing,
                incoming=incoming,
            )
        )

    merged = current.model_copy(update=updates, deep=True)
    return AdvisorProfileMergeResult(
        profile=merged,
        changed_fields=changed_fields,
        conflicts=conflicts,
    )


def reset_advisor_profile(
    initial_patch: AdvisorClientProfile | None = None,
) -> AdvisorClientProfile:
    """Создает независимый профиль для нового клиента.

    Если передан `initial_patch`, новый профиль начинается с указанных значений.
    В противном случае возвращается полностью пустой профиль.
    """
    return (initial_patch or AdvisorClientProfile()).model_copy(deep=True)
