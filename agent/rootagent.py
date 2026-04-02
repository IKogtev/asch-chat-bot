import json
from typing import Any, AsyncGenerator, Callable, Dict, List

from google.genai import types as genai_types
from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event, EventActions

from utils.logger import setup_logger
from .config import (
    ACTIVE_DOCUMENTS_COLLECTION,
    KB_DOCUMENTS_COLLECTION,
    KB_TOP_K,
    DEBUG_EXCEPTIONS,
    KbSearchBackend,
)
from .helpers import (
    truncate_for_log,
    extract_json,
    format_text_answer,
    format_reject_answer,
    deduplicate_results,
)
from .agents.owasp_agent import validate_owasp_result
from .agents.dispatcher_agent import validate_dispatcher_result
from .agents.kb_answer_agent import validate_kb_answer_result

logger = setup_logger("root_agent", "agent.log")


class RootAgent(BaseAgent):
    """
    Оркестратор цепочки:
    owasp_agent -> dispatcher_agent -> (doc_search_agent | kb_answer_agent)
    """

    owasp_agent: LlmAgent
    dispatcher_agent: LlmAgent
    doc_search_agent: LlmAgent
    kb_answer_agent: LlmAgent
    kb_backend: Any
    doc_collection: str
    kb_collection: str
    top_k: int

    model_config = {"arbitrary_types_allowed": True}

    def __init__(
        self,
        *,
        owasp_agent: LlmAgent,
        dispatcher_agent: LlmAgent,
        doc_search_agent: LlmAgent,
        kb_answer_agent: LlmAgent,
        kb_backend: KbSearchBackend,
        doc_collection: str = ACTIVE_DOCUMENTS_COLLECTION,
        kb_collection: str = KB_DOCUMENTS_COLLECTION,
        top_k: int = KB_TOP_K,
    ):
        super().__init__(
            name="root_agent",
            owasp_agent=owasp_agent,
            dispatcher_agent=dispatcher_agent,
            doc_search_agent=doc_search_agent,
            kb_answer_agent=kb_answer_agent,
            kb_backend=kb_backend,
            doc_collection=doc_collection,
            kb_collection=kb_collection,
            top_k=top_k,
            sub_agents=[
                owasp_agent,
                dispatcher_agent,
                doc_search_agent,
                kb_answer_agent,
            ],
        )
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

    @staticmethod
    def _normalize_search_backend_results(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Нормализация результатов поиска из KB backend"""
        normalized = []
        for item in items:
            normalized.append({
                "title": item.get("title", "Без названия"),
                "content": item.get("content", ""),
                "score": item.get("score", 0.0),
                "metadata": item.get("metadata", {}),
            })
        return normalized

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
        """Запуск leaf-агента с JSON-валидацией"""
        async for event in agent.run_async(ctx):
            yield event
        
        raw = str(ctx.session.state.get(output_key) or "").strip()
        logger.debug("%s raw: %s", log_label, truncate_for_log(raw, 500))
        
        parsed = validator(extract_json(raw))
        ctx.session.state[parsed_state_key] = parsed
        logger.debug("%s parsed: %s", log_label, json.dumps(parsed, ensure_ascii=False))
        
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
                async for event in self._handle_doc_search(ctx, user_text, dispatch["search_query"]):
                    yield event
                final_text = self._get_required_state_text(ctx, "_root_final_text")
                yield self._build_final_event(ctx, final_text)
                return

            async for event in self._handle_kb_answer(ctx, user_text, dispatch["search_query"]):
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

    async def _handle_doc_search(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
    ) -> AsyncGenerator[Event, None]:
        logger.info("doc_search route: query=%s", truncate_for_log(search_query, 300))