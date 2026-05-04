import json
import re
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from google.genai import types as genai_types
from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event, EventActions

from utils.logger import setup_logger
from utils.doc_search_format import extract_download_ranks
from .config import DEBUG_EXCEPTIONS, FAQ_DOCUMENTS_COLLECTION, KB_DOCUMENTS_COLLECTION
from .helpers import truncate_for_log, format_text_answer, format_reject_answer
from .json_leaf_runner import AgentValidationFailure, run_json_leaf_agent
from .agents.owasp_agent import validate_owasp_result
from .agents.dispatcher_agent import validate_dispatcher_result
from .agents.kb_answer_agent import validate_kb_answer_result
from .agents.doc_search_orchestrator import DocSearchOrchestrator

logger = setup_logger("root_agent", "agent.log")

OWASP_INVALID_CONTRACT_REASON = "invalid_contract"
OWASP_INVALID_CONTRACT_USER_MESSAGE = (
    "Извините, ваш запрос не может быть обработан. Пожалуйста, переформулируйте вопрос."
)

BOT_USER_PROFILE_MESSAGE_PREFIX = "Контекст пользователя:"
VALIDATION_ERROR_USER_MESSAGE = "Не удалось корректно обработать запрос. Попробуйте переформулировать вопрос."
OWASP_CONTEXT_WINDOW = 4
OWASP_HISTORY_STATE_KEY = "_owasp_recent_messages"


def is_bot_user_profile_injection_message(text: str) -> bool:
    t = (text or "").lstrip()
    return t.startswith(BOT_USER_PROFILE_MESSAGE_PREFIX)


class RootAgent(BaseAgent):
    """
    Оркестратор цепочки:
    owasp_agent -> dispatcher_agent -> (DocSearchOrchestrator | kb_answer_agent)
    """

    owasp_agent: LlmAgent
    dispatcher_agent: LlmAgent
    doc_search_orchestrator: DocSearchOrchestrator
    kb_answer_agent: LlmAgent
    faq_collection: str
    kb_collection: str

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        owasp_agent: LlmAgent,
        dispatcher_agent: LlmAgent,
        doc_search_orchestrator: DocSearchOrchestrator,
        kb_answer_agent: LlmAgent,
        faq_collection: str = FAQ_DOCUMENTS_COLLECTION,
        kb_collection: str = KB_DOCUMENTS_COLLECTION,
    ):
        super().__init__(
            name="root_agent",
            owasp_agent=owasp_agent,
            dispatcher_agent=dispatcher_agent,
            doc_search_orchestrator=doc_search_orchestrator,
            kb_answer_agent=kb_answer_agent,
            faq_collection=faq_collection,
            kb_collection=kb_collection,
            sub_agents=[
                owasp_agent,
                dispatcher_agent,
                doc_search_orchestrator,
                kb_answer_agent,
            ],
        )

    def _get_user_profile(self, ctx: InvocationContext) -> Dict[str, Any]:
        """
        Извлекает профиль пользователя:
        1) сначала из `ctx.user.state` как из основного хранилища;
        2) затем fallback из `ctx.session.state`, если профиль есть только в сессии.
        """
        profile: Dict[str, Any] = {}

        user_state = getattr(getattr(ctx, "user", None), "state", None) or {}
        session_state = getattr(getattr(ctx, "session", None), "state", None) or {}

        for key in (
            "first_name",
            "last_name",
            "full_name",
            "username",
            "region",
            "manager_group",
            "coach_group",
        ):
            value = user_state.get(key)
            if value in (None, ""):
                value = session_state.get(key)

            if value not in (None, ""):
                profile[key] = value

        return profile

    @staticmethod
    def _extract_user_text(ctx: InvocationContext) -> str:
        """
        Извлекает текст текущего пользовательского сообщения из `InvocationContext`.
        """
        user_content = getattr(ctx, "user_content", None)
        if user_content and getattr(user_content, "parts", None):
            out: List[str] = []
            for part in user_content.parts:
                text = getattr(part, "text", None)
                if text:
                    out.append(text)
            if out:
                return "\n".join(out).strip()

        return ""

    @staticmethod
    def _build_final_event(ctx: InvocationContext, text: str) -> Event:
        """Финальное событие root-агента."""
        return Event(
            author="root_agent",
            invocation_id=ctx.invocation_id,
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part(text=text)],
            ),
            actions=EventActions(end_of_agent=True),
        )

    def _build_final_event_with_history(
        self,
        ctx: InvocationContext,
        user_text: str,
        text: str,
    ) -> Event:
        """Формирует финальный ответ и обновляет bounded history текущего диалога."""
        self._append_recent_message(ctx, "user", user_text)
        self._append_recent_message(ctx, "assistant", text)
        return self._build_final_event(ctx, text)

    def _clear_state_keys(self, ctx: InvocationContext, keys: List[str]) -> None:
        """Очищает указанные ключи из `state`."""
        for key in keys:
            ctx.session.state.pop(key, None)

    def _get_recent_messages(self, ctx: InvocationContext) -> List[Dict[str, str]]:
        """Возвращает сохраненное ограниченное окно недавних сообщений."""
        value = ctx.session.state.get(OWASP_HISTORY_STATE_KEY)
        if not isinstance(value, list):
            return []

        items: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip()
            text = str(item.get("text") or "").strip()
            if role in {"user", "assistant"} and text:
                items.append({"role": role, "text": text})
        return items

    def _store_recent_messages(self, ctx: InvocationContext, messages: List[Dict[str, str]]) -> None:
        """Сохраняет ограниченное окно истории для bounded-context проверки."""
        ctx.session.state[OWASP_HISTORY_STATE_KEY] = messages[-OWASP_CONTEXT_WINDOW:]

    def _append_recent_message(self, ctx: InvocationContext, role: str, text: str) -> None:
        """Добавляет сообщение в bounded history, игнорируя пустые записи."""
        normalized_role = str(role or "").strip()
        normalized_text = str(text or "").strip()
        if normalized_role not in {"user", "assistant"} or not normalized_text:
            return

        history = self._get_recent_messages(ctx)
        history.append({"role": normalized_role, "text": normalized_text})
        self._store_recent_messages(ctx, history)

    def _prepare_owasp_input(self, ctx: InvocationContext, user_text: str) -> None:
        """
        Готовит bounded-context вход для `owasp_agent`.

        Основной сигнал — текущее сообщение пользователя.
        История передается только как ограниченное окно недавнего контекста.
        """
        recent_messages = self._get_recent_messages(ctx)
        ctx.session.state["owasp_current_user_message"] = user_text
        ctx.session.state["owasp_recent_messages_json"] = json.dumps(
            recent_messages,
            ensure_ascii=False,
        )

    @staticmethod
    def _pagination_intent_from_message(user_text: str) -> Optional[str]:
        """
        Распознает короткие команды пагинации без новой поисковой темы.
        Возвращает `show_more` или `show_all`, если сообщение похоже на команду
        продолжения уже показанного списка документов.
        """
        t = user_text.strip().lower().replace("ё", "е")
        t = re.sub(r"\s+", " ", t)
        if not t:
            return None
        if re.fullmatch(r"все[!?.]*", t):
            return "show_all"
        if re.fullmatch(r"(полностью|целиком)([!?.]*)", t):
            return "show_all"
        if re.fullmatch(r"all([!?.]*)", t):
            return "show_all"
        if re.fullmatch(r"(покажи|дай|выведи|открой)\s+все([!?.]*)", t):
            return "show_all"
        if re.fullmatch(r"(покажи|выведи)\s+полностью([!?.]*)", t):
            return "show_all"
        if re.fullmatch(r"(еще|больше|далее|следующие)([!?.]*)", t):
            return "show_more"
        if re.fullmatch(r"(еще)\s+(файлы|документы)([!?.]*)", t):
            return "show_more"
        if re.fullmatch(r"(next|more)([!?.]*)", t):
            return "show_more"
        return None

    def _get_required_state_dict(self, ctx: InvocationContext, key: str) -> Dict[str, Any]:
        """Получает обязательный `dict` из `state`."""
        value = ctx.session.state.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"State key '{key}' must be dict, got {type(value)}")
        return value

    def _get_required_state_text(self, ctx: InvocationContext, key: str) -> str:
        """Получает обязательную строку из `state`."""
        value = ctx.session.state.get(key)
        if not isinstance(value, str):
            raise ValueError(f"State key '{key}' must be str, got {type(value)}")
        return value

    async def _run_json_leaf_agent(
        self,
        ctx: InvocationContext,
        agent: LlmAgent,
        output_key: str,
        parsed_state_key: str,
        validator: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
        log_label: str,
        validation_error_user_message: str,
    ) -> AsyncGenerator[Event, None]:
        """Запускает leaf-агента с JSON-валидацией через `json_leaf_runner`."""
        async for event in run_json_leaf_agent(
            ctx=ctx,
            agent=agent,
            output_key=output_key,
            parsed_state_key=parsed_state_key,
            validator=validator,
            log_label=log_label,
            validation_error_user_message=validation_error_user_message,
        ):
            yield event

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        user_text = self._extract_user_text(ctx)
        logger.info("Processing message: %s", truncate_for_log(user_text, 200))

        try:
            if not user_text:
                yield self._build_final_event_with_history(
                    ctx,
                    user_text,
                    "Пустой запрос. Напишите сообщение еще раз.",
                )
                return

            # Синхронизация профиля из бота через AdkApiClient.set_user_state:
            # это не пользовательский запрос и цепочку агентов запускать не нужно.
            if is_bot_user_profile_injection_message(user_text):
                logger.info("Skipping agent chain (bot user profile sync, not a user turn)")
                yield self._build_final_event(ctx, "")
                return

            ctx.session.state["user_query"] = user_text
            self._prepare_owasp_input(ctx, user_text)
            self._clear_state_keys(
                ctx,
                [
                    "_owasp_result_parsed",
                    "_dispatcher_result_parsed",
                    "_doc_search_result_parsed",
                    "_kb_answer_result_parsed",
                    "_root_final_text",
                ],
            )

            async for event in self._run_json_leaf_agent(
                ctx=ctx,
                agent=self.owasp_agent,
                output_key="owasp_result_json",
                parsed_state_key="_owasp_result_parsed",
                validator=validate_owasp_result,
                log_label="owasp_result_json",
                validation_error_user_message=OWASP_INVALID_CONTRACT_USER_MESSAGE,
            ):
                yield event

            owasp = self._get_required_state_dict(ctx, "_owasp_result_parsed")
            logger.info("OWASP result: status=%s route=%s", owasp["status"], owasp["route"])

            if owasp["status"] == "blocked":
                yield self._build_final_event_with_history(
                    ctx,
                    user_text,
                    format_reject_answer(owasp["user_message"]),
                )
                return

            ranks = extract_download_ranks(user_text)
            if ranks:
                dispatch = validate_dispatcher_result(
                    {
                        "status": "ok",
                        "route": "doc_search",
                        "intent": "file_download",
                        "reason": "download_by_rank_short_circuit",
                        "search_query": "",
                    },
                    dict(ctx.session.state),
                )
                ctx.session.state["_dispatcher_result_parsed"] = dispatch
                ctx.session.state.pop("dispatcher_result_json", None)
                logger.info(
                    "Dispatcher skipped (file_download by rank): ranks=%s",
                    ranks,
                )
            else:
                ctx.session.state["dispatcher_user_query"] = user_text
                ctx.session.state.pop("dispatcher_result_json", None)
                
                async for event in self._run_json_leaf_agent(
                    ctx=ctx,
                    agent=self.dispatcher_agent,
                    output_key="dispatcher_result_json",
                    parsed_state_key="_dispatcher_result_parsed",
                    validator=validate_dispatcher_result,
                    log_label="dispatcher_result_json",
                    validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
                ):
                    yield event

                dispatch = self._get_required_state_dict(ctx, "_dispatcher_result_parsed")
                logger.info(
                    "Dispatcher result: route=%s intent=%s search_query=%s",
                    dispatch["route"],
                    dispatch["intent"],
                    dispatch["search_query"],
                )

                pin = self._pagination_intent_from_message(user_text)
                if pin and (
                    dispatch.get("route") != "doc_search" or dispatch.get("intent") != pin
                ):
                    dispatch = validate_dispatcher_result(
                        {
                            "status": "ok",
                            "route": "doc_search",
                            "intent": pin,
                            "reason": "pagination_override_saved_doc_list",
                            "search_query": "",
                        },
                        dict(ctx.session.state),
                    )
                    ctx.session.state["_dispatcher_result_parsed"] = dispatch
                    logger.info("Dispatcher pagination override: intent=%s", pin)

                if (
                    dispatch.get("route") == "doc_search"
                    and dispatch.get("intent") == "doc_search"
                ):
                    dr = extract_download_ranks(
                        user_text, str(dispatch.get("search_query") or "")
                    )
                    if dr:
                        dispatch = validate_dispatcher_result(
                            {
                                "status": "ok",
                                "route": "doc_search",
                                "intent": "file_download",
                                "reason": "download_ranks_override_after_dispatcher",
                                "search_query": "",
                            },
                            dict(ctx.session.state),
                        )
                        ctx.session.state["_dispatcher_result_parsed"] = dispatch
                        ctx.session.state.pop("dispatcher_result_json", None)
                        logger.info(
                            "Dispatcher doc_search->file_download override: ranks=%s",
                            dr,
                        )

            if dispatch["route"] == "doc_search":
                ctx.session.state["doc_search_intent"] = dispatch["intent"]
                ctx.session.state["doc_search_search_query"] = dispatch["search_query"]
                async for event in self.doc_search_orchestrator.run_async(ctx):
                    yield event
                final_text = self._get_required_state_text(ctx, "_root_final_text")
                yield self._build_final_event_with_history(ctx, user_text, final_text)
                return

            async for event in self._handle_kb_answer(
                ctx,
                user_text,
                dispatch["search_query"],
                dispatch["intent"],
            ):
                yield event
            final_text = self._get_required_state_text(ctx, "_root_final_text")
            yield self._build_final_event_with_history(ctx, user_text, final_text)

        except AgentValidationFailure as exc:
            logger.warning(
                "RootAgent stopped after validation failure: agent=%s error=%s raw=%s",
                exc.log_label,
                exc.validation_error,
                truncate_for_log(exc.raw, 500),
            )
            yield self._build_final_event_with_history(ctx, user_text, exc.user_message)

        except Exception as exc:
            logger.error("RootAgent failure: %s", exc, exc_info=True)
            message = (
                f"DEBUG: {type(exc).__name__}: {exc}"
                if DEBUG_EXCEPTIONS
                else "Произошла ошибка при обработке запроса. Попробуйте позже."
            )
            yield self._build_final_event_with_history(ctx, user_text, message)

    async def _handle_kb_answer(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
        intent: str,
    ) -> AsyncGenerator[Event, None]:
        """
        Запуск kb_answer_agent для FAQ/KB-ответа или smalltalk.

        Args:
            ctx: Контекст выполнения.
            user_message: Исходный вопрос пользователя.
            search_query: Нормализованный поисковый запрос.
            intent: Тип запроса (kb_answer, smalltalk).
        """
        effective_search_query = (search_query or user_message).strip()
        logger.info(
            "kb_answer route: query=%s intent=%s",
            truncate_for_log(effective_search_query, 300),
            intent,
        )

        # Передаем данные профиля и маршрутизации для kb_answer_agent.
        user_profile = self._get_user_profile(ctx)
        # Распаковываем все поля профиля в корневой state.
        for key, value in user_profile.items():
            ctx.session.state[key] = value
        ctx.session.state["search_query"] = effective_search_query
        ctx.session.state["faq_collection"] = self.faq_collection
        ctx.session.state["kb_answer_collection"] = self.kb_collection
        ctx.session.state["intent"] = intent

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.kb_answer_agent,
            output_key="kb_answer_result_json",
            parsed_state_key="_kb_answer_result_parsed",
            validator=validate_kb_answer_result,
            log_label="kb_answer_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
        ):
            yield event

        kb_answer = self._get_required_state_dict(ctx, "_kb_answer_result_parsed")
        ctx.session.state["_root_final_text"] = format_text_answer(kb_answer["message"])
