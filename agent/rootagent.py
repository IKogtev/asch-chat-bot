import json
import re
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, ClassVar

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
    DATABASE_URL,
    COMPARE_FRAZE,
    PRODUCT_CARD_KIT_OFFER
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
from .smart_fallback import generate_agent_fallback
from collections import OrderedDict, deque
import asyncpg

logger = setup_logger("root_agent", "agent.log")

OWASP_INVALID_CONTRACT_REASON = "invalid_contract"
OWASP_INVALID_CONTRACT_USER_MESSAGE = (
    "Извините, ваш запрос не может быть обработан. Пожалуйста, переформулируйте вопрос."
)

BOT_USER_PROFILE_MESSAGE_PREFIX = "Контекст пользователя:"
VALIDATION_ERROR_USER_MESSAGE = "Не удалось корректно обработать запрос. Попробуйте переформулировать вопрос."
RECOVERY_MESSAGE = (
    "Я не смог корректно обработать запрос.\n\n"
    "Попробуйте:\n"
    "• уточнить формулировку вопроса;\n"
    "• задать вопрос другими словами;\n"
    "• использовать /reset если диалог зашел в тупик;\n"
    "• подождать и задать вопрос позже"
)
VALIDATION_ERROR_USER_MESSAGE = RECOVERY_MESSAGE
OWASP_CONTEXT_WINDOW = 4
OWASP_HISTORY_STATE_KEY = "_owasp_recent_messages"
PRODUCT_DIALOG_CONTEXT_STATE_KEY = "_product_dialog_context"
PRODUCT_FILTER_FOLLOWUP_QUESTION = (
    "Могу показать карточку продукта или скачать комплект. Какой продукт Вас интересует ?"
)
PRODUCT_ATTRIBUTE_FOLLOWUP_QUESTION = (
    "Могу показать продукты с этими свойствами. Какое свойство вас интересует ?"
)
DOC_LIST_FOLLOWUP_INTENTS = frozenset({"file_download", "show_more", "show_all"})
DOC_LIST_FOLLOWUP_INTENTS = frozenset({"file_download", "show_more", "show_all"})

def is_bot_user_profile_injection_message(text: str) -> bool:
    t = (text or "").lstrip()
    return t.startswith(BOT_USER_PROFILE_MESSAGE_PREFIX)

async def is_history_empty_by_global_id(global_user_id: str) -> bool:
    """Одним запросом находит platform_user_id по UUID в user_accounts 
    и проверяет, пуста ли его история в chat_history."""
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        # Вложенный запрос: извлекаем platform_user_id по UUID и проверяем историю
        # cast (::bigint) нужен, чтобы типы точно совпали с числовым user_id в chat_history
        count = await conn.fetchval("""
            SELECT COUNT(*) 
            FROM chat_history 
            WHERE user_id = (
                SELECT platform_user_id::bigint 
                FROM user_accounts 
                WHERE user_id = $1
            );
        """, global_user_id)
        
        await conn.close()
        return count == 0  # Если 0, значит история пуста (был /reset)
    except Exception as e:
        logger.error(f"Ошибка проверки существующей таблицы истории: {e}")
        return False
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
    product_resolver: ProductResolverService
    faq_collection: str
    kb_collection: str

    model_config = {"arbitrary_types_allowed": True}
    MAX_HISTORY_PER_USER: ClassVar[int] = 3  # Сколько последних запросов хранить для ОДНОГО пользователя
    # Глобальный кэш для сохранения контекста при 409 Conflict (сплите сессий)
    # Ключом будет базовый session_id, значением — словарь с контекстом
    # Глобальное хранилище: { clean_id: deque([state1, state2, ...]) }
    _CROSS_SESSION_CACHE: ClassVar[OrderedDict] = OrderedDict()

    def __init__(
        self,
        *,
        owasp_agent: LlmAgent,
        dispatcher_agent: LlmAgent,
        doc_search_orchestrator: DocSearchOrchestrator,
        kb_answer_agent: LlmAgent,
        product_selection_agent: LlmAgent,
        glossary_lookup: GlossaryLookup | None = None,
        product_resolver: ProductResolverService | None = None,
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
            product_resolver=product_resolver or ProductResolverService(),
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

        # Сохраняем last_* ключи для контекста между сессиями
        for key in [
            "last_user_query",
            "last_route",
            "last_intent",
            "last_search_query",
            "last_product",
            "last_document_list",
        ]:
            value = session_state.get(key)
            if value is not None:
                state_delta[key] = value
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
        # Достаем результат работы диспетчера из состояния текущего шага
        dispatch = ctx.session.state.get("_dispatcher_result_parsed")
        
        if isinstance(dispatch, dict):
            ctx.session.state["last_user_query"] = user_text
            ctx.session.state["last_route"] = dispatch.get("route", "")
            ctx.session.state["last_intent"] = dispatch.get("intent", "")
            ctx.session.state["last_search_query"] = dispatch.get("search_query", "")
            
            # Автоматически управляем списком документов
            if dispatch.get("route") == "doc_search":
                intent = str(dispatch.get("intent") or "")
                if intent in DOC_LIST_FOLLOWUP_INTENTS:
                    pass
                elif intent == "doc_search" and text.strip():
                    ctx.session.state["last_document_list"] = text[:1500]
                    # Автоматическое сохранение контекста продукта из найденных документов ---
                    codes = self._extract_product_codes(text)
                    first_code = codes[0] if codes else ""
                    name_guess = ""
                    # 1. Пытаемся вытащить имя продукта из строки ответа, если есть код
                    if first_code:
                        match_name = re.search(r"(?:^|\d+\.\s*)([^\n()]+)\s*\(" + re.escape(first_code) + r"\)", text)
                        if match_name:
                            name_guess = match_name.group(1).strip()
                            # Очищаем от процентов доходности в хвосте, если они прилипли
                            name_guess = re.sub(r"\s+\d+([.,]\d+)?%\s*$", "", name_guess).strip()
                    
                    # Fallback: если в тексте ответа нет кодов, берем название из search_query или user_text
                    if not name_guess:
                        sq = dispatch.get("search_query", "").strip()
                        # Если search_query содержит что-то осмысленное (не просто общие слова)
                        if sq and len(sq) > 2 and not re.fullmatch(r"(?i)(документы|файлы|материалы|список)", sq):
                            name_guess = sq
                        elif user_text:
                            name_guess = re.sub(
                                r"(?i)^(найди|покажи|выведи|открой|документы|доки|по|для|скачать|файл|файлы|материалы|презентацию|презентер|памятку|инструкцию|регламент|шаблон|список)\s+", 
                                "", 
                                user_text
                            ).strip()
                            name_guess = re.sub(r"(?i)\bпо\b\s*", "", name_guess).strip()
                    # Если имя удалось определить, сохраняем контекст
                    if name_guess:
                        # Записываем в плоскую строку для _get_last_product_from_state
                        if first_code:
                            ctx.session.state["last_product"] = f"{name_guess} (код {first_code})".strip()
                        else:
                            ctx.session.state["last_product"] = name_guess.strip()
                            
                        # Записываем в структурированный контекст для _get_selected_product_from_context
                        current_context = self._get_product_dialog_context(ctx) or {}
                        current_context["last_mode"] = "product_card"
                        selected_prod = {"name": name_guess}
                        if first_code:
                            selected_prod["code"] = first_code
                        current_context["selected_product"] = selected_prod
                        current_context["products"] = [selected_prod]
                        ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = current_context
                        logger.info("Auto-saved product context from doc_search: code=%s, name=%s", first_code, name_guess)
                else:
                    ctx.session.state["last_document_list"] = ""
            else:
                ctx.session.state["last_document_list"] = ""
    
            logger.debug(
                "Context auto-updated inside final event: route=%s, intent=%s, search_query=%s",
                ctx.session.state["last_route"],
                ctx.session.state["last_intent"],
                ctx.session.state["last_search_query"]
            )
        # БЛОК СОХРАНЕНИЯ В КЭШ ДЛЯ ПОДСТРАХОВКИ СЛЕДУЮЩИХ ШАГОВ ---
        sess_id = getattr(ctx.session, "id", "")
        clean_id = sess_id.split("_")[0] if sess_id else ""
        if clean_id:
            # Собираем текущий снимок состояния
            current_state = {
                "last_user_query": ctx.session.state.get("last_user_query"),
                "last_route": ctx.session.state.get("last_route"),
                "last_intent": ctx.session.state.get("last_intent"),
                "last_search_query": ctx.session.state.get("last_search_query"),
                "last_document_list": ctx.session.state.get("last_document_list"),
                "last_product": ctx.session.state.get("last_product"),
                "_product_dialog_context": ctx.session.state.get("_product_dialog_context"),
            }

            # 1. Если пользователя еще нет в кэше — создаем для него личную очередь
            if clean_id not in self._CROSS_SESSION_CACHE:
                # Инициализируем деку с жестким редактируемым лимитом размера
                self._CROSS_SESSION_CACHE[clean_id] = deque(maxlen=self.MAX_HISTORY_PER_USER)
            
            # 2. Добавляем текущее состояние в деку пользователя. 
            # Благодаря maxlen, если там уже было 3 записи, самая старая удалится автоматически!
            self._CROSS_SESSION_CACHE[clean_id].append(current_state)
            
            # 3. Передвигаем пользователя в конец OrderedDict, так как он совершил действие (LRU-логика)
            self._CROSS_SESSION_CACHE.move_to_end(clean_id)
            
            logger.debug(
                "State saved for user %s. User history size: %d/%d. Total users in cache: %d",
                clean_id,
                len(self._CROSS_SESSION_CACHE[clean_id]),
                self.MAX_HISTORY_PER_USER,
                len(self._CROSS_SESSION_CACHE)
            )

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

    def _reset_turn_state(self, ctx: InvocationContext) -> None:
        """Сбрасывает служебное состояние перед новым пользовательским сообщением."""
        self._clear_state_keys(
            ctx,
            [
                "user_query",
                "search_query",
                "faq_collection",
                "kb_answer_collection",
                "intent",
                "dispatcher_user_query",
                "doc_search_query",
                "doc_search_intent",
                "product_selection_search_query",
                "product_selection_intent",
                "from_glossary",
                "_owasp_result_parsed",
                "_dispatcher_result_parsed",
                "_doc_search_result_parsed",
                "_kb_answer_result_parsed",
                "_product_selection_result_parsed",
                "_root_final_text",
                "_bot_action",
                "product_resolution",
                "product_resolutions",
                "product_filter_resolution",
                "owasp_current_user_message",
                "owasp_recent_messages_json",
            ],
        )
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
        mode = product_selection.get("mode")
        if mode == "product_filter":
            if PRODUCT_FILTER_FOLLOWUP_QUESTION not in message:
                message = "\n\n".join([message, PRODUCT_FILTER_FOLLOWUP_QUESTION])
            return message

        if mode == "product_attribute_values":
            if PRODUCT_ATTRIBUTE_FOLLOWUP_QUESTION not in message:
                message = "\n\n".join([message, PRODUCT_ATTRIBUTE_FOLLOWUP_QUESTION])
            return message

        if mode == "product_card":
            # Добавляем предложение, только если агент сам его ещё не добавил
            message_lower = message.lower()
            if "комплект документов" not in message_lower and "скачать комплект" not in message_lower:
                message = message + f"\n\n {PRODUCT_CARD_KIT_OFFER}"
            return message
        if mode == "needs_clarification":
            
            options = [
                cls._format_clarification_option(option)
                for option in product_selection.get("clarification_options") or []
            ]
            options = [option for option in options if option]
            if not options:
                return message

            return_message = "\n".join([message, *options])
            # if COMPARE_FRAZE not in return_message:
            #     return_message += f"\n\n {COMPARE_FRAZE}"
            return return_message

        return message


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

    @classmethod
    def clear_user_cache(cls, user_id: str) -> None:
        """Полностью удаляет RAM-историю запросов конкретного пользователя"""
        if user_id in cls._CROSS_SESSION_CACHE:
            cls._CROSS_SESSION_CACHE.pop(user_id, None)
            logger.info(f"Cross-session FIFO cache successfully cleared for user: {user_id}")

    @staticmethod
    def _normalize_attribute_values(value: Any) -> List[str]:
        if not isinstance(value, list):
            return []

        values: List[str] = []
        seen: set[str] = set()
        for item in value:
            text = str(item or "").strip()
            key = RootAgent._normalize_product_dialog_text(text)
            if text and key and key not in seen:
                values.append(text)
                seen.add(key)
        return values

    def _store_product_dialog_context(
        self,
        ctx: InvocationContext,
        product_selection: Dict[str, Any],
    ) -> None:
        mode = product_selection.get("mode")
        if mode == "product_attribute_values":
            attribute_values = self._normalize_attribute_values(
                product_selection.get("attribute_values")
            )
            if attribute_values:
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": "product_attribute_values",
                    "attribute_name": str(product_selection.get("attribute_name") or "").strip(),
                    "attribute_column": str(product_selection.get("attribute_column") or "").strip(),
                    "attribute_values": attribute_values,
                    "products": [],
                    "selected_product": None,
                }
            else:
                self._clear_product_dialog_context(ctx)
            return

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

        if mode == "no_data":
            self._clear_product_dialog_context(ctx)
            return
            
        if mode == "product_compare":
            resolved_product = product_selection.get("resolved_product")
            products = self._normalize_dialog_products(product_selection.get("products") or [])
            previous = self._get_product_dialog_context(ctx)
            
            # Если агент явно выбрал один продукт (например, ответил на вопрос "где меньше рисков")
            if resolved_product and (resolved_product.get("code") or resolved_product.get("name")):
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": "product_compare",
                    "products": products or previous.get("products") or [],
                    "selected_product": resolved_product,
                }
            else:
                # Иначе просто сохраняем список продуктов для дальнейшего сравнения
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": "product_compare",
                    "products": products or previous.get("products") or [],
                    "selected_product": previous.get("selected_product"),
                }
            return

    def _is_blind_followup(self, user_text: str) -> bool:
        """
        Проверяет, является ли сообщение слепым follow-up (только триггеры/местоимения)
        или пользователь явно указал продукт (код/название).
        Возвращает True, если это слепой follow-up (контекст нужен).
        """
        explicit_codes = self._extract_product_codes(user_text)
        if explicit_codes:
            return False
            
        normalized = self._normalize_product_dialog_text(user_text)
        
        # Удаляем триггеры, местоимения и предлоги
        blind_triggers = r"\b(скач\w*|пришл\w*|отправ\w*|дай|дать|комплект\w*|материал\w*|документ\w*|давай|ок|хорошо|ладно|параметр\w*|карточк\w*|свойств\w*|характеристик\w*|подробн\w*|покаж\w*|расскаж\w*|презентац\w*|презентер\w*|памятк\w*|инструкц\w*|регламент\w*|шаблон\w*|пф|полис\w*|договор\w*|буклет\w*|нем|о\s+нем|ней|о\s+ней|этом|об\s+этом|программе|продукт\w*|программа|его|ее|них|покажи|выведи|открой|найди|скинь|кидай|хочу|пакет\s+документов|пакет\s+материалов|комплект\s+документов|полный\s+комплект|нужен|скачать|скинь|пришли|отправь|про|по|для|на|в|во|с|со|к|ко|о|об|и|а|но|да|же|бы|ли)\b"
        clean_msg = re.sub(blind_triggers, "", normalized)
        clean_msg = re.sub(r"[^\w\s]", "", clean_msg).strip()
        
        # Если после очистки что-то осталось (например, "фн", "зк 2 года") - это явный запрос
        return len(clean_msg) == 0

    def _find_attribute_value_in_dialog_context(
        self,
        ctx: InvocationContext,
        user_text: str,
    ) -> str | None:
        context = self._get_product_dialog_context(ctx)
        if context.get("last_mode") != "product_attribute_values":
            return None

        normalized = self._normalize_product_dialog_text(user_text)
        if not normalized:
            return None

        values = self._normalize_attribute_values(context.get("attribute_values"))
        exact_matches = [
            value
            for value in values
            if self._normalize_product_dialog_text(value) == normalized
        ]
        if len(exact_matches) == 1:
            return exact_matches[0]

        contained_matches = [
            value
            for value in values
            if self._normalize_product_dialog_text(value)
            and self._normalize_product_dialog_text(value) in normalized
        ]
        if len(contained_matches) == 1:
            return contained_matches[0]

        return None

    def _get_selected_product_from_context(self, ctx: InvocationContext) -> Dict[str, str] | None:
        """Надёжно извлекает selected_product из контекста диалога."""
        context = self._get_product_dialog_context(ctx)
        selected = context.get("selected_product")
        if isinstance(selected, dict) and (selected.get("code") or selected.get("name")):
            logger.debug("Fallback to selected_product from context: %s", selected)
            return selected
        return None
    
    def _get_last_product_from_state(self, ctx: InvocationContext) -> Dict[str, str] | None:
        """Извлекает последний продукт из state (last_product)."""
        last_product = ctx.session.state.get("last_product")
        logger.debug("DEBUG _get_last_product_from_state: last_product=%r", last_product)
        if not last_product:
            logger.debug("DEBUG: last_product is empty or None")
            return None
        
        # last_product имеет формат "Fort Knox 1 год (код 8914)"
        match = re.search(r"\(код (\d+)\)", last_product)
        if match:
            code = match.group(1)
            name = last_product.split("(код")[0].strip()
            logger.debug("Extracted last_product from state: code=%s name=%s", code, name)
            return {"code": code, "name": name}
        
        # Если формат другой, пробуем извлечь код
        codes = self._extract_product_codes(last_product)
        if codes:
            logger.debug("Extracted code from last_product: %s", codes[0])
            return {"code": codes[0], "name": last_product}
        # Если кода нет, но строка не пустая, возвращаем её как имя продукта
        logger.debug("Extracted name-only last_product from state: name=%s", last_product)
        return {"code": "", "name": last_product.strip()}

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

    @staticmethod
    def _product_resolution_to_state(value: Any) -> Dict[str, Any]:
        if hasattr(value, "to_dict"):
            data = value.to_dict()
            return data if isinstance(data, dict) else {}
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _product_resolutions_to_state(value: Any) -> Dict[str, Any]:
        if hasattr(value, "to_dict"):
            data = value.to_dict()
        elif isinstance(value, dict):
            data = value
        else:
            return {}

        if not isinstance(data, dict):
            return {}

        items = data.get("items")
        if not isinstance(items, list):
            return data

        unique_items = []
        seen_keys = set()
        for item in items:
            if not isinstance(item, dict):
                unique_items.append(item)
                continue

            dedup_key = RootAgent._product_resolution_dedup_key(item)
            if dedup_key is not None:
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)

            unique_items.append(item)

        return {**data, "items": unique_items}

    @staticmethod
    def _product_resolution_dedup_key(item: Dict[str, Any]) -> tuple[str, str] | None:
        product_code = str(item.get("product_code", "")).strip()
        if not product_code:
            return None

        name = str(
            item.get("product_name")
            or item.get("canonical_name")
            or ""
        ).strip()

        options = item.get("options")
        if not name and isinstance(options, list) and options:
            first_option = options[0]
            if isinstance(first_option, dict):
                name = str(
                    first_option.get("canonical_name")
                    or first_option.get("alias")
                    or ""
                ).strip()

        normalized_name = " ".join(name.casefold().split())
        return product_code, normalized_name

    @staticmethod
    def _product_filter_resolution_to_state(value: Any) -> Dict[str, Any]:
        if hasattr(value, "to_dict"):
            data = value.to_dict()
        elif isinstance(value, dict):
            data = value
        else:
            return {}

        if not isinstance(data, dict):
            return {}

        return {
            "status": data.get("status"),
            "query": data.get("query"),
            "product_codes": data.get("product_codes") or [],
            "matched_terms": data.get("matched_terms") or [],
            "error": data.get("error"),
        }

    async def _prepare_product_resolution_state(
        self,
        ctx: InvocationContext,
        query: str,
        intent: str,
    ) -> None:
        ctx.session.state["product_resolution"] = {}
        ctx.session.state["product_resolutions"] = {}
        ctx.session.state["product_filter_resolution"] = {}

        if intent == "product_filter":
            result = await self.product_resolver.resolve_product_filter(query)
            ctx.session.state["product_filter_resolution"] = self._product_filter_resolution_to_state(
                result
            )
            logger.debug(
                "product_filter_resolution state: %s",
                ctx.session.state["product_filter_resolution"],
            )
            return

        if intent == "product_compare":
            result = await self.product_resolver.resolve_products(query)
            ctx.session.state["product_resolutions"] = self._product_resolutions_to_state(
                result
            )
            logger.debug(
                "product_resolutions state: %s",
                ctx.session.state["product_resolutions"],
            )
            return

        if intent in {"product_card", "product_kit"}:
            result = await self.product_resolver.resolve_product(query)
            ctx.session.state["product_resolution"] = self._product_resolution_to_state(
                result
            )
            logger.debug(
                "product_resolution state: %s",
                ctx.session.state["product_resolution"],
            )
    
    def _is_contextual_product_request(self, text: str) -> bool:
        """Проверяет, является ли запрос ссылкой на контекстный продукт."""
        normalized = text.lower().strip()
        triggers = ["о нем", "про него", "расскажи", "покажи", "параметры", "подробнее", "характеристики"]
        # Если запрос короткий и содержит маркеры контекста
        return len(normalized) < 30 and any(t in normalized for t in triggers)
    
    def _get_explicit_intent_dispatch(self, ctx: InvocationContext, user_text: str) -> Dict[str, Any] | None:
        """
        Перехватывает явные запросы на комплект или фильтр до вызова LLM-dispatcher.
        """
        normalized = self._normalize_product_dialog_text(user_text)
        if not normalized:
            return None

        # 1. Явный запрос комплекта/документов (но не "какие документы есть" -> это фильтр)
        is_explicit_kit = bool(
            re.search(
                r"\b(пакет документов|пакет материалов|полный комплект|все материалы|комплект|пакет)\b",
                normalized,
            )
        )
        is_asking_list = bool(re.search(r"\b(какие|что за|список|покажи список|есть ли)\b", normalized))
        
        if is_explicit_kit and not is_asking_list:
            # ЕСЛИ ПОЛЬЗОВАТЕЛЬ ЯВНО УКАЗАЛ НОВЫЙ ПРОДУКТ, ОТДАЕМ ДИСПЕТЧЕРУ
            if not self._is_blind_followup(user_text):
                return None
            # ИСПРАВЛЕНИЕ: Извлекаем продукт из контекста диалога (selected_product)
            product = self._find_product_in_dialog_context(
                ctx,
                user_text,
                allow_selected_product=True,
            )
            logger.debug("DEBUG: _find_product_in_dialog_context returned: %s", product)
            
            if not product:
                product = self._get_selected_product_from_context(ctx)
                logger.debug("DEBUG: _get_selected_product_from_context returned: %s", product)
            
            if not product:
              product = self._get_last_product_from_state(ctx)
              logger.debug("DEBUG: _get_last_product_from_state returned: %s", product)

            if product:
                code = product.get("code") or ""
                name = product.get("name") or ""
                query = f"скачать комплект документов по продукту {code or name}".strip()
                logger.info("Explicit kit dispatch: found product code=%s name=%s", code, name)
            else:
                logger.warning("Explicit kit dispatch: product NOT found in context, using raw query")
                query = user_text

            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "product_selection",
                    "intent": "product_kit",
                    "reason": "explicit_kit_short_circuit",
                    "search_query": query, 
                },
                dict(ctx.session.state),
            )

        # 2. Явный запрос списка/архива/фильтра
        if re.search(r"\b(архивные|все продукты|список продуктов|покажи продукты|покажи архивные)\b", normalized):
            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "product_selection",
                    "intent": "product_filter",
                    "reason": "explicit_filter_short_circuit",
                    "search_query": user_text,
                },
                dict(ctx.session.state),
            )
        # 3. Перехват контекстного согласия ("давай", "пришли", "отправь") сразу после показа карточки продукта
        last_route = ctx.session.state.get("last_route")
        last_intent = ctx.session.state.get("last_intent")
        is_confirmation = normalized in [
            "давай", "да", "давайте", "пришли", "отправь", "скинь", "кидай", 
            "хочу", "ок", "хорошо", "давай комплект", "пришли комплект"
        ]
        if last_route == "product_selection" and last_intent == "product_card" and is_confirmation:
            product = self._find_product_in_dialog_context(ctx, user_text, allow_selected_product=True)
            if not product:
                product = self._get_selected_product_from_context(ctx)
            if not product:
                product = self._get_last_product_from_state(ctx)

            if product:
                code = product.get("code") or ""
                name = product.get("name") or ""
                query = f"скачать комплект документов по продукту {code or name}".strip()
                logger.info("Explicit confirmation kit dispatch short-circuit: found product code=%s name=%s", code, name)
                return validate_dispatcher_result(
                    {
                        "status": "ok",
                        "route": "product_selection",
                        "intent": "product_kit",
                        "reason": "explicit_confirmation_kit_short_circuit",
                        "search_query": query, 
                    },
                    dict(ctx.session.state),
                )
            
        return None

    def _product_followup_dispatch(self, ctx: InvocationContext, user_text: str) -> Dict[str, Any] | None:
        normalized = self._normalize_product_dialog_text(user_text)
        if not normalized:
            return None
        context = self._get_product_dialog_context(ctx)
        # Блок поиска по атрибутам и кодам (требует обязательного наличия RAM-контекста)
        if context:
            attribute_value = self._find_attribute_value_in_dialog_context(ctx, user_text)
            if attribute_value:
                attribute_name = str(context.get("attribute_name") or "").strip()
                attribute_column = str(context.get("attribute_column") or "").strip()
                attribute_label = attribute_name or attribute_column or "selected attribute"
                query = f"покажи продукты, у которых {attribute_label}: {attribute_value}"
                return validate_dispatcher_result(
                    {
                        "status": "ok",
                        "route": "product_selection",
                        "intent": "product_filter",
                        "reason": "product_attribute_value_followup",
                        "search_query": query,
                    },
                    dict(ctx.session.state),
                )
            
            codes = self._extract_product_codes(user_text)
            if len(codes) == 1 and normalized == codes[0].lower():
                product = self._find_product_in_dialog_context(
                    ctx,
                    user_text,
                    allow_selected_product=False,
                )
                if product:
                    ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                        **context,
                        "selected_product": product,
                    }
                    code = product.get("code") or codes[0]
                    return validate_dispatcher_result(
                        {
                            "status": "ok",
                            "route": "product_selection",
                            "intent": "product_card",
                            "reason": "product_code_followup",
                            "search_query": f"показать карточку продукта {code}",
                        },
                        dict(ctx.session.state),
                    )
        # Логика отправки комплекта или открытия карточки (работает в т.ч. по flat-стейту last_product)
        asks_kit = bool(
            re.search(
                r"\b(скач|пришл|отправ|дай|дать|комплект|пакет|материал|документ|давай|ок|хорошо|ладно)\b",
                normalized,
            )
        )
        asks_card = bool(
            re.search(
                r"\b(параметр|карточк|свойств|характеристик|подробн|покаж|расскаж)\b",
                normalized,
            )
        )
        asks_doc = bool(
            re.search(
                r"\b(презентац|презентер|памятк|инструкц|регламент|шаблон|пф|полис|договор|буклет)\b",
                normalized,
            )
        )
        # Не перехватываем общие запросы списков ("какие есть презентации", "покажи все документы")
        is_asking_general_list = bool(re.search(r"\b(какие есть|список|покажи список|все документы|все файлы)\b", normalized))
        if asks_doc and is_asking_general_list:
            asks_doc = False
        # Дополнительная подстраховка: если это триггер согласия после просмотра карточки
        last_route = ctx.session.state.get("last_route")
        last_intent = ctx.session.state.get("last_intent")
        if last_route == "product_selection" and last_intent == "product_card":
            if normalized in ["давай", "да", "давайте", "пришли", "отправь", "скинь", "кидай", "хочу", "ок", "хорошо"]:
                asks_kit = True

        if not asks_kit and not asks_card and not asks_doc:
            return None
        # Если это не слепой follow-up (пользователь явно указал продукт),
        # отдаем запрос диспетчеру, чтобы он не подменял его старым контекстом
        if not self._is_blind_followup(user_text):
            return None
        # 1. Сначала пытаемся найти продукт стандартным путем через RAM-контекст модулей
        product = None
        if context:
            product = self._find_product_in_dialog_context(
                ctx,
                user_text,
                allow_selected_product=(
                    (asks_kit or asks_card or asks_doc)
                    and not self._extract_product_codes(user_text)
                ),
            )
            if not product:
                product = self._get_selected_product_from_context(ctx)
        
        if not product:
            product = self._get_last_product_from_state(ctx)
        # 2. ФОЛЛБЭК: Если продукт все еще не найден
        search_target = ""
        if not product:
            logger.info("Context is empty after split. Starting cascade history extraction...")
            # Если пользователь явно назвал продукт в текущем сообщении, мы должны использовать его,
            # а не падать в историю. Чистим текущий запрос от триггеров запроса документов/комплекта.
            current_query_clean = re.sub(
                r"(?i)^(дай|дать|покажи|скачать|скинь|пришли|отправь|найди)\s+",
                "",
                user_text
            ).strip()
            current_query_clean = re.sub(
                r"(?i)\b(документы|доки|комплект|материалы|файлы|пакет|полный)\b",
                "",
                current_query_clean
            ).strip()
            current_query_clean = re.sub(r"^\s*(по|для)\s+", "", current_query_clean).strip()
            
            # Проверяем, что после очистки не осталось просто местоимение или пустота
            pronouns_and_empty = {"", "нем", "нём", "ней", "них", "это", "этот", "о нем", "о нём", "о ней", "про него"}
            if current_query_clean.lower() not in pronouns_and_empty and len(current_query_clean) > 1:
                search_target = current_query_clean
                logger.info(f"Fallback Stage 1 (Current Query Priority): Extracted target '{search_target}' from current user_text")
            # А. Проверяем существующую переменную last_product (строка формата "Имя (код ХХХХ)")
            if not search_target:
                last_product_str = ctx.session.state.get("last_product")
                if last_product_str and isinstance(last_product_str, str):
                    logger.info(f"Fallback stage A: analyzing last_product content: '{last_product_str}'")
                    extracted_codes = self._extract_product_codes(last_product_str)
                    if extracted_codes:
                        search_target = extracted_codes[0]
                        logger.info(f"Fallback Stage A: Found explicit code {search_target} in last_product")
                    else:
                        match_name = re.search(r"^([^(]+)", last_product_str)
                        if match_name:
                            search_target = match_name.group(1).strip()
                            logger.info(f"Fallback Stage A: Found name '{search_target}' in last_product")
               
            # Шаг Б: Если Шаг А не дал результатов, парсим last_document_list (текст ответа док-серча)
            if not search_target and last_route == "doc_search":
                last_doc_list = ctx.session.state.get("last_document_list")
                if last_doc_list:
                    extracted_codes = self._extract_product_codes(last_doc_list)
                    if extracted_codes:
                        search_target = extracted_codes[0]
                        logger.info(f"Fallback Stage B: Extracted code {search_target} from last_document_list")
                        
            # Шаг В: Если кодов нигде нет, вытаскиваем продукт из самого ПРЕДЫДУЩЕГО ЗАПРОСА пользователя
            if not search_target:
                last_user_query = ctx.session.state.get("last_user_query")
                if last_user_query:
                    # Чистим запрос от префиксов, чтобы вычленить "Fort Knox 1 год" целиком
                    clean_query = re.sub(
                        r"(?i)^(найди|покажи|документы|доки|по|для|скачать|файл|файлы|продукт|карточку|информацию|расскажи|про)\s+", 
                        "", 
                        last_user_query
                    ).strip()
                    # Фильтр вопросов: не позволяем извлекать вопросы и сравнения как название продукта
                    question_markers = [
                        "отличаются", "сравнить", "сравни", "какой", "что", "как", 
                        "почему", "где", "когда", "чем", "них", "него", "они"
                    ]
                    if not any(marker in clean_query.lower() for marker in question_markers):
                        if clean_query:
                            search_target = clean_query
                            logger.info(f"Fallback Stage C (Last User Query): Extracted clean query target '{search_target}' from last_user_query")
                    else:
                        logger.info(f"Fallback Stage C: Skipped question/comparison query '{clean_query}'")
        else:     
            code = product.get("code") or ""
            name = product.get("name") or ""
            logger.info("Product followup dispatch: found product code=%s name=%s", code, name)
            search_target = code if code else name
        # Если в итоге мы смогли определить цель поиска
        if search_target:
            if asks_doc:
                query = f"{user_text} по продукту {search_target}".strip()
                # Убираем дублирование, если пользователь уже написал "по продукту"
                query = re.sub(r"по продукту\s+по продукту", "по продукту", query, flags=re.IGNORECASE)
                
                logger.info(f"Doc search followup short-circuit triggered. Target: {search_target}, Route: doc_search")
                return validate_dispatcher_result(
                    {
                        "status": "ok",
                        "route": "doc_search",
                        "intent": "doc_search",
                        "reason": "product_context_doc_search_followup",
                        "search_query": query,
                    },
                    dict(ctx.session.state),
                )
            intent = "product_kit" if asks_kit else "product_card"
            if intent == "product_kit":
                query = f"скачать комплект документов по продукту {search_target}".strip()
            else:
                query = f"показать параметры продукта {search_target}".strip()

            logger.info(f"Product followup short-circuit triggered. Target: {search_target}, Route: product_selection, Intent: {intent}")
            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "product_selection",
                    "intent": intent,
                    "reason": "product_dialog_history_cascade_fallback",
                    "search_query": query,
                },
                dict(ctx.session.state),
            )

        logger.warning("Product followup dispatch: Product target could not be restored from history.")
        return None

    def _has_doc_list_followup_context(self, ctx: InvocationContext) -> bool:
        """True, если предыдущий ход был связан с выдачей списка документов."""
        if ctx.session.state.get("last_route") != "doc_search":
            return False
        return bool(str(ctx.session.state.get("last_document_list") or "").strip())

    def _doc_list_followup_dispatch(
        self,
        ctx: InvocationContext,
        user_text: str,
    ) -> Dict[str, Any] | None:
        """
        Follow-up к сохранённому списку документов: пагинация (ещё / все) или
        скачивание по номеру (1, 1,3, первый и т.п.).
        """
        if not self._has_doc_list_followup_context(ctx):
            return None

        pin = self._pagination_intent_from_message(user_text)
        if pin:
            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "doc_search",
                    "intent": pin,
                    "reason": f"doc_list_followup_{pin}",
                    "search_query": "",
                },
                dict(ctx.session.state),
            )

        ranks = self._extract_ranks_with_words(user_text)
        if not ranks:
            return None

        return validate_dispatcher_result(
            {
                "status": "ok",
                "route": "doc_search",
                "intent": "file_download",
                "reason": "doc_list_followup_download_by_rank",
                "search_query": "",
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

    def _extract_ranks_with_words(self, text: str) -> List[int]:
        ranks = extract_download_ranks(text)
        if ranks:
            return ranks
        text_lower = text.lower()
        mapping = {
            "первый": 1, "первую": 1, "первым": 1, "первого": 1,
            "второй": 2, "вторую": 2, "вторым": 2, "второго": 2,
            "третий": 3, "третью": 3, "третьим": 3, "третьего": 3,
        }
        for word, rank in mapping.items():
            if re.search(rf"\b{word}\b", text_lower):
                return [rank]
        return []

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
        # БЛОК АВТОМАТИЧЕСКОГО ВОССТАНОВЛЕНИЯ КОНТЕКСТА (ЗАЩИТА ОТ 409 CONFLICT) ---
        sess_id = getattr(ctx.session, "id", "")
        clean_id = sess_id.split("_")[0] if sess_id else ""
        if clean_id:
            # Проверяем существующую БД: если там пусто, значит бот стёр историю через /reset
            if await is_history_empty_by_global_id(clean_id):  # или is_context_cleared_in_db
                self._CROSS_SESSION_CACHE.pop(clean_id, None)
                logger.info(f"🧹 [RAM Cache] Локальная память агента очищена, так как в БД история пуста для {clean_id}")
            elif clean_id in self._CROSS_SESSION_CACHE:
                # Если в новой сессии пропали ключевые данные контекста, восстанавливаем их из кэша
                if not ctx.session.state.get("last_product") and not ctx.session.state.get("_product_dialog_context"):
                    user_history = self._CROSS_SESSION_CACHE[clean_id]
                    
                    if user_history:  # Если у этого юзера есть сохраненные шаги
                        logger.info(
                            "Session split (409). Restoring context from the latest request of user: %s", 
                            clean_id
                        )
                        # Элемент [-1] в deque — это самый свежий добавленный запрос этого пользователя
                        latest_cached_state = user_history[-1]
                        for key, value in latest_cached_state.items():
                            ctx.session.state[key] = value
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
            # Инициализируем переменные контекста, если их нет в state
            for key in [
                "last_user_query", 
                "last_route", 
                "last_intent", 
                "last_search_query",
                "last_product",
                "last_document_list"
            ]:
                if key not in ctx.session.state:
                    ctx.session.state[key] = ""
            # Сбрасываем служебное состояние текущего шага
            self._reset_turn_state(ctx)
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
                    "_from_glossary",
                    "doc_search_query",
                    "product_resolution",
                    "product_resolutions",
                    "product_filter_resolution",
                ],
            )
            # Проверка безопасности (OWASP)
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
                # Используем _build_final_event, чтобы НЕ сохранять заблокированное 
                # сообщение в историю OWASP и не загрязнять контекст для следующих запросов
                yield self._build_final_event(
                    ctx,
                    format_reject_answer(owasp["user_message"]),
                )
                return

            ctx.session.state["from_glossary"] = await self.glossary_lookup.find(user_text)
            logger.info(
                "Glossary terms found: %s",
                len(ctx.session.state["from_glossary"]),
            )
            # 1. Пытаемся перехватить явные интенты (Комплект, Архивные) без LLM
            dispatch = self._get_explicit_intent_dispatch(ctx, user_text)

            # 2. Product follow-up по _product_dialog_context / last_product
            if not dispatch:
                dispatch = self._product_followup_dispatch(ctx, user_text)

            # 3. Doc list follow-up: пагинация и скачивание при last_route=doc_search
            if not dispatch:
                dispatch = self._doc_list_followup_dispatch(ctx, user_text)

            if dispatch:
                ctx.session.state["_dispatcher_result_parsed"] = dispatch
                ctx.session.state.pop("dispatcher_result_json", None)
                logger.info(
                    "Dispatcher skipped (short-circuit): reason=%s intent=%s search_query=%s",
                    dispatch.get("reason"),
                    dispatch["intent"],
                    dispatch["search_query"],
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
            # Сохраняем контекст текущего хода для следующих реплик
            ctx.session.state["last_user_query"] = user_text
            ctx.session.state["last_route"] = dispatch["route"]
            ctx.session.state["last_intent"] = dispatch["intent"]
            # не затираем last_search_query пустой строкой при follow-up
            new_search_query = dispatch.get("search_query", "")
            if new_search_query:
                ctx.session.state["last_search_query"] = new_search_query

            if dispatch["route"] == "doc_search":
                async for event in self._handle_doc_search(
                    ctx,
                    user_text,
                    dispatch["intent"],
                    dispatch.get("search_query", ""),
                ):
                    yield event
                final_text = self._get_required_state_text(ctx, "_root_final_text")
                # Сохраняем список документов в state для контекста диспетчера
                yield self._build_final_event_with_history(ctx, user_text, final_text)
                return

            if dispatch["route"] == "product_selection":
                # СЛОЙ ПЕРЕХВАТА МЕСТОИМЕНИЙ И ОБОГАЩЕНИЯ КОНТЕКСТА ---
                sq_clean = dispatch.get("search_query", "").strip().lower()
                # Обогощение запроса для сравнения (product_compare) <<<
                if dispatch.get("intent") == "product_compare":
                    context = self._get_product_dialog_context(ctx)
                    products = self._normalize_dialog_products(context.get("products") or [])
                    
                    # Если в контексте есть список продуктов (например, после product_filter)
                    if len(products) >= 2:
                        # Проверяем, упомянул ли пользователь конкретные продукты в запросе явно
                        mentioned = False
                        for p in products:
                            code = p.get("code", "")
                            name = p.get("name", "").lower()
                            if (code and code in sq_clean) or (name and name in sq_clean):
                                mentioned = True
                                break
                        
                        # Если продукты из контекста не упомянуты, проверяем, не является ли это запросом на сравнение НОВЫХ продуктов
                        if not mentioned:
                            has_explicit_codes = bool(self._extract_product_codes(user_text))
                            # Паттерн для "слепого" follow-up (например, "сравни их", "чем они отличаются")
                            blind_pattern = r"(сравни|сравнить|чем\s+отличаются|в\s+чем\s+разница|какие\s+различия|их|эти|эти\s+продукты|два\s+продукта|оба|давай\s+сравним|давайте\s+сравним|сравни\s+их|сравнить\s+их)[\s?!.]*"
                            # Передаем flags=re.IGNORECASE
                            is_blind_followup = bool(re.fullmatch(blind_pattern, user_text.strip(), flags=re.IGNORECASE)) or (not has_explicit_codes and len(user_text.split()) <= 3)
                            if not is_blind_followup:
                                # Пользователь явно указал новые продукты для сравнения, не подменяем запрос
                                logger.info("Skipping product_compare enrichment: user specified new products.")
                            else:
                                names = [p.get("name") or p.get("code") for p in products[:2]]
                                dispatch["search_query"] = f"сравнить {' и '.join(names)}"
                                sq_clean = dispatch["search_query"].strip().lower()
                                ctx.session.state["last_search_query"] = dispatch["search_query"]
                                logger.info(f"Enriched product_compare search_query to: {dispatch['search_query']}")
                pronoun_triggers = [
                    "нем", "о нем", "ней", "о ней", "этом", "об этом", 
                    "программе", "продукт", "продукте", "программа", "подробнее", "о нем подробнее"
                ]
                if sq_clean in pronoun_triggers or not sq_clean:
                    product = self._get_selected_product_from_context(ctx)
                    if not product:
                        product = self._get_last_product_from_state(ctx)
                    
                    if product:
                        code = product.get("code") or ""
                        name = product.get("name") or ""
                        # Переопределяем абстрактное "нем" на жесткий поисковый запрос для агента продуктов
                        dispatch["search_query"] = f"продукт {code or name}".strip()
                        ctx.session.state["last_search_query"] = dispatch["search_query"]
                        logger.info("Enriched product_selection pronoun search_query to: %s", dispatch["search_query"])
                
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
            # По умолчанию уходим в базу знаний (kb_answer)
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
            # Определяем, какой агент работал
            agent_name = None
            if "product_selection" in exc.log_label:
                agent_name = "product_selection"
            elif "kb_answer" in exc.log_label:
                agent_name = "kb_answer"
            elif "dispatcher" in exc.log_label:
                agent_name = "dispatcher"
            elif "doc_search" in exc.log_label:
                agent_name = "doc_search"

            # Собираем контекст из состояния
            context: Dict[str, Any] = {
                "validation_error": exc.validation_error,
            }
            
            # Поисковый запрос — в разных ключах для разных агентов
            context["search_query"] = (
                ctx.session.state.get("product_selection_search_query")
                or ctx.session.state.get("doc_search_query")
                or ctx.session.state.get("search_query")
                or ctx.session.state.get("dispatcher_user_query")
                or ""
            )
            # Специфичные данные для каждого агента
            if agent_name == "product_selection":
                parsed = ctx.session.state.get("_product_selection_result_parsed") or {}
                context["used_tables"] = parsed.get("used_tables") or []
                context["mode"] = parsed.get("mode", "")
                context["resolved_product"] = parsed.get("resolved_product")
                context["clarification_options"] = parsed.get("clarification_options") or []
                context["products"] = parsed.get("products") or []
            
            elif agent_name == "doc_search":
                parsed = ctx.session.state.get("_doc_search_result_parsed") or {}
                context["mode"] = parsed.get("mode", "")
                context["results_count"] = len(parsed.get("results") or [])
                context["source"] = "kb_search"
            
            elif agent_name == "kb_answer":
                parsed = ctx.session.state.get("_kb_answer_result_parsed") or {}
                context["mode"] = parsed.get("mode", "")
                context["source"] = parsed.get("source", "")
            
            elif agent_name == "dispatcher":
                parsed = ctx.session.state.get("_dispatcher_result_parsed") or {}
                context["route"] = parsed.get("route", "")
                context["intent"] = parsed.get("intent", "")

            # Пытаемся извлечь данные из сырого ответа
            payload = {}
            if exc.log_label == "product_selection_result_json":
                try:
                    payload = extract_json(exc.raw)
                    # Дополняем контекст данными из payload (они могут быть свежее state)
                    context.setdefault("resolved_product", payload.get("resolved_product"))
                    context.setdefault(
                        "clarification_options",
                        payload.get("clarification_options") or [],
                    )
                    context.setdefault("mode", payload.get("mode", ""))
                    context.setdefault("used_tables", payload.get("used_tables") or [])
                except Exception:
                    pass
            
            # Пытаемся извлечь сообщение из сырого ответа 
            product_selection_tool_usage_failure = (
                exc.log_label == "product_selection_result_json"
                and "tool_usage" in exc.validation_error
            )
            legacy_message = (
                self._fallback_product_selection_message(exc.raw)
                if (
                    exc.log_label == "product_selection_result_json"
                    and not product_selection_tool_usage_failure
                )
                else None
            )
            # Приоритет 2: Умный fallback
            smart_message = None
            if not legacy_message:
                smart_message = generate_agent_fallback(
                    user_text=user_text,
                    error_type="validation_failure",
                    agent_name=agent_name,
                    context=context,
                )
            final_fallback_message = legacy_message or smart_message or exc.user_message
            if exc.log_label == "product_selection_result_json":
                logger.debug(
                    "product_selection fallback diagnostics: legacy_used=%s smart_used=%s "
                    "blocked_by_tool_usage=%s mode=%s resolved_product=%s "
                    "clarification_options_count=%s message_preview=%s",
                    bool(legacy_message),
                    bool(smart_message),
                    product_selection_tool_usage_failure,
                    payload.get("mode"),
                    payload.get("resolved_product"),
                    len(payload.get("clarification_options") or []),
                    truncate_for_log(payload.get("message"), 300),
                )
            else:
                logger.debug(
                    "agent fallback diagnostics: agent=%s smart_used=%s "
                    "search_query=%s validation_error=%s",
                    agent_name,
                    bool(smart_message),
                    truncate_for_log(context.get("search_query"), 100),
                    truncate_for_log(context.get("validation_error"), 200),
                )
            yield self._build_final_event_with_history(
                ctx,
                user_text,
                final_fallback_message,
            )

        except Exception as exc:
            logger.error("RootAgent failure: %s", exc, exc_info=True)
            message = (
                f"DEBUG: {type(exc).__name__}: {exc}"
                if DEBUG_EXCEPTIONS
                # fallback при нескольких сообщениях подряд
                else RECOVERY_MESSAGE
            )
            yield self._build_final_event_with_history(ctx, user_text, message)

    async def _handle_doc_search(
        self,
        ctx: InvocationContext,
        user_message: str,
        intent: str,
        search_query: str = "",
    ) -> AsyncGenerator[Event, None]:
        
        if intent == "file_download":
            ranks = self._extract_ranks_with_words(user_message)
            if ranks:
                ctx.session.state["_bot_action"] = {
                    "type": "download_by_ranks",
                    "ranks": ranks,
                }
                ctx.session.state["_root_final_text"] = ""
                logger.info(
                    "doc_search file_download: bot_action download_by_ranks ranks=%s",
                    ranks,
                )
                return
            base_query = search_query if search_query else user_message
            doc_search_query = await self.glossary_lookup.build_doc_search_query(base_query)
        elif intent in ("show_more", "show_all"):
            ctx.session.state["_bot_action"] = {
                "type": "show_doc_list_more" if intent == "show_more" else "show_doc_list_all",
            }
            ctx.session.state["_root_final_text"] = ""
            logger.info("doc_search %s: bot_action %s", intent, ctx.session.state["_bot_action"]["type"])
            return
        else:
            # ИСПОЛЬЗУЕМ search_query от диспетчера/follow-up, если он есть, иначе fallback на user_message
            base_query = search_query if search_query else user_message
            doc_search_query = await self.glossary_lookup.build_doc_search_query(base_query)
            
        logger.info(
            "doc_search route: query=%s intent=%s",
            truncate_for_log(doc_search_query, 300),
            intent,
        )

        ctx.session.state["doc_search_intent"] = intent
        ctx.session.state["doc_search_query"] = doc_search_query

        async for event in self.doc_search_orchestrator.run_async(ctx):
            yield event

    async def _handle_kb_answer(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
        intent: str,
    ) -> AsyncGenerator[Event, None]:
        self._clear_product_dialog_context(ctx)
        """
        Запуск kb_answer_agent для FAQ/KB-ответа или smalltalk.

        Args:
            ctx: Контекст выполнения.
            user_message: Исходный вопрос пользователя.
            search_query: Нормализованный поисковый запрос.
            intent: Тип запроса (kb_answer, smalltalk).
        """
        base_search_query = (search_query or user_message).strip()
        effective_search_query = await self.glossary_lookup.expand_search_query(
            base_search_query,
        )
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
        base_search_query = (search_query or user_message).strip()
        effective_search_query = await self.glossary_lookup.expand_search_query(
            base_search_query,
        )
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
        await self._prepare_product_resolution_state(ctx, effective_search_query, intent)
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
        # Если агент не вернул resolved_product, но в тексте ответа явно выделил один продукт 
        if product_selection.get("mode") == "product_compare" and not product_selection.get("resolved_product"):
            context = self._get_product_dialog_context(ctx)
            products = self._normalize_dialog_products(context.get("products") or [])
            message_text = str(product_selection.get("message") or "").lower()
            
            if products and message_text:
                found_products = []
                for p in products:
                    name = p.get("name", "").lower()
                    if name and name in message_text:
                        found_products.append(p)
                
                if len(found_products) == 1:
                    product_selection["resolved_product"] = found_products[0]
                    logger.info(f"Super-fallback for product_compare: extracted resolved_product from message text: {found_products[0]}")
                elif len(found_products) > 1:
                    # Ищем маркер выбора ("меньше", "лучше", "выгоднее") и берем продукт, который идет после него
                    choice_markers = ["меньше", "ниже", "лучше", "выгоднее", "оптимальн", "рекомендую", "выбираем"]
                    marker_pos = -1
                    for marker in choice_markers:
                        pos = message_text.find(marker)
                        if pos != -1 and (marker_pos == -1 or pos < marker_pos):
                            marker_pos = pos
                    
                    if marker_pos != -1:
                        text_after_marker = message_text[marker_pos:]
                        for p in found_products:
                            name = p.get("name", "").lower()
                            if name in text_after_marker:
                                product_selection["resolved_product"] = p
                                logger.info(f"Super-fallback for product_compare: extracted resolved_product after marker: {p}")
                                break
        resolved = product_selection.get("resolved_product")
        product_resolution = ctx.session.state.get("product_resolution") or {}
        # 1. Определяем code продукта (из state резолвера или из ответа LLM)
        base_code = None
        if isinstance(product_resolution, dict) and product_resolution.get("product_code"):
            base_code = product_resolution.get("product_code")
        elif isinstance(resolved, dict) and resolved.get("code"):
            base_code = resolved.get("code")

        # 2. Если code есть, идем в БД и забираем folder_kit (Python-фоллбэк, не зависит от LLM)
        if base_code:
            full_details = await self.product_resolver.fetch_product_full_details(base_code)
            if full_details.get("folder_kit"):
                logger.info(f"Python fallback: успешно получен folder_kit='{full_details['folder_kit']}' для code={base_code}")
                
                # Если LLM вообще не вернула resolved_product, создаем базовый словарь
                if not resolved:
                    resolved = {
                        "code": base_code,
                        "name": product_resolution.get("product_name", "")
                    }
                    product_selection["resolved_product"] = resolved
                    
                # Жестко инжектим folder_kit в словарь
                resolved["folder_kit"] = full_details["folder_kit"]
        if isinstance(product_resolution, dict) and product_resolution.get("product_code"):
            if not resolved:
                # Если агент не вернул resolved_product, но resolver нашел продукт, используем его данные
                resolved = {
                    "code": product_resolution.get("product_code"),
                    "name": product_resolution.get("product_name"),
                    "folder_kit": product_resolution.get("folder_kit") # Может быть None или строка
                }
                product_selection["resolved_product"] = resolved
            else:
                # Если агент вернул resolved_product, но у него нет folder_kit, но resolver его нашел, добавим его
                if not resolved.get("folder_kit") and product_resolution.get("folder_kit"):
                    resolved["folder_kit"] = product_resolution.get("folder_kit")
                    product_selection["resolved_product"] = resolved # Обновляем словарь в product_selection
        
        if resolved and (resolved.get("name") or resolved.get("code")):
            # Получаем текущий контекст
            current_context = self._get_product_dialog_context(ctx)
            # Обновляем selected_product
            current_context["selected_product"] = {
                "code": resolved.get("code", ""),
                "name": resolved.get("name", ""),
                "folder_kit": resolved.get("folder_kit") # Добавляем folder_kit в selected_product
            }
            # Сохраняем обновлённый контекст
            ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = current_context
            logger.debug("Updated _product_dialog_context.selected_product: %s", current_context["selected_product"])
        if product_selection.get("mode") == "no_data" and intent == "product_kit":
            # Берем resolved_product, который мы только что обогатили через Python fallback
            resolved_product = product_selection.get("resolved_product") or {}
            folder_kit = str(resolved_product.get("folder_kit") or "").strip()
            
            # Если folder_kit нашелся (значит, в БД он есть, но LLM-агент его проигнорировал из-за нечеткого SQL-поиска)
            if folder_kit and folder_kit.lower() != "none":
                logger.info("Agent returned no_data, but folder_kit found in resolved_product. Forcing product_kit.")
                product_code = str(resolved_product.get("code") or "").strip()
                product_name = str(resolved_product.get("name") or "").strip()
                
                # Формируем action для бота
                ctx.session.state["_bot_action"] = {
                    "type": "send_product_kit",
                    "product_code": product_code,
                    "product_name": product_name,
                    "folder_kit": folder_kit,
                }
                ctx.session.state["_root_final_text"] = f"📂 Отправляю комплект документов для продукта **{product_name}**."
                
                # Подменяем ответ агента, чтобы контекст диалога сохранился корректно
                product_selection["mode"] = "product_kit"
                product_selection["resolved_product"] = resolved_product
                product_selection["message"] = ctx.session.state["_root_final_text"]
                
                # Сохраняем контекст и выходим, пропуская стандартную логику
                self._store_product_dialog_context(ctx, product_selection)
                return
        # Сохраняем последний продукт для контекста
        resolved = product_selection.get("resolved_product")
        logger.debug(f"RESOLVED PRODUCT_SELECTION: {resolved}")
        if product_selection.get("mode") in ("product_card", "product_kit") and resolved:
            name = resolved.get('name', '')
            code = resolved.get('code', '')
            if name or code:
                ctx.session.state["last_product"] = f"{name} (код {code})".strip()
                logger.debug("Saved last_product='%s' to state", ctx.session.state["last_product"])
        # Не делаем ничего для 'no_data', 'needs_clarification' и т.д., чтобы не потерять предыдущее значен
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
