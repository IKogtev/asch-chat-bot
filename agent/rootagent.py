import json
import re
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from google.genai import types as genai_types
from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event, EventActions

from utils.logger import setup_logger
from utils.doc_search_format import extract_download_ranks
from .config import (
    AGENT_DIALOG_MEMORY_MAX_TURNS,
    DEBUG_EXCEPTIONS,
    FAQ_DOCUMENTS_COLLECTION,
    KB_DOCUMENTS_COLLECTION,
)
from .helpers import extract_json, truncate_for_log, format_text_answer, format_reject_answer
from .json_leaf_runner import AgentValidationFailure, run_json_leaf_agent
from .agents.owasp_agent import validate_owasp_result
from .agents.dispatcher_agent import validate_dispatcher_result
from .agents.kb_answer_agent import validate_kb_answer_result
from .agents.doc_search_orchestrator import DocSearchOrchestrator
from .agents.product_selection_agent import validate_product_selection_result
from .glossary import GlossaryLookup
from .product_resolver_service import ProductResolverService

logger = setup_logger("root_agent", "agent.log")

OWASP_INVALID_CONTRACT_REASON = "invalid_contract"
OWASP_INVALID_CONTRACT_USER_MESSAGE = (
    "Извините, ваш запрос не может быть обработан. Пожалуйста, переформулируйте вопрос."
)

BOT_USER_PROFILE_MESSAGE_PREFIX = "Контекст пользователя:"
VALIDATION_ERROR_USER_MESSAGE = "Не удалось корректно обработать запрос. Попробуйте переформулировать вопрос."
OWASP_CONTEXT_WINDOW = 4
OWASP_HISTORY_STATE_KEY = "_owasp_recent_messages"
PRODUCT_DIALOG_CONTEXT_STATE_KEY = "_product_dialog_context"
PRODUCT_FILTER_FOLLOWUP_QUESTION = (
    "Могу показать карточку продукта или скачать комплект. Какой продукт Вас интересует ?"
)


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
    product_selection_agent: LlmAgent
    glossary_lookup: GlossaryLookup
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
        product_selection_agent: LlmAgent,
        glossary_lookup: GlossaryLookup | None = None,
        faq_collection: str = FAQ_DOCUMENTS_COLLECTION,
        kb_collection: str = KB_DOCUMENTS_COLLECTION,
    ):
        super().__init__(
            name="root_agent",
            owasp_agent=owasp_agent,
            dispatcher_agent=dispatcher_agent,
            doc_search_orchestrator=doc_search_orchestrator,
            kb_answer_agent=kb_answer_agent,
            product_selection_agent=product_selection_agent,
            glossary_lookup=glossary_lookup or GlossaryLookup(),
            faq_collection=faq_collection,
            kb_collection=kb_collection,
            sub_agents=[
                owasp_agent,
                dispatcher_agent,
                doc_search_orchestrator,
                kb_answer_agent,
                product_selection_agent,
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
        state_delta: Dict[str, Any] = {}
        session_state = getattr(getattr(ctx, "session", None), "state", None) or {}
        bot_action = session_state.get("_bot_action")
        if isinstance(bot_action, dict) and bot_action.get("type"):
            state_delta["_bot_action"] = bot_action
        product_dialog_context = session_state.get(PRODUCT_DIALOG_CONTEXT_STATE_KEY)
        if isinstance(product_dialog_context, dict):
            state_delta[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = product_dialog_context

        actions = EventActions(end_of_agent=True)
        actions.state_delta = state_delta

        return Event(
            author="root_agent",
            invocation_id=ctx.invocation_id,
            content=genai_types.Content(
                role="model",
                parts=[genai_types.Part(text=text)],
            ),
            actions=actions,
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

    @staticmethod
    def _is_dialog_memory_event(event: Any) -> bool:
        content = getattr(event, "content", None)
        role = getattr(content, "role", None)
        if role not in {"user", "model"}:
            return False

        parts = getattr(content, "parts", None) or []
        return any(str(getattr(part, "text", "") or "").strip() for part in parts)

    @staticmethod
    def _retained_dialog_memory_events(events: List[Any], max_turns: int) -> List[Any]:
        if max_turns <= 0:
            return list(events)

        user_turn_indices = [
            idx
            for idx, event in enumerate(events)
            if RootAgent._is_dialog_memory_event(event)
            and getattr(getattr(event, "content", None), "role", None) == "user"
        ]
        if len(user_turn_indices) <= max_turns:
            return list(events)

        cutoff_idx = user_turn_indices[-max_turns]
        return list(events[cutoff_idx:])

    async def _trim_dialog_memory(self, ctx: InvocationContext) -> None:
        events = list(getattr(ctx.session, "events", None) or [])
        retained_events = self._retained_dialog_memory_events(
            events,
            AGENT_DIALOG_MEMORY_MAX_TURNS,
        )
        if len(retained_events) == len(events):
            return

        ctx.session.events = retained_events

        logger.info(
            "Dialog memory trimmed in current invocation: kept_events=%s removed_events=%s max_turns=%s",
            len(retained_events),
            len(events) - len(retained_events),
            AGENT_DIALOG_MEMORY_MAX_TURNS,
        )

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

    @staticmethod
    def _format_clarification_option(option: Any) -> str:
        if isinstance(option, dict):
            name = str(option.get("name") or "").strip()
            option_code = str(option.get("code") or "").strip()
            term = str(option.get("term") or "").strip()
            currency = str(option.get("currency") or "").strip()
            details = [item for item in (term, currency) if item]
            if option_code and name:
                label = f"{option_code} {name}"
            else:
                label = option_code or name
            if details:
                label = f"{label} - {', '.join(details)}"
            return label.strip()

        return str(option or "").strip()

    @classmethod
    def _format_product_selection_answer(cls, product_selection: Dict[str, Any]) -> str:
        message = format_text_answer(product_selection["message"])
        if product_selection.get("mode") == "product_filter":
            if PRODUCT_FILTER_FOLLOWUP_QUESTION not in message:
                message = "\n\n".join([message, PRODUCT_FILTER_FOLLOWUP_QUESTION])
            return message

        if product_selection.get("mode") != "needs_clarification":
            return message

        options = [
            cls._format_clarification_option(option)
            for option in product_selection.get("clarification_options") or []
        ]
        options = [option for option in options if option]
        if not options:
            return message

        return "\n".join([message, *options])

    @staticmethod
    def _normalize_product_dialog_text(text: str) -> str:
        value = str(text or "").lower().replace("ё", "е")
        return re.sub(r"\s+", " ", value).strip()

    @staticmethod
    def _extract_product_codes(text: str) -> List[str]:
        return re.findall(r"\b\d{3,}(?:\+\d{3,})?\b", text or "")

    @staticmethod
    def _normalize_dialog_products(value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []

        products: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            product: Dict[str, str] = {}
            for key in ("code", "name", "term", "currency", "folder_kit"):
                text = str(item.get(key) or "").strip()
                if text:
                    product[key] = text
            if product.get("code") or product.get("name"):
                products.append(product)
        return products

    def _get_product_dialog_context(self, ctx: InvocationContext) -> Dict[str, Any]:
        value = ctx.session.state.get(PRODUCT_DIALOG_CONTEXT_STATE_KEY)
        return value if isinstance(value, dict) else {}

    def _clear_product_dialog_context(self, ctx: InvocationContext) -> None:
        ctx.session.state.pop(PRODUCT_DIALOG_CONTEXT_STATE_KEY, None)

    def _store_product_dialog_context(
        self,
        ctx: InvocationContext,
        product_selection: Dict[str, Any],
    ) -> None:
        mode = product_selection.get("mode")
        if mode == "product_filter":
            products = self._normalize_dialog_products(product_selection.get("products"))
            if products:
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": "product_filter",
                    "products": products,
                    "selected_product": None,
                }
            else:
                self._clear_product_dialog_context(ctx)
            return

        if mode == "product_card":
            resolved_product = product_selection.get("resolved_product")
            products = self._normalize_dialog_products([resolved_product])
            if products:
                previous = self._get_product_dialog_context(ctx)
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": "product_card",
                    "products": previous.get("products") or products,
                    "selected_product": products[0],
                }
            return

        if mode == "product_kit":
            resolved_product = product_selection.get("resolved_product")
            products = self._normalize_dialog_products([resolved_product])
            if products:
                previous = self._get_product_dialog_context(ctx)
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": "product_kit",
                    "products": previous.get("products") or products,
                    "selected_product": products[0],
                }
            return

        if mode in {"no_data", "product_compare"}:
            self._clear_product_dialog_context(ctx)

    def _find_product_in_dialog_context(
        self,
        ctx: InvocationContext,
        user_text: str,
        *,
        allow_selected_product: bool,
    ) -> Dict[str, str] | None:
        context = self._get_product_dialog_context(ctx)
        products = self._normalize_dialog_products(context.get("products"))
        codes = set(self._extract_product_codes(user_text))

        if codes:
            for product in products:
                if product.get("code") in codes:
                    return product

        normalized = self._normalize_product_dialog_text(user_text)
        matches = []
        for product in products:
            name = self._normalize_product_dialog_text(product.get("name", ""))
            if name and normalized and (normalized == name or normalized in name):
                matches.append(product)

        if len(matches) == 1:
            return matches[0]

        selected = context.get("selected_product")
        if allow_selected_product and isinstance(selected, dict):
            products = self._normalize_dialog_products([selected])
            if products:
                return products[0]

        return None

    def _product_followup_dispatch(self, ctx: InvocationContext, user_text: str) -> Dict[str, Any] | None:
        normalized = self._normalize_product_dialog_text(user_text)
        if not normalized or not self._get_product_dialog_context(ctx):
            return None

        asks_kit = bool(
            re.search(
                r"\b(скач|пришл|отправ|дай|дать|комплект|материал|документ)",
                normalized,
            )
        )
        asks_card = bool(
            re.search(
                r"\b(параметр|карточк|свойств|характеристик|подробн|покаж|расскаж)",
                normalized,
            )
        )
        if not asks_kit and not asks_card:
            return None

        product = self._find_product_in_dialog_context(
            ctx,
            user_text,
            allow_selected_product=asks_kit and not self._extract_product_codes(user_text),
        )
        if not product:
            return None

        code = product.get("code") or ""
        name = product.get("name") or ""
        if asks_kit:
            intent = "product_kit"
            query = f"скачать комплект документов по продукту {code or name}".strip()
        else:
            intent = "product_card"
            query = f"показать параметры продукта {code or name}".strip()

        return validate_dispatcher_result(
            {
                "status": "ok",
                "route": "product_selection",
                "intent": intent,
                "reason": "product_dialog_followup",
                "search_query": query,
            },
            dict(ctx.session.state),
        )

    @classmethod
    def _fallback_product_selection_message(cls, raw: str) -> str | None:
        try:
            payload = extract_json(raw)
        except Exception:
            return None

        message = str(payload.get("message") or "").strip()
        if not message:
            return None

        return cls._format_product_selection_answer(
            {
                "mode": payload.get("mode"),
                "message": message,
                "clarification_options": payload.get("clarification_options") or [],
            }
        )

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
            await self._trim_dialog_memory(ctx)

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
                    "_product_selection_result_parsed",
                    "_root_final_text",
                    "_bot_action",
                    "from_glossary",
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

            ctx.session.state["from_glossary"] = await self.glossary_lookup.find(user_text)
            logger.info(
                "Glossary terms found: %s",
                len(ctx.session.state["from_glossary"]),
            )

            dispatch = self._product_followup_dispatch(ctx, user_text)
            if dispatch:
                ctx.session.state["_dispatcher_result_parsed"] = dispatch
                ctx.session.state.pop("dispatcher_result_json", None)
                logger.info(
                    "Dispatcher skipped (product dialog follow-up): intent=%s search_query=%s",
                    dispatch["intent"],
                    dispatch["search_query"],
                )
            else:
                ranks = extract_download_ranks(user_text)
            if not dispatch and ranks:
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
            elif not dispatch:
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
                    dr = extract_download_ranks(user_text)

            if dispatch["route"] == "doc_search":
                ctx.session.state["doc_search_intent"] = dispatch["intent"]
                async for event in self.doc_search_orchestrator.run_async(ctx):
                    yield event
                final_text = self._get_required_state_text(ctx, "_root_final_text")
                yield self._build_final_event_with_history(ctx, user_text, final_text)
                return

            if dispatch["route"] == "product_selection":
                async for event in self._handle_product_selection(
                    ctx,
                    user_text,
                    dispatch["search_query"],
                    dispatch["intent"],
                ):
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
            product_selection_tool_usage_failure = (
                exc.log_label == "product_selection_result_json"
                and "tool_usage" in exc.validation_error
            )
            fallback_message = (
                self._fallback_product_selection_message(exc.raw)
                if (
                    exc.log_label == "product_selection_result_json"
                    and not product_selection_tool_usage_failure
                )
                else None
            )
            if exc.log_label == "product_selection_result_json":
                try:
                    payload = extract_json(exc.raw)
                except Exception:
                    payload = {}
                logger.debug(
                    "product_selection fallback diagnostics: fallback_used=%s "
                    "blocked_by_tool_usage=%s mode=%s resolved_product=%s "
                    "clarification_options_count=%s message_preview=%s",
                    bool(fallback_message),
                    product_selection_tool_usage_failure,
                    payload.get("mode"),
                    payload.get("resolved_product"),
                    len(payload.get("clarification_options") or []),
                    truncate_for_log(payload.get("message"), 300),
                )
            yield self._build_final_event_with_history(
                ctx,
                user_text,
                fallback_message or exc.user_message,
            )

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

    async def _handle_product_selection(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
        intent: str,
    ) -> AsyncGenerator[Event, None]:
        effective_search_query = (search_query or user_message).strip()
        logger.info(
            "product_selection route: query=%s intent=%s",
            truncate_for_log(effective_search_query, 300),
            intent,
        )

        user_profile = self._get_user_profile(ctx)
        for key, value in user_profile.items():
            ctx.session.state[key] = value
        ctx.session.state["product_selection_intent"] = intent
        ctx.session.state["product_selection_search_query"] = effective_search_query

        # добавляем resolve продукта
        resolver = ProductResolverService()

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.product_selection_agent,
            output_key="product_selection_result_json",
            parsed_state_key="_product_selection_result_parsed",
            validator=validate_product_selection_result,
            log_label="product_selection_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
        ):
            yield event

        product_selection = self._get_required_state_dict(ctx, "_product_selection_result_parsed")
        logger.debug(
            "product_selection parsed summary: user_query=%s search_query=%s intent=%s "
            "mode=%s resolved_product=%s clarification_options_count=%s used_tables=%s",
            truncate_for_log(user_message, 300),
            truncate_for_log(effective_search_query, 300),
            intent,
            product_selection.get("mode"),
            product_selection.get("resolved_product"),
            len(product_selection.get("clarification_options") or []),
            product_selection.get("used_tables"),
        )
        ctx.session.state["_root_final_text"] = self._format_product_selection_answer(
            product_selection
        )
        self._store_product_dialog_context(ctx, product_selection)

        if product_selection["mode"] == "product_kit":
            resolved_product = product_selection.get("resolved_product") or {}
            product_code = str(resolved_product.get("code") or "").strip()
            product_name = str(resolved_product.get("name") or "").strip()
            folder_kit = str(resolved_product.get("folder_kit") or "").strip()
            if product_code:
                ctx.session.state["_bot_action"] = {
                    "type": "send_product_kit",
                    "product_code": product_code,
                    "product_name": product_name,
                    "folder_kit": folder_kit,
                }
                return

        ctx.session.state.pop("_bot_action", None)
