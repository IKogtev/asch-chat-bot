"""
Композитный агент: поиск документов (LLM + kb_search), нормализация полного списка, сохранение в БД.

Текст из JSON doc_search_agent (поле message при mode=document_list) пользователю как список не показывается:
итоговый вывод — через UI бота из БД. Здесь при успехе выставляется краткий служебный _root_final_text
(DOC_SEARCH_SUCCESS_HINT); в Telegram при смене search_id бот дополнительно шлёт отрендеренную первую порцию.

Пагинация и скачивание по номеру — в Telegram-боте (handlers); прямой вызов ADK с intent show_more/show_all/file_download
даёт краткую подсказку (для web без бота).
"""
from typing import Any, AsyncGenerator, Dict, List, Optional

from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event

from ..config import ACTIVE_DOCUMENTS_COLLECTION, DOC_SEARCH_PAGE_SIZE
from ..helpers import DOC_SEARCH_SUCCESS_HINT, format_text_answer, truncate_for_log
from ..json_leaf_runner import AgentValidationFailure, run_json_leaf_agent
from .doc_search_agent import validate_doc_search_result
from ..doc_search_kb_context import format_kb_hits_summary
from ..doc_search_validation import (
    DOC_SEARCH_MAX_ATTEMPTS,
    DOC_SEARCH_NO_DATA_MESSAGE,
    DocSearchRetryableValidationError,
)
from utils.logger import setup_logger

logger = setup_logger("doc_search_orchestrator", "agent.log")
VALIDATION_ERROR_USER_MESSAGE = "Не удалось корректно обработать запрос. Попробуйте переформулировать вопрос."


def _follow_up_unhandled_in_agent_hint(intent: str) -> str:
    """
    Текст, если follow-up (ещё / все / номер) дошёл до оркестратора (не обработан клиентом).
    Формулировки согласованы с bot.services.config (SHOW_MORE_RE, SHOW_ALL_RE) и parse_download_ranks.
    """
    if intent == "show_more":
        return (
            "Следующую порцию списка документов можно запросить отдельным сообщением, "
            "например: «ещё», «покажи ещё», «дальше» или «ещё файлы»."
        )
    if intent == "show_all":
        return (
            "Весь список документов можно запросить отдельным сообщением, "
            "например: «все», «покажи все», «всё», «да» или «полный список»."
        )
    if intent == "file_download":
        return (
            "Скачивание по номеру из списка выполняется так: отправьте номер документа "
            "(например «3»), несколько номеров через запятую или фразу вида «скачай 1», «1 и 3»."
        )

def _telegram_user_id(ctx: InvocationContext) -> Optional[str]:
    """ID пользователя Telegram из ADK Session (тот же, что в /run)."""
    raw = getattr(ctx.session, "user_id", None)
    if raw is None:
        return None
    try:
        return str(raw)
    except ValueError:
        return None


async def _persist_full_list(
    ctx: InvocationContext,
    *,
    query: str,
    items: List[Dict[str, Any]],
    shown_count: int,
) -> None:
    """
    Сохраняет полный список найденных документов по поисковому запросу пользователя в базе данных (PostgreSQL).

    Аргументы:
        ctx (InvocationContext): Контекст вызова агента, использующийся для получения пользовательских и сессионных данных.
        query (str): Текст поискового запроса пользователя.
        items (List[Dict[str, Any]]): Список документов для сохранения (каждый документ в виде словаря).
        shown_count (int): Количество документов, показанных пользователю (может отличаться от общего числа найденных).

    Процедура:
        - Получает пул соединения с базой данных.
        - Проверяет наличие DATABASE_URL, user_id и session.id в контексте; если какого-либо параметра нет — логирует варнинг и пропускает сохранение.
        - Экранирует и нормализует поля документов для записи.
        - Сохраняет результат поиска через функцию save_doc_search_results.
        - В случае ошибки логирует её с трассировкой.
    """
    from utils.search_results_db import get_shared_pool, save_doc_search_results

    # Получаем пул соединения с БД, если переменная окружения не установлена — пропускаем сохранение
    pool = await get_shared_pool()
    if pool is None:
        logger.warning("DATABASE_URL не задан — список документов не сохранён в PostgreSQL")
        return

    # Получаем ID пользователя Telegram из сессии
    uid = _telegram_user_id(ctx)
    if uid is None:
        logger.warning("session.user_id отсутствует — пропуск сохранения списка в БД")
        return

    # Получаем идентификатор сессии
    sid = str(getattr(ctx.session, "id", None) or "")
    if not sid:
        logger.warning("session.id отсутствует — пропуск сохранения списка в БД")
        return

    # Формируем список документов для сохранения в БД
    db_items: List[Dict[str, Any]] = []
    for it in items:
        db_items.append(
            {
                "rank": int(it["rank"]),  # Позиция документа в результате
                "document_id": str(it["document_id"]),  # Уникальный идентификатор документа
                "source_name": str(it["source_name"]),  # Название источника
                "source_path": it.get("source_path"),   # Путь к файлу (может быть None)
                "score": it.get("score"),               # Оценка релевантности (опционально)
            }
        )

    try:
        # Сохраняем результаты поиска пользователя в БД
        await save_doc_search_results(pool, uid, sid, query, db_items, shown_count)
        logger.info(
            "doc_search: сохранено в БД user_id=%s session=%s rows=%s shown=%s",
            uid,
            sid,
            len(db_items),
            shown_count,
        )
    except Exception as e:
        # Логируем ошибку с подробностями и трассировкой исключения
        logger.error("doc_search: ошибка сохранения в БД: %s", e, exc_info=True)


class DocSearchOrchestrator(BaseAgent):
    """
    Агрегатор поиска документов:
    Этот агент отвечает только за новый поиск документов (intent = "doc_search").
    Оркестрация следующая: LLM-агент -> kb_search -> полный список документов -> сохранение в БД.
    Не обрабатывает follow-up команды, скачивание и др. — только выдаёт новый список и записывает его.

    Атрибуты:
        doc_search_agent (LlmAgent): агент, выполняющий LLM+kb_search для поиска документов.
        doc_collection (str): название коллекции документов для поиска.
        model_config (dict): дополнительная конфигурация pydantic-модели.

    Методы:
        __init__: конструктор класса, принимает агента поиска и коллекцию документов.
        _get_required_state_dict: вспомогательный метод для извлечения словаря из state.
        _run_async_impl: основная асинхронная исполняющая корутина-генератор.
    """

    doc_search_agent: LlmAgent
    doc_collection: str
    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        doc_search_agent: LlmAgent,
        doc_collection: str = ACTIVE_DOCUMENTS_COLLECTION,
    ):
        """
        Инициализация оркестратора поиска документов.

        Аргументы:
            doc_search_agent (LlmAgent): агент, выполняющий LLM+kb_search поиск.
            doc_collection (str): имя коллекции документов для поиска (по умолчанию активная).
        """
        super().__init__(
            name="doc_search_orchestrator",
            doc_search_agent=doc_search_agent,
            doc_collection=doc_collection,
            sub_agents=[doc_search_agent],
        )

    def _get_required_state_dict(self, ctx: InvocationContext, key: str) -> Dict[str, Any]:
        """
        Вытаскивает из state словарь по ключу, иначе бросает ValueError.

        :param ctx: Контекст вызова агента.
        :param key: Ключ для поиска в state.
        :return: Значение из state, если это dict.
        """
        value = ctx.session.state.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"State key '{key}' must be dict, got {type(value)}")
        return value

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        """
        Основная логика асинхронного запуска агента:
        - Проверяет intent. Если это doc_search — запускает поиск документов через LLM+kb_search.
        - Если intent относится к follow-up (show_more и др.) — просто ставит хинт и возвращает результат.
        - Сохраняет список документов в базу данных.
        Генерирует события Event для внешнего обработчика.

        :param ctx: Контекст сессии пользователя, содержит все данные сессии.
        """
        user_query = str(ctx.session.state.get("user_query") or "").strip()
        doc_search_query = str(ctx.session.state.get("doc_search_query") or user_query).strip()
        intent = str(ctx.session.state.get("doc_search_intent") or "doc_search").strip()

        # Логирование полученного интента и запроса
        logger.info(
            "doc_search_orchestrator: intent=%s query=%s",
            intent,
            truncate_for_log(doc_search_query, 300),
        )

        # Обрабатываем только новый поиск, для других интентов — возвращаем обработанное сообщение
        if intent in ("show_more", "show_all", "file_download"):
            ctx.session.state["_root_final_text"] = _follow_up_unhandled_in_agent_hint(intent)
            return

        page = DOC_SEARCH_PAGE_SIZE  # Количество результатов на страницу (см. конфиг)

        ctx.session.state["doc_search_collection"] = self.doc_collection
        ctx.session.state.pop("_doc_search_kb_hits", None)
        ctx.session.state["doc_search_rerank_only"] = False
        ctx.session.state["doc_search_retry_reason"] = ""

        doc_search: Dict[str, Any] | None = None
        last_validation_failure: AgentValidationFailure | None = None
        attempts_used = 0

        for attempt in range(1, DOC_SEARCH_MAX_ATTEMPTS + 1):
            attempts_used = attempt
            ctx.session.state["doc_search_attempt"] = attempt
            ctx.session.state["doc_search_rerank_only"] = attempt > 1

            kb_hits_raw = ctx.session.state.get("_doc_search_kb_hits")
            kb_hits_list = kb_hits_raw if isinstance(kb_hits_raw, list) else []
            kb_hit_count = len(kb_hits_list)
            logger.info(
                "doc_search_orchestrator: attempt %s/%s rerank_only=%s kb_hits=%s retry_reason=%s",
                attempt,
                DOC_SEARCH_MAX_ATTEMPTS,
                ctx.session.state.get("doc_search_rerank_only"),
                format_kb_hits_summary(kb_hits_list),
                truncate_for_log(ctx.session.state.get("doc_search_retry_reason"), 200),
            )

            ctx.session.state.pop("doc_search_result_json", None)
            ctx.session.state.pop("_doc_search_result_parsed", None)

            try:
                async for event in run_json_leaf_agent(
                    ctx=ctx,
                    agent=self.doc_search_agent,
                    output_key="doc_search_result_json",
                    parsed_state_key="_doc_search_result_parsed",
                    validator=validate_doc_search_result,
                    log_label="doc_search_result_json",
                    validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
                ):
                    yield event
                doc_search = self._get_required_state_dict(ctx, "_doc_search_result_parsed")
                if attempt > 1:
                    logger.info(
                        "doc_search_orchestrator: rerank retry succeeded on attempt %s mode=%s results=%s",
                        attempt,
                        doc_search.get("mode"),
                        len(doc_search.get("results") or []),
                    )
                break
            except DocSearchRetryableValidationError as exc:
                logger.warning(
                    "doc_search_orchestrator: retryable validation on attempt %s/%s: %s",
                    attempt,
                    DOC_SEARCH_MAX_ATTEMPTS,
                    exc,
                )
                if attempt >= DOC_SEARCH_MAX_ATTEMPTS:
                    if exc.reason == "empty_relevant":
                        logger.info(
                            "doc_search_orchestrator: no_data after %s attempts "
                            "(empty_relevant, kb_hits=%s)",
                            attempt,
                            kb_hit_count,
                        )
                        doc_search = {
                            "status": "ok",
                            "mode": "no_data",
                            "message": DOC_SEARCH_NO_DATA_MESSAGE,
                            "results": [],
                        }
                        break
                    raise AgentValidationFailure(
                        log_label="doc_search_result_json",
                        validation_error=str(exc),
                        raw=str(ctx.session.state.get("doc_search_result_json") or ""),
                        user_message=VALIDATION_ERROR_USER_MESSAGE,
                    ) from exc
                ctx.session.state["doc_search_retry_reason"] = str(exc)
                continue
            except AgentValidationFailure as exc:
                logger.warning(
                    "doc_search_orchestrator: validation failure on attempt %s/%s: %s",
                    attempt,
                    DOC_SEARCH_MAX_ATTEMPTS,
                    exc.validation_error,
                )
                last_validation_failure = exc
                if attempt >= DOC_SEARCH_MAX_ATTEMPTS:
                    raise
                ctx.session.state["doc_search_retry_reason"] = exc.validation_error
                continue

        if doc_search is None:
            if last_validation_failure is not None:
                raise last_validation_failure
            raise RuntimeError("doc_search_orchestrator finished without parsed result")

        logger.info(
            "doc_search_orchestrator: finished attempts=%s mode=%s relevant_docs=%s",
            attempts_used,
            doc_search.get("mode"),
            len(doc_search.get("results") or []),
        )

        # Получаем результат поиска из state

        # Если агент вернул не список, а какой-то особый режим (нет данных/сообщение)
        if doc_search["mode"] != "document_list":
            ctx.session.state["_root_final_text"] = format_text_answer(doc_search["message"])
            return

        # Нормализуем список: validate_doc_search_result уже отфильтровал is_relevant=true
        # и отсортировал по new_rank; rank в БД — порядковый номер после сортировки.
        results_raw = doc_search["results"]
        normalized: List[Dict[str, Any]] = []
        for i, item in enumerate(results_raw, start=1):
            normalized.append(
                {
                    "document_id": item["document_id"],       # ID документа
                    "source_name": item["source_name"],       # Название документа
                    "source_path": item.get("source_path"),   # Путь (может быть None)
                    "rank": i,                                # Место в списке
                }
            )

        shown = min(page, len(normalized))  # Число показанных пользователю документов

        # Основной хинт пользователю: список скоро появится в UI (строится из БД)
        ctx.session.state["_root_final_text"] = DOC_SEARCH_SUCCESS_HINT

        # Асинхронное сохранение результата поиска пользователя в БД
        await _persist_full_list(
            ctx,
            query=doc_search_query,
            items=normalized,
            shown_count=shown,
        )

def create_doc_search_orchestrator(
    doc_search_agent: LlmAgent,
    *,
    doc_collection: str = ACTIVE_DOCUMENTS_COLLECTION,
) -> DocSearchOrchestrator:
    """
    Фабрика для создания DocSearchOrchestrator с нужным агентом и коллекцией.
    :param doc_search_agent: Агент поиска документов (LLM+kb_search).
    :param doc_collection: Имя коллекции документов.
    :return: DocSearchOrchestrator
    """
    return DocSearchOrchestrator(
        doc_search_agent=doc_search_agent,
        doc_collection=doc_collection,
    )
