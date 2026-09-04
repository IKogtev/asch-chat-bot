from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints
from typing_extensions import Annotated

from agent.advisor_profile_matcher import (
    AdvisorClientTypeDefinition,
    AdvisorClientTypeEvidence,
    AdvisorSelectedClientType,
)
from utils.client_types import ClientTypeRule


# Непустая строка после удаления пробелов по краям. Пример: «Консервативный».
NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
# Код исключения: продукт не соответствует обязательному свойству типа клиента.
REQUIRED_PROPERTY_MISMATCH = "REQUIRED_PROPERTY_MISMATCH"
# Код исключения: продукт соответствует противопоказанию типа клиента.
CONTRAINDICATED_PROPERTY = "CONTRAINDICATED_PROPERTY"
# Код исключения: итоговый балл продукта ниже минимально допустимого значения.
BELOW_MINIMUM_SCORE = "BELOW_MINIMUM_SCORE"
# Текстовый статус активного продукта из фиксированного продуктового классификатора.
ACTIVE_PRODUCT_STATUS = "Действующий"
# Код исключения: продукт не имеет активного статуса в продуктовом классификаторе.
INACTIVE_PRODUCT = "INACTIVE_PRODUCT"


class AdvisorScoringPolicy(BaseModel):
    """Хранит версионированные бизнес-настройки детерминированного ранжирования."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Версия набора правил. Пример: `pilot-v1`.
    version: NonEmptyText
    # Максимальная сумма баллов за предпочтительные свойства. Пример: Decimal("100").
    preferred_weight: Decimal = Field(ge=0, le=100)
    # Максимальный штраф за допустимые компромиссы. Пример: Decimal("20").
    compromise_penalty: Decimal = Field(ge=0, le=100)
    # Минимальный балл для попадания продукта в кандидаты. Пример: Decimal("60").
    minimum_score: Decimal = Field(ge=0, le=100)
    # Допустимый разрыв баллов при замене продукта ради разнообразия. Пример: 10.
    diversity_max_score_gap: Decimal = Field(ge=0, le=100)
    # Максимальное количество продуктов одного семейства в TOP. Пример: 1.
    diversity_max_per_family: int = Field(ge=1)
    # Максимальное количество итоговых рекомендаций. По умолчанию: 5.
    top_n: int = Field(default=5, ge=1, le=10)
    # Точность округления баллов. Пример: Decimal("0.01") для двух знаков.
    rounding_quantum: Decimal = Field(default=Decimal("0.01"), gt=0)


class AdvisorProductFacts(BaseModel):
    """Хранит проверенные факты об одном продукте, полученные из каталога."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Код продукта. Пример: «2832».
    code: NonEmptyText
    # Наименование продукта. Пример: «Fort Knox 3 месяца».
    name: NonEmptyText
    # Статус продукта. Пример: «Действующий».
    is_active: NonEmptyText
    # Остальные свойства каталога. Пример: `{"currency": "Рубли"}`.
    attributes: dict[str, Any] = Field(default_factory=dict)
    # Явное семейство для проверки разнообразия. Пример: «Unit Linked».
    family: str | None = None
    # Приоритет при одинаковых баллах: меньшее значение идет раньше. Пример: 10.
    tie_break_priority: int = 100

    def value_for(self, product_column: str) -> Any:
        """Возвращает значение запрошенной технической колонки продукта.

        Статус хранится в отдельном поле `is_active`; значения остальных колонок
        извлекаются из словаря `attributes`.
        """
        if product_column == "is_active":
            return self.is_active
        return self.attributes.get(product_column)

    def diversity_family(self) -> str:
        """Возвращает нормализованное семейство продукта для diversity-проверки.

        Источники проверяются по порядку: явное `family`, атрибут
        `product_family`, тип продукта и, в качестве последнего варианта, код.
        """
        value = (
            self.family
            or self.attributes.get("product_family")
            or self.attributes.get("product_type")
            or self.code
        )
        return str(value).strip().casefold()


class AdvisorExclusion(BaseModel):
    """Описывает одну детерминированную причину исключения продукта."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Код исключенного продукта. Пример: «8914».
    product_code: NonEmptyText
    # Наименование исключенного продукта. Пример: «Фиксированный доход 1 год».
    product_name: NonEmptyText
    # Статус исключенного продукта. Пример: «Действующий».
    product_status: NonEmptyText
    # Машиночитаемый код причины. Пример: `REQUIRED_PROPERTY_MISMATCH`.
    code: NonEmptyText
    # Понятное человеку описание причины исключения.
    reason: NonEmptyText
    # Колонка правила Client Types. Пример: `required_properties`.
    rule_column: str | None = None
    # Техническая колонка продукта. Пример: `currency`.
    product_column: str | None = None
    # Разрешенные или запрещенные значения. Пример: `(«Рубли»,)`.
    expected_values: tuple[str, ...] = ()
    # Фактическое значение продукта. Пример: «Доллары».
    actual_value: Any = None


class AdvisorScoreComponent(BaseModel):
    """Хранит вклад одного критерия в итоговый клиентский балл продукта."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Стабильное имя критерия. Пример: `preferred:1:product_type`.
    criterion: NonEmptyText
    # Начисленные или вычтенные баллы. Пример: Decimal("20.00") или -10.
    points: Decimal
    # Совпало ли свойство продукта со значением правила. Пример: True.
    matched: bool


class AdvisorRankedProduct(BaseModel):
    """Объединяет факты продукта с рассчитанным баллом и его расшифровкой."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Проверенные данные продукта из каталога.
    product: AdvisorProductFacts
    # Итоговый балл пригодности от 0 до 100. Пример: Decimal("80.00").
    score: Decimal = Field(ge=0, le=100)
    # Компоненты, сумма которых равна итоговому баллу.
    score_components: tuple[AdvisorScoreComponent, ...]
    # Сработавшие допустимые компромиссы, которые нужно раскрыть пользователю.
    compromises: tuple[ClientTypeRule, ...] = ()


class AdvisorDiversityReplacement(BaseModel):
    """Фиксирует замену похожего продукта альтернативой из другого семейства."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Код более высоко оцененного, но отложенного дублирующего продукта.
    deferred_product_code: NonEmptyText
    # Код выбранной разнообразной альтернативы.
    selected_product_code: NonEmptyText
    # Объяснение выполненной замены.
    reason: NonEmptyText


class AdvisorRankingResult(BaseModel):
    """Хранит полный проверяемый результат фильтрации и ранжирования продуктов."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    # Основной тип клиента, использованный для правил. Пример: «Умеренный».
    primary_client_type: NonEmptyText
    # Проверенные доказательства выбора типа клиента.
    match_evidence: tuple[AdvisorClientTypeEvidence, ...] = ()
    # Прошедшие жесткие правила и минимальный порог продукты в порядке балла.
    accepted_candidates: tuple[AdvisorRankedProduct, ...]
    # Все причины исключения неподходящих продуктов.
    excluded_candidates: tuple[AdvisorExclusion, ...]
    # Финальный список рекомендаций после diversity-проверки, обычно TOP-5.
    top_products: tuple[AdvisorRankedProduct, ...]
    # Выполненные замены ради разнообразия итогового списка.
    diversity_replacements: tuple[AdvisorDiversityReplacement, ...] = ()
    # Версия политики, по которой рассчитан результат. Пример: `pilot-v1`.
    scoring_policy_version: NonEmptyText


def _normalized(value: Any) -> str:
    """Нормализует значение для сравнения без учета регистра и крайних пробелов."""
    if value is None:
        return ""
    return str(value).strip().casefold()


def _matches(product: AdvisorProductFacts, rule: ClientTypeRule) -> bool:
    """Проверяет совпадение свойства продукта хотя бы с одним значением правила."""
    actual = _normalized(product.value_for(rule.product_column))
    expected = {_normalized(value) for value in rule.expected_values}
    return actual in expected


class AdvisorRankingService:
    """Детерминированно фильтрует, оценивает и выбирает TOP продуктов."""

    def __init__(self, policy: AdvisorScoringPolicy) -> None:
        """Создает сервис с явно переданной версионированной политикой."""
        self.policy = policy

    def rank(
        self,
        *,
        products: list[AdvisorProductFacts],
        selected_client_type: AdvisorSelectedClientType,
    ) -> AdvisorRankingResult:
        """Выполняет полный цикл фильтрации и ранжирования.

        Сначала применяются обязательные свойства и противопоказания. Затем
        подходящие продукты получают мягкий балл, проверяются на минимальный
        порог, сортируются с устойчивыми tie-break правилами и проходят отбор
        разнообразных семейств.

        Аргументы:
            products: Проверенные факты о продуктах-кандидатах.
            selected_client_type: Проверенный единственный тип клиента.

        Возвращает:
            Полный результат с кандидатами, исключениями, TOP и объяснениями.

        Исключения:
            ValueError: Если данные выбранного типа нарушают контракт.
        """
        primary_client_type = selected_client_type.definition

        excluded: list[AdvisorExclusion] = []
        accepted: list[AdvisorRankedProduct] = []

        for product in products:
            hard_exclusions = self._hard_exclusions(product, primary_client_type)
            if hard_exclusions:
                excluded.extend(hard_exclusions)
                continue
            ranked = self._score(product, primary_client_type)
            if ranked.score < self.policy.minimum_score:
                excluded.append(
                    AdvisorExclusion(
                        product_code=product.code,
                        product_name=product.name,
                        product_status=product.is_active,
                        code=BELOW_MINIMUM_SCORE,
                        reason=(
                            f"Client-fit score {ranked.score} is below minimum "
                            f"{self.policy.minimum_score}."
                        ),
                    )
                )
                continue
            accepted.append(ranked)

        accepted.sort(
            key=lambda item: (
                -item.score,
                item.product.tie_break_priority,
                item.product.code,
                item.product.name.casefold(),
                item.product.is_active.casefold(),
            )
        )
        top_products, replacements = self._select_diverse(accepted)

        return AdvisorRankingResult(
            primary_client_type=primary_client_type.profile_name,
            match_evidence=tuple(selected_client_type.evidence),
            accepted_candidates=tuple(accepted),
            excluded_candidates=tuple(excluded),
            top_products=tuple(top_products),
            diversity_replacements=tuple(replacements),
            scoring_policy_version=self.policy.version,
        )

    def _hard_exclusions(
        self,
        product: AdvisorProductFacts,
        client_type: AdvisorClientTypeDefinition,
    ) -> list[AdvisorExclusion]:
        """Возвращает все жесткие причины исключения одного продукта.

        Несовпадение с каждым обязательным свойством и совпадение с каждым
        противопоказанием сохраняются как отдельные причины.
        """
        exclusions: list[AdvisorExclusion] = []
        if product.is_active != ACTIVE_PRODUCT_STATUS:
            exclusions.append(
                AdvisorExclusion(
                    product_code=product.code,
                    product_name=product.name,
                    product_status=product.is_active,
                    code=INACTIVE_PRODUCT,
                    reason="Product status is not active in the product classifier.",
                    product_column="is_active",
                    expected_values=(ACTIVE_PRODUCT_STATUS,),
                    actual_value=product.is_active,
                )
            )
        for rule in client_type.required_properties:
            if not _matches(product, rule):
                exclusions.append(
                    self._rule_exclusion(
                        product,
                        rule,
                        code=REQUIRED_PROPERTY_MISMATCH,
                        rule_column="required_properties",
                        reason="Required Client Type product property does not match.",
                    )
                )
        for rule in client_type.contraindications:
            if _matches(product, rule):
                exclusions.append(
                    self._rule_exclusion(
                        product,
                        rule,
                        code=CONTRAINDICATED_PROPERTY,
                        rule_column="contraindications",
                        reason="Product matches a Client Type contraindication.",
                    )
                )
        return exclusions

    @staticmethod
    def _rule_exclusion(
        product: AdvisorProductFacts,
        rule: ClientTypeRule,
        *,
        code: str,
        rule_column: str,
        reason: str,
    ) -> AdvisorExclusion:
        """Создает унифицированную запись исключения по одному правилу."""
        return AdvisorExclusion(
            product_code=product.code,
            product_name=product.name,
            product_status=product.is_active,
            code=code,
            reason=reason,
            rule_column=rule_column,
            product_column=rule.product_column,
            expected_values=rule.expected_values,
            actual_value=product.value_for(rule.product_column),
        )

    def _score(
        self,
        product: AdvisorProductFacts,
        client_type: AdvisorClientTypeDefinition,
    ) -> AdvisorRankedProduct:
        """Рассчитывает мягкий балл продукта и его компоненты.

        Совпавшие предпочтения добавляют баллы, а совпавшие допустимые
        компромиссы вычитают ограниченный штраф. Результат ограничивается
        диапазоном 0–100 и округляется согласно политике.
        """
        components: list[AdvisorScoreComponent] = []
        compromises: list[ClientTypeRule] = []

        preferred_count = len(client_type.preferred_properties)
        preferred_points = (
            self.policy.preferred_weight / preferred_count
            if preferred_count
            else Decimal("0")
        )
        for index, rule in enumerate(client_type.preferred_properties, start=1):
            matched = _matches(product, rule)
            components.append(
                AdvisorScoreComponent(
                    criterion=f"preferred:{index}:{rule.product_column}",
                    points=preferred_points if matched else Decimal("0"),
                    matched=matched,
                )
            )

        compromise_count = len(client_type.acceptable_compromises)
        compromise_points = (
            self.policy.compromise_penalty / compromise_count
            if compromise_count
            else Decimal("0")
        )
        for index, rule in enumerate(client_type.acceptable_compromises, start=1):
            matched = _matches(product, rule)
            if matched:
                compromises.append(rule)
            components.append(
                AdvisorScoreComponent(
                    criterion=f"compromise:{index}:{rule.product_column}",
                    points=-compromise_points if matched else Decimal("0"),
                    matched=matched,
                )
            )

        raw_score = sum((component.points for component in components), Decimal("0"))
        bounded_score = min(Decimal("100"), max(Decimal("0"), raw_score))
        score = bounded_score.quantize(
            self.policy.rounding_quantum,
            rounding=ROUND_HALF_UP,
        )
        rounded_components = tuple(
            component.model_copy(
                update={
                    "points": component.points.quantize(
                        self.policy.rounding_quantum,
                        rounding=ROUND_HALF_UP,
                    )
                }
            )
            for component in components
        )
        component_total = sum(
            (component.points for component in rounded_components),
            Decimal("0"),
        )
        rounding_adjustment = score - component_total
        if rounding_adjustment:
            rounded_components += (
                AdvisorScoreComponent(
                    criterion="rounding_adjustment",
                    points=rounding_adjustment,
                    matched=True,
                ),
            )
        return AdvisorRankedProduct(
            product=product,
            score=score,
            score_components=rounded_components,
            compromises=tuple(compromises),
        )

    def _select_diverse(
        self,
        ranked: list[AdvisorRankedProduct],
    ) -> tuple[list[AdvisorRankedProduct], list[AdvisorDiversityReplacement]]:
        """Формирует TOP с учетом ограничений разнообразия семейств.

        Продукт из другого семейства может заменить очередной дубликат только
        тогда, когда разница баллов не превышает разрешенный политикой порог.
        Если подходящей альтернативы нет, исходный порядок сохраняется.
        """
        remaining = list(ranked)
        selected: list[AdvisorRankedProduct] = []
        replacements: list[AdvisorDiversityReplacement] = []
        family_counts: dict[str, int] = {}

        while remaining and len(selected) < self.policy.top_n:
            best = remaining[0]
            best_family = best.product.diversity_family()
            if family_counts.get(best_family, 0) < self.policy.diversity_max_per_family:
                choice_index = 0
            else:
                choice_index = next(
                    (
                        index
                        for index, candidate in enumerate(remaining[1:], start=1)
                        if family_counts.get(candidate.product.diversity_family(), 0)
                        < self.policy.diversity_max_per_family
                        and best.score - candidate.score
                        <= self.policy.diversity_max_score_gap
                    ),
                    0,
                )
            choice = remaining.pop(choice_index)
            if choice_index:
                replacements.append(
                    AdvisorDiversityReplacement(
                        deferred_product_code=best.product.code,
                        selected_product_code=choice.product.code,
                        reason="Selected a different product family within the score-gap limit.",
                    )
                )
            selected.append(choice)
            family = choice.product.diversity_family()
            family_counts[family] = family_counts.get(family, 0) + 1

        return selected, replacements
