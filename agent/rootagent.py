from typing import AsyncGenerator, Callable, Dict, List, Any

from google.genai import types as genai_types
from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event, EventActions

from utils.logger import setup_logger
from .config import KB_DOCUMENTS_COLLECTION, DEBUG_EXCEPTIONS
from .helpers import truncate_for_log, format_text_answer, format_reject_answer
from .json_leaf_runner import run_json_leaf_agent
from .agents.owasp_agent import validate_owasp_result
from .agents.dispatcher_agent import validate_dispatcher_result
from .agents.kb_answer_agent import validate_kb_answer_result
from .agents.doc_search_orchestrator import DocSearchOrchestrator

logger = setup_logger("root_agent", "agent.log")

class RootAgent(BaseAgent):
    """
    Оркестратор цепочки:
    owasp_agent -> dispatcher_agent -> (DocSearchOrchestrator | kb_answer_agent)
    """

    owasp_agent: LlmAgent
    dispatcher_agent: LlmAgent
    doc_search_orchestrator: DocSearchOrchestrator
    kb_answer_agent: LlmAgent
    kb_collection: str

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        owasp_agent: LlmAgent,
        dispatcher_agent: LlmAgent,
        doc_search_orchestrator: DocSearchOrchestrator,
        kb_answer_agent: LlmAgent,
        kb_collection: str = KB_DOCUMENTS_COLLECTION,
    ):
        super().__init__(
            name="root_agent",
            owasp_agent=owasp_agent,
            dispatcher_agent=dispatcher_agent,
            doc_search_orchestrator=doc_search_orchestrator,
            kb_answer_agent=kb_answer_agent,
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
        Извлекает профиль пользователя из ctx.user.state.
        """
        profile = {}
        if hasattr(ctx, "user") and hasattr(ctx.user, "state"):
            for key, value in ctx.user.state.items():
                profile[key] = value
        return profile

    @staticmethod
    def _extract_user_text(ctx: InvocationContext) -> str:
        """
        Извлекаем текст текущего пользовательского сообщения из InvocationContext.
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
        """Финальное событие root-агента"""
        return Event(
            author="root_agent",
            invocation_id=ctx.invocation_id,
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part(text=text)],
            ),
            actions=EventActions(end_of_agent=True),
        )

    def _clear_state_keys(self, ctx: InvocationContext, keys: List[str]) -> None:
        """Очистка указанных ключей из state"""
        for key in keys:
            ctx.session.state.pop(key, None)

    def _get_required_state_dict(self, ctx: InvocationContext, key: str) -> Dict[str, Any]:
        """Получение обязательного dict из state"""
        value = ctx.session.state.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"State key '{key}' must be dict, got {type(value)}")
        return value

    def _get_required_state_text(self, ctx: InvocationContext, key: str) -> str:
        """Получение обязательного текста из state"""
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
        validator: Callable[[Dict[str, Any]], Dict[str, Any]],
        log_label: str,
    ) -> AsyncGenerator[Event, None]:
        """Запуск leaf-агента с JSON-валидацией (делегирует json_leaf_runner)."""
        async for event in run_json_leaf_agent(
            ctx=ctx,
            agent=agent,
            output_key=output_key,
            parsed_state_key=parsed_state_key,
            validator=validator,
            log_label=log_label,
        ):
            yield event

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        user_text = self._extract_user_text(ctx)
        logger.info("Processing message: %s", truncate_for_log(user_text, 200))

        try:
            if not user_text:
                yield self._build_final_event(ctx, "Пустой запрос. Напишите сообщение ещё раз.")
                return

            ctx.session.state["user_query"] = user_text
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
            ):
                yield event

            owasp = self._get_required_state_dict(ctx, "_owasp_result_parsed")
            logger.info("OWASP result: status=%s route=%s", owasp["status"], owasp["route"])

            if owasp["status"] == "blocked":
                yield self._build_final_event(ctx, format_reject_answer(owasp["user_message"]))
                return

            async for event in self._run_json_leaf_agent(
                ctx=ctx,
                agent=self.dispatcher_agent,
                output_key="dispatcher_result_json",
                parsed_state_key="_dispatcher_result_parsed",
                validator=validate_dispatcher_result,
                log_label="dispatcher_result_json",
            ):
                yield event

            dispatch = self._get_required_state_dict(ctx, "_dispatcher_result_parsed")
            logger.info(
                "Dispatcher result: route=%s intent=%s search_query=%s",
                dispatch["route"],
                dispatch["intent"],
                dispatch["search_query"],
            )

            if dispatch["route"] == "doc_search":
                ctx.session.state["doc_search_intent"] = dispatch["intent"]
                ctx.session.state["doc_search_search_query"] = dispatch["search_query"]
                async for event in self.doc_search_orchestrator.run_async(ctx):
                    yield event
                final_text = self._get_required_state_text(ctx, "_root_final_text")
                yield self._build_final_event(ctx, final_text)
                return

            async for event in self._handle_kb_answer(
                ctx,
                user_text,
                dispatch["search_query"],
                dispatch["intent"],
            ):
                yield event
            final_text = self._get_required_state_text(ctx, "_root_final_text")
            yield self._build_final_event(ctx, final_text)

        except Exception as exc:
            logger.error("RootAgent failure: %s", exc, exc_info=True)
            message = (
                f"DEBUG: {type(exc).__name__}: {exc}"
                if DEBUG_EXCEPTIONS
                else "Произошла ошибка при обработке запроса. Попробуйте позже."
            )
            yield self._build_final_event(ctx, message)

    async def _handle_kb_answer(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
        intent: str,
    ) -> AsyncGenerator[Event, None]:
        """
        Запуск kb_answer_agent с прямым MCP-поиском или smalltalk.

        Args:
            ctx: Контекст выполнения
            user_message: Исходный вопрос пользователя
            search_query: Нормализованный поисковый запрос
            intent: Тип запроса (kb_answer, smalltalk)
        """
        effective_search_query = (search_query or user_message).strip()
        logger.info("kb_answer route: query=%s intent=%s", truncate_for_log(effective_search_query, 300), intent)

        # Переменные для промпта kb_answer_agent
        user_profile = self._get_user_profile(ctx)
        # Распаковываем все поля профиля в корень state
        for key, value in user_profile.items():
            ctx.session.state[key] = value
        ctx.session.state["search_query"] = effective_search_query
        ctx.session.state["kb_answer_collection"] = self.kb_collection
        ctx.session.state["intent"] = intent

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.kb_answer_agent,
            output_key="kb_answer_result_json",
            parsed_state_key="_kb_answer_result_parsed",
            validator=validate_kb_answer_result,
            log_label="kb_answer_result_json",
        ):
            yield event

        kb_answer = self._get_required_state_dict(ctx, "_kb_answer_result_parsed")
        ctx.session.state["_root_final_text"] = format_text_answer(kb_answer["message"])
