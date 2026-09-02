import json
import re
from datetime import datetime, timezone
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, ClassVar
from dataclasses import dataclass, field
from collections import OrderedDict, deque
import asyncpg
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
    PRODUCT_CARD_KIT_OFFER
)
from .helpers import extract_json, truncate_for_log, format_text_answer, format_reject_answer
from .debug_trace import log_advisor_input_state
from .json_leaf_runner import AgentValidationFailure, run_json_leaf_agent
from .agents.owasp_agent import validate_owasp_result
from .agents.dispatcher_agent import validate_dispatcher_result
from .agents.kb_answer_agent import run_kb_agent_with_self_correction
from .agents.smalltalk_agent import validate_smalltalk_result
from .agents.doc_search_orchestrator import DocSearchOrchestrator
from .agents.product_filter_contract import validate_product_filter_result
from .agents.product_info_contract import validate_product_info_result
from .agents.advisor_contract import (
    AdvisorContentResult,
    validate_advisor_content_result,
    validate_advisor_final_result,
)
from .advisor_profile import AdvisorClientProfile, merge_advisor_profile
from .advisor_profile_matcher import (
    AdvisorSelectedClientType,
    validate_selected_client_type,
)
from .advisor_ranking_service import AdvisorRankingResult, AdvisorRankingService
from .glossary import GlossaryLookup
from .product_resolver_service import ProductResolverService
from .smart_fallback import (
    generate_agent_fallback, OWASP_INVALID_CONTRACT_USER_MESSAGE, 
    VALIDATION_ERROR_USER_MESSAGE, RESPONSE_SCHEMA_CONFIGURATION_ERROR_MESSAGE
)
from .stage_metrics import (
    STAGE_METRICS_STATE_KEY,
    TIMING_STATE_DELTA_KEY,
    build_timing_payload,
)

logger = setup_logger("root_agent", "agent.log")

BOT_USER_PROFILE_MESSAGE_PREFIX = "Контекст пользователя:"
OWASP_CONTEXT_WINDOW = 6
OWASP_HISTORY_STATE_KEY = "_owasp_recent_messages"
PRODUCT_DIALOG_CONTEXT_STATE_KEY = "_product_dialog_context"
ADVISOR_DIALOG_CONTEXT_STATE_KEY = "advisor_dialog_context"
ADVISOR_DIALOG_CONTEXT_SCHEMA_VERSION = 2
PRODUCT_FILTER_FOLLOWUP_QUESTION = (
    "Могу показать карточку продукта или скачать комплект. Какой продукт тебя интересует ?"
)
PRODUCT_FILTER_ONLY_FOLLOWUP_QUESTION = "Могу показать карточку продукта или скачать комплект."
PRODUCT_ATTRIBUTE_FOLLOWUP_QUESTION = (
    "Могу показать продукты с этими свойствами. Какое свойство тебя интересует ?"
)
PRODUCT_COMPARE_CLARIFICATION_QUESTION = (
    "Какой из продуктов вы хотите сравнить с \"{label}\":"
)
PRODUCT_COMPARE_NOT_FOUND_IN_ARCHIVE = "Не удалось найти в архиве «{label}»."
PRODUCT_COMPARE_NOT_FOUND = "Не удалось найти «{label}»."
PRODUCT_COMPARE_FALLBACK_CANDIDATES = (
    "Не найден «{label}» {requested_place}, вот кандидаты среди {fallback_place}:"
)
PRODUCT_COMPARE_FALLBACK_FOUND = (
    "Не найден «{label}» {requested_place}, найден среди {fallback_place}:"
)
COMPARE_MENTION_STATUS_TOKEN_RE = re.compile(
    r"^(?:активн\w*|архивн\w*|действующ\w*|архив(?:а|е|ом|у)?)$",
    flags=re.IGNORECASE,
)
COMPARE_MENTION_ARCHIVED_RE = re.compile(
    r"\bархивн\w*\b|\b(?:в|из)\s+архив(?:а|е)?\b|\bархив(?:а|е|ом|у)\b",
    flags=re.IGNORECASE,
)
DOC_LIST_FOLLOWUP_INTENTS = frozenset({"file_download", "show_more", "show_all"})

# Скомпилированные регулярные выражения (оптимизация производительности)
RE_CLEAN_SPACES = re.compile(r"\s+")
RE_PRODUCT_CODES = re.compile(r"\b\d{3,}(?:\+\d{3,})?\b")
RE_BLIND_TRIGGERS = re.compile(
    r"\b(скач\w*|пришл\w*|отправ\w*|дай|дать|комплект\w*|материал\w*|документ\w*|давай|ок|хорошо|ладно|параметр\w*|карточк\w*|свойств\w*|характеристик\w*|подробн\w*|покаж\w*|расскаж\w*|презентац\w*|презентер\w*|памятк\w*|инструкц\w*|регламент\w*|шаблон\w*|пф|полис\w*|договор\w*|буклет\w*|нем|о\s+нем|ней|о\s+ней|этом|об\s+этом|программе|продукт\w*|программа|его|ее|них|покажи|выведи|открой|найди|скинь|кидай|хочу|пакет\s+документов|пакет\s+материалов|комплект\s+документов|полный\s+комплект|нужен|скачать|скинь|пришли|отправь|про|по|для|на|в|во|с|со|к|ко|о|об|и|а|но|да|же|бы|ли)\b"
)
RE_EXPLICIT_KIT = re.compile(r"\b(пакет документов|пакет материалов|полный комплект|все материалы|комплект|пакет)\b")
RE_ASKING_LIST = re.compile(r"\b(какие|что за|список|покажи список|есть ли)\b")
RE_EXPLICIT_FILTER = re.compile(r"\b(архивные|все продукты|список продуктов|покажи продукты|покажи архивные)\b")
RE_CONFIRMATION_WORDS = {"давай", "да", "давайте", "пришли", "отправь", "скинь", "кидай", "хочу", "ок", "хорошо", "давай комплект", "пришли комплект"}
RE_PRODUCT_NAME_TRIM = re.compile(r"(?i)^(найди|покажи|выведи|открой|документы|доки|по|для|скачать|файл|файлы|материалы|презентацию|презентер|памятку|инструкцию|регламент|шаблон|список)\s+")

# Выделенные регулярки для Приоритета 1 (Сравнения и Команды)
RE_COMPARISON = re.compile(
    r"\b(сравни|сравнить|чем\s+отличается|чем\s+отличаются|в\s+чем\s+разница|какая\s+разница|отличия|сравни\s+с|по\s+сравнению|чем\s+лучше|разница\s+между)\b",
    re.IGNORECASE,
)
RE_COMMAND_VERBS = re.compile(
    r"\b(покаж\w*|найди|найд\w*|открой|выведи|расскаж\w*|дай|пришли|отправ\w*|скач\w*|скинь|кидай|хочу|нужен|сравни|сравнит\w*)\b",
    re.IGNORECASE,
)

# регулярки для smalltalk по контексту:
RE_SMALLTALK_CHOICE_FOLLOWUP = re.compile(
    r"^\s*(?:ну|а|и|так|тогда|подскажи|скажи|пожалуйста)?\s*"
    r"(?:"
    r"где меньше рисков|где рисков меньше|какой лучше|какой из продуктов лучше|какой из пакетов лучше|"
    r"что выбрать|что из этого выбрать|выбери|порекомендуй|рекомендуешь|лучше выбрать|лучше взять|"
    r"меньше риск|меньше рисков|более надежн\w*|надежнее|что посоветуешь|"
    r"какой продукт лучше|какой продукт выбрать|какой пакет лучше|какой пакет выбрать"
    r")\s*[?!.]*\s*$",
    re.IGNORECASE,
)

RE_SMALLTALK_CLARIFICATION_FOLLOWUP = re.compile(
    r"\b(там|в нем|в нём|в ней|в этом|нем|нём|ней|этом|о нем|о нём|о ней|"
    r"валюта|валют|доллар|рубл|срок|риск|выплат|гарант|доход|капитал|"
    r"зк|фн|пф|снг|дсг|что ли|правда|точно)\b"
)

RE_KB_EXPLANATION_REQUEST = re.compile(
    r"\b(объясн|как объяснить|как сказать|как ответить|аргумент|возражен|"
    r"клиент переживает|клиент боится|что сказать клиенту|показать клиенту|"
    r"почему это|зачем это|как это преподнести)\b"
)

RE_DOC_CONTEXT_REQUEST = re.compile(
    r"\b(документ|документы|документами|материал|материалы|файл|файлы|"
    r"справк|лиценз|что показать|что дать|нужны документы|показать список|"
    r"что показать клиенту|что дать клиенту)\b"
)
# Полный список ключей состояния, очищаемых перед каждым ходом
STATE_KEYS_TO_CLEAR = [
    "user_query", "search_query", "faq_collection", "kb_answer_collection", "intent",
    "dispatcher_user_query", "doc_search_query", "doc_search_intent",
    "product_info_search_query", "product_info_intent",
    "product_filter_search_query", "product_filter_intent",
    "from_glossary", "_owasp_result_parsed",
    "_dispatcher_result_parsed", "_doc_search_result_parsed",
    "_kb_answer_result_parsed", "_smalltalk_result_parsed",
    "_product_info_content_result_parsed", "_product_filter_content_result_parsed",
    "_product_info_result_parsed", "_product_filter_result_parsed",
    "product_info_content_result_json", "product_filter_content_result_json",
    "product_info_result_json", "product_filter_result_json",
    "_product_info_content_tool_calls", "_product_info_content_tool_events",
    "_product_filter_content_tool_calls", "_product_filter_content_tool_events",
    "_root_final_text", "_bot_action", "product_resolution",
    "product_resolutions", "product_filter_resolution",
    "owasp_current_user_message", "owasp_recent_messages_json",
    STAGE_METRICS_STATE_KEY,
    "dialog_recent_messages",
    "dialog_context_json",
    "product_dialog_context_json",
    "advisor_search_query", "advisor_client_profile", "advisor_client_profile_json",
    "advisor_dialog_context_json", "advisor_minimum_client_type_confidence",
    "advisor_source_turn", "advisor_updated_at",
    "_advisor_content_result_parsed", "_advisor_result_parsed",
    "advisor_content_result_json", "advisor_ranking_result",
    "advisor_ranking_result_json", "advisor_result_json",
    "_advisor_content_tool_calls", "_advisor_content_tool_events",
]

def is_bot_user_profile_injection_message(text: str) -> bool:
    return (text or "").lstrip().startswith(BOT_USER_PROFILE_MESSAGE_PREFIX)

def is_response_schema_configuration_error(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "failed to parse the parameter" in message
        and "set_model_response" in message
        and "automatic function calling" in message
    )

async def is_history_empty_by_global_id(global_user_id: str) -> bool:
    """Одним запросом находит platform_user_id по UUID в user_accounts 
    и проверяет, пуста ли его история в chat_history."""
    conn = None
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
        return count == 0  # Если 0, значит история пуста (был /reset)
    except Exception as e:
        logger.error(f"Ошибка проверки существующей таблицы истории: {e}")
        return False
    finally:
        if conn:
            await conn.close()

@dataclass
class PipelineContext:
    """Единый контейнер данных текущего шага пайплайна."""
    ctx: Any
    user_text: str
    clean_text: str
    session_id: str
    last_route: Optional[str] = None
    last_intent: Optional[str] = None
    last_search_query: Optional[str] = None
    last_product: Optional[str] = None
    product_dialog_context: Optional[Dict[str, Any]] = None
    doc_search_context: Optional[Dict[str, Any]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

class RootAgent(BaseAgent):
    """Оркестрирует безопасную маршрутизацию запроса в целевой сценарий.

    Цепочка начинается с `owasp_agent` и `dispatcher_agent`, после чего запрос
    передается в поиск документов, базу знаний, smalltalk, продуктовый сценарий
    или advisor-пайплайн с детерминированным ранжированием и сохранением контекста.
    """
    owasp_agent: LlmAgent
    dispatcher_agent: LlmAgent
    doc_search_orchestrator: DocSearchOrchestrator
    kb_answer_agent: LlmAgent
    smalltalk_agent: LlmAgent
    product_info_content_agent: LlmAgent
    product_info_format_agent: LlmAgent
    product_filter_content_agent: LlmAgent
    product_filter_format_agent: LlmAgent
    advisor_content_agent: LlmAgent
    advisor_format_agent: LlmAgent
    advisor_ranking_service: AdvisorRankingService
    advisor_minimum_client_type_confidence: float
    glossary_lookup: GlossaryLookup
    product_resolver: ProductResolverService
    faq_collection: str
    kb_collection: str

    MAX_HISTORY_PER_USER: ClassVar[int] = 3  # Сколько последних запросов хранить для ОДНОГО пользователя
    MAX_DIALOG_TURNS_HARD_LIMIT: ClassVar[int]=3
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
        smalltalk_agent: LlmAgent,
        product_info_content_agent: LlmAgent,
        product_info_format_agent: LlmAgent,
        product_filter_content_agent: LlmAgent,
        product_filter_format_agent: LlmAgent,
        advisor_content_agent: LlmAgent,
        advisor_format_agent: LlmAgent,
        advisor_ranking_service: AdvisorRankingService,
        advisor_minimum_client_type_confidence: float = 0.75,
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
            smalltalk_agent=smalltalk_agent,
            product_info_content_agent=product_info_content_agent,
            product_info_format_agent=product_info_format_agent,
            product_filter_content_agent=product_filter_content_agent,
            product_filter_format_agent=product_filter_format_agent,
            advisor_content_agent=advisor_content_agent,
            advisor_format_agent=advisor_format_agent,
            advisor_ranking_service=advisor_ranking_service,
            advisor_minimum_client_type_confidence=advisor_minimum_client_type_confidence,
            glossary_lookup=glossary_lookup or GlossaryLookup(),
            product_resolver=product_resolver or ProductResolverService(),
            faq_collection=faq_collection,
            kb_collection=kb_collection,
            sub_agents=[
                owasp_agent,
                dispatcher_agent,
                doc_search_orchestrator,
                kb_answer_agent,
                smalltalk_agent,
                product_info_content_agent,
                product_info_format_agent,
                product_filter_content_agent,
                product_filter_format_agent,
                advisor_content_agent,
                advisor_format_agent,
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
        advisor_dialog_context = session_state.get(ADVISOR_DIALOG_CONTEXT_STATE_KEY)
        if isinstance(advisor_dialog_context, dict):
            state_delta[ADVISOR_DIALOG_CONTEXT_STATE_KEY] = advisor_dialog_context

        timing = build_timing_payload(session_state)
        if timing:
            state_delta[TIMING_STATE_DELTA_KEY] = timing

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
            dispatch_search_query = str(dispatch.get("search_query") or "").strip()
            if dispatch_search_query:
                ctx.session.state["last_search_query"] = dispatch_search_query
            
            # Автоматически управляем списком документов
            if dispatch.get("route") == "doc_search":
                intent = str(dispatch.get("intent") or "")
                if intent not in DOC_LIST_FOLLOWUP_INTENTS and intent == "doc_search" and text.strip():
                    ctx.session.state["last_document_list"] = text[:1500]
                    # Автоматическое сохранение контекста продукта из найденных документов ---
                    codes = self._extract_product_codes(text)
                    first_code = codes[0] if codes else ""
                    name_guess = ""
                    # 1. Пытаемся вытащить имя продукта из строки ответа, если есть код
                    if first_code:
                        match_name = re.search(r"(?:^|\d+\.\s*)([^\n()]+)\s*\(" + re.escape(first_code) + r"\)", text)
                        if match_name:
                            # Очищаем от процентов доходности в хвосте, если они прилипли
                            name_guess = re.sub(r"\s+\d+([.,]\d+)?%\s*$", "", match_name.group(1).strip()).strip()
                    
                    # Fallback: если в тексте ответа нет кодов, берем название из search_query или user_text
                    if not name_guess:
                        sq = dispatch.get("search_query", "").strip()
                        # Если search_query содержит что-то осмысленное (не просто общие слова)
                        if sq and len(sq) > 2 and not re.fullmatch(r"(?i)(документы|файлы|материалы|список)", sq):
                            name_guess = sq
                        elif user_text:
                            name_guess = RE_PRODUCT_NAME_TRIM.sub("", user_text).strip()
                            name_guess = re.sub(r"(?i)\bпо\b\s*", "", name_guess).strip()
                    # Если имя удалось определить, сохраняем контекст
                    if name_guess:
                        # Записываем в плоскую строку для _get_last_product_from_state
                        ctx.session.state["last_product"] = f"{name_guess} (код {first_code})".strip() if first_code else name_guess.strip()
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
                elif intent not in DOC_LIST_FOLLOWUP_INTENTS:
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
                ADVISOR_DIALOG_CONTEXT_STATE_KEY: ctx.session.state.get(
                    ADVISOR_DIALOG_CONTEXT_STATE_KEY
                ),
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
            idx for idx, event in enumerate(events)
            if RootAgent._is_dialog_memory_event(event)
            and getattr(getattr(event, "content", None), "role", None) == "user"
        ]
        if len(user_turn_indices) <= max_turns:
            return list(events)

        cutoff_idx = user_turn_indices[-max_turns]
        return list(events[cutoff_idx:])

    async def _trim_dialog_memory(self, ctx: InvocationContext) -> None:
        events = list(getattr(ctx.session, "events", None) or [])
        effective_max = min(AGENT_DIALOG_MEMORY_MAX_TURNS, self.MAX_DIALOG_TURNS_HARD_LIMIT)
        retained_events = self._retained_dialog_memory_events(
            events,
            effective_max,
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
        self._clear_state_keys(ctx, STATE_KEYS_TO_CLEAR)

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
        role, text = str(role or "").strip(), str(text or "").strip()
        if role in {"user", "assistant"} and text:
            history = self._get_recent_messages(ctx)
            history.append({"role": role, "text": text})
            self._store_recent_messages(ctx, history)

    @staticmethod
    def _truncate_prompt_text(text: str, limit: int = 2500) -> str:
        text = str(text or "").strip()
        if len(text) <= limit:
            return text
        return text[:limit] + "..."

    def _format_recent_messages_for_prompt(
        self,
        messages: List[Dict[str, str]],
        max_messages: int = 6,
    ) -> str:
        """
        Форматирует bounded history для передачи в prompt агентов.
        """
        lines: List[str] = []
        for message in messages[-max_messages:]:
            role = str(message.get("role") or "").strip()
            text = self._truncate_prompt_text(message.get("text"))
            if not text:
                continue
            if role == "user":
                lines.append(f"Пользователь: {text}")
            elif role == "assistant":
                lines.append(f"Ассистент: {text}")

        return "\n".join(lines) if lines else "Контекста нет"

    def _prepare_dialog_context_state(self, ctx: InvocationContext) -> None:
        """
        Готовит общий контекст диалога для dispatcher, smalltalk, kb_answer
        и других leaf-агентов.
        """
        # Подтягиваем профиль, чтобы smalltalk мог использовать {first_name}
        for key, value in self._get_user_profile(ctx).items():
            if key not in ctx.session.state or ctx.session.state.get(key) in (None, ""):
                ctx.session.state[key] = value
        if not ctx.session.state.get("first_name"):
            ctx.session.state["first_name"] = "unknown"
        recent_messages = self._get_recent_messages(ctx)[-6:]
        product_context = self._get_product_dialog_context(ctx)
        ctx.session.state["dialog_recent_messages"] = (
            self._format_recent_messages_for_prompt(recent_messages)
        )
        ctx.session.state["product_dialog_context_json"] = (
            json.dumps(product_context, ensure_ascii=False)
            if product_context
            else "{}"
        )
        dialog_context = {
            "recent_messages": recent_messages,
            "last_route": str(ctx.session.state.get("last_route") or ""),
            "last_intent": str(ctx.session.state.get("last_intent") or ""),
            "last_search_query": str(ctx.session.state.get("last_search_query") or ""),
            "last_product": str(ctx.session.state.get("last_product") or ""),
            "last_document_list": str(ctx.session.state.get("last_document_list") or "")[:500],
            "product_dialog_context": product_context,
        }
        ctx.session.state["dialog_context_json"] = json.dumps(
            dialog_context,
            ensure_ascii=False,
        )

    def _set_last_product_from_result(
        self,
        ctx: InvocationContext,
        product_result: Dict[str, Any],
        source_mode: str | None = None,
    ) -> None:
        """
        Сохраняет last_product и selected_product из результата продуктового агента.
        """
        resolved = product_result.get("resolved_product") or {}
        code = str(resolved.get("code") or "").strip()
        name = str(resolved.get("name") or "").strip()
        folder_kit = str(resolved.get("folder_kit") or "").strip()
        is_active = str(resolved.get("is_active") or "").strip()
        if not (code or name) and str(product_result.get("mode") or "") == "product_filter":
            products = self._normalize_dialog_products(product_result.get("products"))
            if len(products) == 1:
                resolved = products[0]
                code = str(resolved.get("code") or "").strip()
                name = str(resolved.get("name") or "").strip()
                folder_kit = str(resolved.get("folder_kit") or "").strip()
                is_active = str(resolved.get("is_active") or "").strip()
        if not (code or name):
            return
        label = f"{name} (код {code})" if code and name else (name or code)
        ctx.session.state["last_product"] = label
        context = self._get_product_dialog_context(ctx)
        selected_product = {
            "code": code,
            "name": name,
            "folder_kit": folder_kit,
        }
        if is_active:
            selected_product["is_active"] = is_active
        context["selected_product"] = selected_product
        if source_mode:
            context["last_mode"] = source_mode
        ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = context
        logger.info(
            "last_product updated from product agent: source_mode=%s product=%s",
            source_mode,
            label,
        )

    def _apply_smalltalk_product_selection(
        self,
        ctx: InvocationContext,
        smalltalk_result: Dict[str, Any],
    ) -> None:
        """
        Если smalltalk_agent выбрал продукт, сохраняем его в last_product
        и в _product_dialog_context.selected_product.
        """
        selected = smalltalk_result.get("selected_product")
        if not isinstance(selected, dict):
            selected = {}
        code = str(
            smalltalk_result.get("selected_product_code")
            or selected.get("code")
            or ""
        ).strip()
        name = str(
            smalltalk_result.get("selected_product_name")
            or selected.get("name")
            or ""
        ).strip()
        folder_kit = str(
            smalltalk_result.get("selected_product_folder_kit")
            or selected.get("folder_kit")
            or ""
        ).strip()
        if not (code or name):
            return
        label = f"{name} (код {code})" if code and name else (name or code)
        ctx.session.state["last_product"] = label
        context = self._get_product_dialog_context(ctx)
        context["selected_product"] = {
            "code": code,
            "name": name,
            "folder_kit": folder_kit,
        }
        if not context.get("last_mode"):
            context["last_mode"] = "smalltalk_product_selection"
        ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = context
        logger.info(
            "smalltalk selected product saved: code=%s name=%s label=%s",
            code,
            name,
            label,
        )

    def _contextual_smalltalk_followup_dispatch(
        self,
        ctx: InvocationContext,
        user_text: str,
    ) -> Dict[str, Any] | None:
        """
        Контекстный smalltalk follow-up:
        - где меньше рисков после сравнения продуктов;
        - там доллары? после карточки продукта;
        - короткие уточнения по прошлому продуктовому контексту.
        """
        normalized = self._normalize_product_dialog_text(user_text)
        if not normalized:
            return None
        # Не перехватываем документы, комплект и объяснения для KB.
        if RE_DOC_CONTEXT_REQUEST.search(normalized):
            return None
        if RE_EXPLICIT_KIT.search(normalized):
            return None
        if RE_KB_EXPLANATION_REQUEST.search(normalized):
            return None
        # Если пользователь явно указал новый продукт, лучше отдать dispatcher/product agents.
        if self._extract_product_codes(user_text):
            return None
        # Не перехватываем явные запросы карточки/параметров/документов
        # Если есть слова "покажи карточку", "дай комплект", "найди документы" и т.д.,
        # это полноценный запрос, а не follow-up уточнение.
        explicit_request_markers = re.compile(
            r"\b(покажи|дай|дать|найди|открой|выведи|карточк|параметр|свойств|"
            r"характеристик|подробн|расскаж|презентац|презентер|памятк|инструкц|"
            r"регламент|шаблон|документ|файл|материал)\b"
        )
        if explicit_request_markers.search(normalized):
            return None
        context = self._get_product_dialog_context(ctx)
        last_route = str(ctx.session.state.get("last_route") or "")
        last_mode = str(context.get("last_mode") or "")
        products = self._normalize_dialog_products(context.get("products"))
        # 1. После сравнения продуктов: "Где меньше рисков?", "Какой лучше?"
        if (
            last_route in {"product_info", "product_filter"}
            and last_mode in {"product_compare", "product_filter"}
            and len(products) >= 2
            and RE_SMALLTALK_CHOICE_FOLLOWUP.search(normalized)
        ):
            logger.info("Contextual smalltalk short-circuit: product choice follow-up")
            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "smalltalk",
                    "intent": "smalltalk",
                    "reason": "smalltalk_other",
                    "search_query": "",
                },
                dict(ctx.session.state),
            )
        # 2. После карточки продукта: "Там доллары?", "Он рублёвый?", "Срок 3 года?"
        selected = (
            self._get_selected_product_from_context(ctx)
            or self._get_last_product_from_state(ctx)
        )
        if (
            selected
            and last_route in {"product_info", "product_filter"}
            and len(normalized.split()) <= 6
            and RE_SMALLTALK_CLARIFICATION_FOLLOWUP.search(normalized)
        ):
            # Проверяем, что запрос не упоминает ДРУГОЙ продукт
            selected_name = str(selected.get("name") or "").lower()
            selected_code = str(selected.get("code") or "").strip()
            # Извлекаем возможные названия продуктов из запроса
            # Если в запросе есть "зк", "фн" и т.д., проверяем, совпадает ли это с selected_product
            product_abbreviations = {
                "зк": "защищенный капитал",
                "фн": "fort knox",
                "пф": "путь к успеху",
                "снг": "сна",
                "дсг": "дсж",
            }
            # Проверяем, упоминается ли в запросе продукт, отличный от selected
            mentions_different_product = False
            for abbrev, full_name in product_abbreviations.items():
                if re.search(rf"\b{abbrev}\b", normalized):
                    # Если аббревиатура есть в запросе, проверяем, совпадает ли она с selected_product
                    if (
                        selected_name
                        and full_name not in selected_name
                        and abbrev not in selected_name
                        and selected_code not in normalized
                    ):
                        mentions_different_product = True
                        break
            # Также проверяем явные упоминания других продуктов по названию
            other_product_patterns = [
                r"\b(защищенн\w* капитал|fort\s*knox|форт\s*нокс|unit\s*linked|юнит\s*линкед|"
                r"альфа\s*kids|альфа\s*баланс|альфа\s*инвестиции|деньги\s+в\s+резерве|"
                r"путь\s+к\s+успеху|фиксированн\w*\s+доход)\b"
            ]
            for pattern in other_product_patterns:
                match = re.search(pattern, normalized, re.IGNORECASE)
                if match:
                    found_product = match.group(0).lower()
                    # Если найденный продукт не совпадает с selected_product
                    if (
                        selected_name
                        and found_product not in selected_name
                        and selected_name not in found_product
                    ):
                        mentions_different_product = True
                        break
            if mentions_different_product:
                logger.info(
                    "Contextual smalltalk short-circuit SKIPPED: query mentions different product"
                )
                return None
            logger.info("Contextual smalltalk short-circuit: product clarification follow-up")
            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "smalltalk",
                    "intent": "smalltalk",
                    "reason": "smalltalk_other",
                    "search_query": "",
                },
                dict(ctx.session.state),
            )
        return None

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
        t = RE_CLEAN_SPACES.sub(" ", user_text.strip().lower().replace("ё", "е"))
        if not t:
            return None
        if re.fullmatch(r"(все|полностью|целиком|all|(покажи|дай|выведи|открой)\s+все|(покажи|выведи)\s+полностью)[!?.]*", t):
            return "show_all"
        if re.fullmatch(r"(еще|больше|далее|следующие|(еще)\s+(файлы|документы)|next|more)[!?.]*", t):
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
            is_active = str(option.get("is_active") or "").strip() or "Статус не указан"
            details = [item for item in (is_active, term, currency) if item]
            label = f"{option_code} {name}".strip() if (option_code and name) else (option_code or name)
            return f"{label} - {', '.join(details)}" if details else label
        return str(option or "").strip()

    @classmethod
    def _product_dialog_identity(cls, product: Dict[str, Any]) -> tuple[str, str, str]:
        """Ключ экземпляра продукта для compare: код, имя и статус."""
        return (
            str(product.get("code") or product.get("product_code") or "").strip(),
            cls._normalize_product_dialog_text(
                str(product.get("name") or product.get("product_name") or "")
            ),
            cls._normalize_product_dialog_text(str(product.get("is_active") or "")),
        )

    @classmethod
    def _exclude_resolved_identities(
        cls,
        options: List[Any],
        resolved: List[Dict[str, str]] | None,
    ) -> List[Any]:
        """Убирает уже найденные экземпляры из списка уточнения."""
        if not options or not resolved:
            return options
        banned = {
            cls._product_dialog_identity(item)
            for item in resolved
            if isinstance(item, dict)
            and (item.get("code") or item.get("name") or item.get("product_code") or item.get("product_name"))
        }
        if not banned:
            return options
        filtered: List[Any] = []
        for option in options:
            if isinstance(option, dict) and cls._product_dialog_identity(option) in banned:
                continue
            filtered.append(option)
        return filtered

    @classmethod
    def _format_product_answer(
        cls,
        product_result: Dict[str, Any],
        *,
        resolved_products: List[Dict[str, str]] | None = None,
    ) -> str:
        message = format_text_answer(product_result["message"])
        mode = product_result.get("mode")
        if mode == "product_filter":
            products = product_result.get("products")
            product_count = len(products) if isinstance(products, list) else 0
            if product_count == 1:
                message = message.replace(PRODUCT_FILTER_FOLLOWUP_QUESTION, PRODUCT_FILTER_ONLY_FOLLOWUP_QUESTION).strip()
                if PRODUCT_FILTER_ONLY_FOLLOWUP_QUESTION not in message:
                    message = "\n\n".join([message, PRODUCT_FILTER_ONLY_FOLLOWUP_QUESTION])
                return message
            if PRODUCT_FILTER_FOLLOWUP_QUESTION not in message:
                message = "\n\n".join([message, PRODUCT_FILTER_FOLLOWUP_QUESTION])
            return message

        if mode == "product_attribute_values":
            attribute_name = str(product_result.get("attribute_name") or "").strip()
            attribute_values = cls._normalize_attribute_values(
                product_result.get("attribute_values")
            )
            lines = [
                f"Доступные значения свойства «{attribute_name}»:",
                *(f"- {value}" for value in attribute_values),
                "",
                PRODUCT_ATTRIBUTE_FOLLOWUP_QUESTION,
            ]
            return "\n".join(lines)

        if mode == "product_card":
            # Добавляем предложение, только если агент сам его ещё не добавил
            message_lower = message.lower()
            if "комплект документов" not in message_lower and "скачать комплект" not in message_lower:
                message = message + f"\n\n {PRODUCT_CARD_KIT_OFFER}"
            return message
        if mode == "needs_clarification":
            # Structured options are rendered below; keep only the question from
            # an older or non-conforming formatter response to avoid two lists.
            message = next(
                (line.strip() for line in message.splitlines() if line.strip()),
                message,
            )
            raw_options = cls._exclude_resolved_identities(
                list(product_result.get("clarification_options") or []),
                resolved_products,
            )
            options = [
                cls._format_clarification_option(option)
                for option in raw_options
            ]
            options = [option for option in options if option]
            if not options:
                return message
            compare_question = cls._compare_one_sided_clarification_question(
                resolved_products
            )
            if compare_question:
                return "\n".join([compare_question, "", *options])
            return "\n".join([message, *options])
        return message

    @classmethod
    def _compare_one_sided_clarification_question(
        cls,
        resolved_products: List[Dict[str, str]] | None,
    ) -> str | None:
        """Вопрос уточнения, если в сравнении уже найден ровно один продукт."""
        if not resolved_products or len(resolved_products) != 1:
            return None
        product = resolved_products[0]
        code = str(product.get("code") or "").strip()
        name = str(product.get("name") or "").strip()
        label = f"{name} ({code})".strip()
        if not label:
            return None
        return PRODUCT_COMPARE_CLARIFICATION_QUESTION.format(label=label)

    @classmethod
    def _compare_mention_display_label(cls, mention: str) -> str:
        """Название упоминания без статусного слова, для сообщения пользователю."""
        tokens = [
            token
            for token in str(mention or "").split()
            if token and not COMPARE_MENTION_STATUS_TOKEN_RE.fullmatch(token)
        ]
        label = " ".join(tokens).strip() or str(mention or "").strip()
        if label and label[0].islower():
            return label[0].upper() + label[1:]
        return label

    @classmethod
    def _compare_mention_is_archived(cls, mention: str) -> bool:
        return bool(
            COMPARE_MENTION_ARCHIVED_RE.search(
                cls._normalize_product_dialog_text(mention)
            )
        )

    @classmethod
    def _compare_not_found_message(cls, resolutions: Dict[str, Any] | None) -> str | None:
        """Сообщение, если в сравнении есть not_found и нет ambiguous."""
        items = resolutions.get("items") if isinstance(resolutions, dict) else None
        if not isinstance(items, list) or not items:
            return None
        statuses = [str(item.get("status") or "") for item in items if isinstance(item, dict)]
        if "error" in statuses or "ambiguous" in statuses:
            return None
        missing = [
            item
            for item in items
            if isinstance(item, dict) and item.get("status") == "not_found"
        ]
        if not missing:
            return None
        lines = []
        for item in missing:
            mention = str(item.get("mention") or "").strip()
            label = cls._compare_mention_display_label(mention) or "продукт"
            template = (
                PRODUCT_COMPARE_NOT_FOUND_IN_ARCHIVE
                if cls._compare_mention_is_archived(mention)
                else PRODUCT_COMPARE_NOT_FOUND
            )
            lines.append(template.format(label=label))
        return "\n".join(lines)

    @classmethod
    def _compare_status_places(cls, requested_status: str | None) -> tuple[str, str]:
        if requested_status == "archived":
            return "в архиве", "действующих"
        return "в действующих", "архивных"

    @classmethod
    def _compare_fallback_clarification(
        cls,
        resolutions: Dict[str, Any] | None,
    ) -> Dict[str, Any] | None:
        """Уточнение, если продукт нашёлся только в другом каталоге и вариантов несколько."""
        items = resolutions.get("items") if isinstance(resolutions, dict) else None
        if not isinstance(items, list) or not items:
            return None
        fallback_items = [
            item
            for item in items
            if isinstance(item, dict)
            and item.get("status") == "ambiguous"
            and item.get("found_via_fallback")
        ]
        if not fallback_items:
            return None
        messages: List[str] = []
        options: List[Dict[str, str]] = []
        for item in fallback_items:
            mention = str(item.get("mention") or "").strip()
            label = cls._compare_mention_display_label(mention) or "продукт"
            requested_place, fallback_place = cls._compare_status_places(
                item.get("requested_status")
            )
            messages.append(
                PRODUCT_COMPARE_FALLBACK_CANDIDATES.format(
                    label=label,
                    requested_place=requested_place,
                    fallback_place=fallback_place,
                )
            )
            options.extend(cls._normalize_dialog_products(item.get("options") or []))
        if not options:
            return None
        return {
            "mode": "needs_clarification",
            "message": "\n".join(messages),
            "clarification_options": options,
            "resolved_product": None,
            "products": [],
        }

    @classmethod
    def _compare_fallback_notice(cls, resolutions: Dict[str, Any] | None) -> str | None:
        """Предупреждение, если сравнение идёт с продуктом из другого каталога."""
        items = resolutions.get("items") if isinstance(resolutions, dict) else None
        if not isinstance(items, list):
            return None
        lines: List[str] = []
        for item in items:
            if not isinstance(item, dict) or item.get("status") != "resolved":
                continue
            if not item.get("found_via_fallback"):
                continue
            mention = str(item.get("mention") or "").strip()
            label = cls._compare_mention_display_label(mention) or "продукт"
            requested_place, fallback_place = cls._compare_status_places(
                item.get("requested_status")
            )
            candidate = cls._format_clarification_option(
                {
                    "code": item.get("product_code") or "",
                    "name": item.get("product_name") or "",
                    "is_active": item.get("is_active") or "",
                }
            )
            header = PRODUCT_COMPARE_FALLBACK_FOUND.format(
                label=label,
                requested_place=requested_place,
                fallback_place=fallback_place,
            )
            lines.append("\n".join([header, candidate] if candidate else [header]))
        return "\n\n".join(lines) if lines else None

    @staticmethod
    def _normalize_product_dialog_text(text: str) -> str:
        return RE_CLEAN_SPACES.sub(" ", str(text or "").lower().replace("ё", "е")).strip()

    @staticmethod
    def _extract_product_codes(text: str) -> List[str]:
        return RE_PRODUCT_CODES.findall(text or "")

    @staticmethod
    def _normalize_dialog_products(value: Any) -> List[Dict[str, str]]:
        if not isinstance(value, list):
            return []
        products: List[Dict[str, str]] = []
        for item in value:
            if not isinstance(item, dict):
                continue
            product: Dict[str, str] = {}
            for key in ("code", "name", "term", "currency", "folder_kit", "is_active"):
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
        product_result: Dict[str, Any],
    ) -> None:
        mode = product_result.get("mode")
        if mode == "product_attribute_values":
            attribute_values = self._normalize_attribute_values(
                product_result.get("attribute_values")
            )
            if attribute_values:
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": "product_attribute_values",
                    "attribute_name": str(product_result.get("attribute_name") or "").strip(),
                    "attribute_column": str(product_result.get("attribute_column") or "").strip(),
                    "attribute_values": attribute_values,
                    "products": [],
                    "selected_product": None,
                }
            else:
                self._clear_product_dialog_context(ctx)
            return

        if mode == "product_filter":
            products = self._normalize_dialog_products(product_result.get("products"))
            if products:
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": "product_filter",
                    "products": products,
                    "selected_product": products[0] if len(products) == 1 else None,
                }
            else:
                self._clear_product_dialog_context(ctx)
            return

        if mode in {"product_card", "product_kit"}:
            if products := self._normalize_dialog_products([product_result.get("resolved_product")]):
                selected_product = products[0]
                # Извлекаем все атрибуты из текста карточки
                # (Валюта: Доллары, Риск: Без риска, Срок: Среднесрочный и т.д.)
                message = str(product_result.get("message") or "")
                card_attributes = self._extract_product_attributes_from_text(message)

                enriched_product: Dict[str, Any] = {**selected_product, **card_attributes}
                if mode == "product_card":
                    previous = self._get_product_dialog_context(ctx)
                    target_products = previous.get("products") or enriched_product
                else:
                    # A resolved kit replaces stale selections restored from an older turn.
                    target_products = [
                        {k: selected_product[k] for k in ("code", "name") if selected_product.get(k)}
                    ]
                ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                    "last_mode": mode,
                    "products": target_products,
                    "selected_product": selected_product,
                }
                if card_attributes:
                    logger.info(
                        "product_card: enriched selected_product with %d attributes: %s",
                        len(card_attributes),
                        list(card_attributes.keys())[:15],
                    )
            return

        if mode == "no_data":
            self._clear_product_dialog_context(ctx)
            return

        if mode == "needs_clarification":
            options = self._normalize_dialog_products(
                product_result.get("clarification_options") or []
            )
            previous = self._get_product_dialog_context(ctx)
            pending_intent = str(
                ctx.session.state.get("product_info_intent")
                or ctx.session.state.get("product_filter_intent")
                or ctx.session.state.get("last_intent")
                or previous.get("pending_intent")
                or ""
            ).strip()
            original_search_query = str(
                ctx.session.state.get("product_info_search_query")
                or ctx.session.state.get("product_filter_search_query")
                or ctx.session.state.get("last_search_query")
                or previous.get("original_search_query")
                or ""
            ).strip()
            compare_resolved = self._resolved_products_from_resolutions(ctx)
            if not compare_resolved:
                compare_resolved = self._normalize_dialog_products(
                    previous.get("compare_resolved_products") or []
                )
            if pending_intent == "product_compare":
                options = self._normalize_dialog_products(
                    self._exclude_resolved_identities(options, compare_resolved)
                )
            ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
                "last_mode": "needs_clarification",
                "pending_intent": pending_intent,
                "products": options,
                "clarification_options": options,
                "compare_resolved_products": compare_resolved,
                "original_search_query": original_search_query,
                "selected_product": previous.get("selected_product"),
            }
            return

        if mode == "product_compare":
            resolved_product = product_result.get("resolved_product")
            products = self._normalize_dialog_products(product_result.get("products") or [])
            previous = self._get_product_dialog_context(ctx)
            # Fallback: если агент не вернул products в ответе,
            # берём уже resolved продукты из product_resolutions
            if not products:
                products = self._resolved_products_from_resolutions(ctx)
                if products:
                    logger.info(
                        "product_compare: products restored from product_resolutions: %s",
                        [(p.get("code"), p.get("name")) for p in products],
                    )
            # Обогащаем каждый продукт атрибутами из message сравнения
            # (риск, выплаты, валюта, доход и т.д.)
            message = str(product_result.get("message") or "")
            if products and message:
                products = self._enrich_compare_products_with_attributes(message, products)
                logger.info(
                    "product_compare: products enriched with attributes: %s",
                    [
                        {
                            "code": p.get("code"),
                            "name": p.get("name"),
                            "attrs": [k for k in p.keys() if k not in ("code", "name")],
                        }
                        for p in products
                    ],
                )
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
        clean_msg = RE_BLIND_TRIGGERS.sub("", normalized)
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
            code_matches = [
                product for product in products if product.get("code") in codes
            ]
            if len(code_matches) == 1:
                return code_matches[0]

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

    def _resolved_products_for_clarification_filter(
        self,
        ctx: InvocationContext,
    ) -> List[Dict[str, str]]:
        """Уже найденные продукты compare, которые нельзя снова предлагать в уточнении."""
        previous = self._get_product_dialog_context(ctx)
        pending_intent = str(
            ctx.session.state.get("product_info_intent")
            or ctx.session.state.get("product_filter_intent")
            or ctx.session.state.get("last_intent")
            or previous.get("pending_intent")
            or ""
        ).strip()
        if pending_intent != "product_compare":
            return []
        compare_resolved = self._resolved_products_from_resolutions(ctx)
        if compare_resolved:
            return compare_resolved
        return self._normalize_dialog_products(
            previous.get("compare_resolved_products") or []
        )

    def _resolved_products_from_resolutions(
        self,
        ctx: InvocationContext,
    ) -> List[Dict[str, str]]:
        """Достаёт уже resolved продукты из product_resolutions (для resume compare)."""
        resolutions = ctx.session.state.get("product_resolutions") or {}
        items = resolutions.get("items") if isinstance(resolutions, dict) else None
        if not isinstance(items, list):
            return []

        products: List[Dict[str, str]] = []
        seen_identities: set[tuple[str, str, str]] = set()
        for item in items:
            if not isinstance(item, dict) or item.get("status") != "resolved":
                continue
            code = str(item.get("product_code") or "").strip()
            name = str(item.get("product_name") or "").strip()
            is_active = str(item.get("is_active") or "").strip()
            if not code and not name:
                continue
            identity = (
                code,
                self._normalize_product_dialog_text(name),
                self._normalize_product_dialog_text(is_active),
            )
            if identity in seen_identities:
                continue
            seen_identities.add(identity)
            product: Dict[str, str] = {}
            if code:
                product["code"] = code
            if name:
                product["name"] = name
            if is_active:
                product["is_active"] = is_active
            products.append(product)
        return products

    @staticmethod
    def _extract_product_attributes_from_text(text: str) -> Dict[str, str]:
        """
        Извлекает пары "Ключ: Значение" из текста карточки или блока сравнения.
        
        Поддерживает форматы:
        - "Ключ: Значение"           (product_card)
        - "• Ключ: Значение"         (product_compare, блоки "Одинаковые свойства")
        - "- Ключ: Значение"
        
        Возвращает dict {название_атрибута: значение}.
        Игнорирует служебные поля (Код, Продукт, Название).
        """
        if not text:
            return {}
        attributes: Dict[str, str] = {}
        # Паттерн: опциональный маркер (•/-/*), затем ключ (2..80 символов), ":", значение
        pattern = re.compile(
            r"(?:^|\n)\s*(?:[•\-\*]\s*)?([^:\n]{2,80}?)\s*:\s*([^\n•\-]+)",
            re.UNICODE,
        )
        skip_keys = {
            "код", "продукт", "название", "имя", "code", "name",
            "дата ввода", "дата", "folder_kit",
        }
        for match in pattern.finditer(text):
            key = RE_CLEAN_SPACES.sub(" ", match.group(1)).strip()
            value = match.group(2).strip()
            if not key or not value:
                continue
            # Отсекаем хвостовые маркеры списка, если попали
            value = re.sub(r"\s*[•\-\*]\s*$", "", value).strip()
            if not value:
                continue
            # Пропускаем идентификаторы — они уже есть в code/name
            if key.lower() in skip_keys:
                continue
            # Защита от мусорных "ключей" (например, "📂 Могу также прислать" — без ":" не пройдёт, но подстрахуемся)
            if len(key) < 3:
                continue
            attributes[key] = value
        return attributes

    def _enrich_compare_products_with_attributes(
        self,
        message: str,
        products: List[Dict[str, str]],
    ) -> List[Dict[str, Any]]:
        """
        Для режима product_compare извлекает атрибуты каждого продукта
        и общие свойства из message и добавляет их к product dict.
        Формат message:
            "7695 - Юнит Линк Ежемесячный доход
            • Уровень риска продукта: Низкий
            • Тип выплат: Ежемесячные выплаты

            7695 - Юнит Линк Стратегия роста
            • Уровень риска продукта: Высокий
            • Тип выплат: Без выплат

            Одинаковые свойства:
            • Тип продукта: Unit Linked
            • Валюта: Рубли
            ..."
        """
        if not message or not products:
            return products
        # 1) Общие свойства — секция "Одинаковые свойства:"
        common_attributes: Dict[str, str] = {}
        common_match = re.search(
            r"Одинаковые\s+свойства\s*:\s*([\s\S]*?)$",
            message,
            re.IGNORECASE,
        )
        if common_match:
            common_attributes = self._extract_product_attributes_from_text(
                common_match.group(1)
            )
        # 2) Секция различающихся свойств (если есть заголовок — ограничим ею поиск блоков продуктов)
        diff_section_end = len(message)
        diff_match = re.search(
            r"Одинаковые\s+свойства\s*:",
            message,
            re.IGNORECASE,
        )
        if diff_match:
            diff_section_end = diff_match.start()
        diff_text = message[:diff_section_end]
        enriched: List[Dict[str, Any]] = []
        for product in products:
            code = str(product.get("code") or "").strip()
            name = str(product.get("name") or "").strip()
            product_attrs: Dict[str, str] = {}
            # 3) Ищем блок этого продукта в секции различий
            patterns_to_try: List[str] = []
            if code and name:
                patterns_to_try.append(
                    rf"{re.escape(code)}\s*[-–—]\s*{re.escape(name)}"
                )
            if name:
                patterns_to_try.append(re.escape(name))
            if code:
                patterns_to_try.append(rf"\b{re.escape(code)}\b")
            block_text = ""
            for pattern in patterns_to_try:
                match = re.search(pattern, diff_text, re.IGNORECASE)
                if not match:
                    continue
                start_pos = match.end()
                # Граница блока: следующий "код -" или конец diff_text
                end_patterns = [
                    r"\n\d{3,}\s*[-–—]",
                    r"\n\s*\n",  # пустая строка как разделитель
                ]
                end_pos = len(diff_text)
                for ep in end_patterns:
                    end_match = re.search(ep, diff_text[start_pos:])
                    if end_match:
                        end_pos = min(end_pos, start_pos + end_match.start())
                block_text = diff_text[start_pos:end_pos]
                break
            if block_text:
                product_attrs = self._extract_product_attributes_from_text(block_text)
            # 4) Объединяем: общие + индивидуальные (индивидуальные имеют приоритет)
            merged = {**common_attributes, **product_attrs}
            enriched.append({**product, **merged})
        return enriched
    
    def _match_clarification_option(
        self,
        ctx: InvocationContext,
        user_text: str,
    ) -> Dict[str, str] | None:
        """Сопоставляет ответ пользователя с options после needs_clarification."""
        context = self._get_product_dialog_context(ctx)
        if context.get("last_mode") != "needs_clarification":
            return None
        options = self._normalize_dialog_products(
            context.get("clarification_options") or context.get("products") or []
        )
        if not options:
            return None
        codes = self._extract_product_codes(user_text)
        if len(codes) == 1:
            code = codes[0]
            code_matches = [option for option in options if option.get("code") == code]
            if len(code_matches) == 1:
                return code_matches[0]
        normalized = self._normalize_product_dialog_text(user_text)
        matches: List[Dict[str, str]] = []
        for option in options:
            label = self._normalize_product_dialog_text(
                self._format_clarification_option(option)
            )
            name = self._normalize_product_dialog_text(option.get("name", ""))
            if normalized and (
                normalized == label
                or (name and normalized == name)
            ):
                matches.append(option)
        if len(matches) == 1:
            return matches[0]
        return None

    def _dispatch_clarification_followup(
        self,
        ctx: InvocationContext,
        selected: Dict[str, str],
    ) -> Dict[str, Any] | None:
        """Продолжает исходный intent (обычно product_compare) после выбора option."""
        context = self._get_product_dialog_context(ctx)
        pending_intent = str(
            context.get("pending_intent")
            or ctx.session.state.get("last_intent")
            or ""
        ).strip()
        code = str(selected.get("code") or "").strip()
        name = str(selected.get("name") or "").strip()
        if not code and not name:
            return None

        ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = {
            **context,
            "last_mode": pending_intent or "needs_clarification",
            "selected_product": selected,
            "clarification_options": [],
            "pending_intent": "",
        }

        if pending_intent == "product_compare":
            resolved = self._normalize_dialog_products(
                context.get("compare_resolved_products") or []
            )
            identities: List[Dict[str, str]] = []
            seen_identities: set[tuple[str, str, str]] = set()
            for product in [*resolved, selected]:
                identity = (
                    str(product.get("code") or "").strip(),
                    self._normalize_product_dialog_text(product.get("name", "")),
                    self._normalize_product_dialog_text(product.get("is_active", "")),
                )
                if identity in seen_identities:
                    continue
                seen_identities.add(identity)
                identities.append(product)

            if len(identities) >= 2:
                labels = []
                for product in identities[:2]:
                    product_name = str(product.get("name") or "").strip()
                    product_code = str(product.get("code") or "").strip()
                    is_active = str(product.get("is_active") or "").strip()
                    label = " ".join(
                        part for part in (product_code, product_name) if part
                    )
                    if is_active:
                        label = f"{is_active} {label}"
                    labels.append(label)
                query = f"сравни продукты {labels[0]} и {labels[1]}"
            else:
                original = str(context.get("original_search_query") or "").strip()
                selected_label = code or name
                query = (
                    f"{original} {selected_label}".strip()
                    if original
                    else f"сравни продукт {selected_label}"
                )

            logger.info(
                "Clarification followup -> product_compare: selected=%s query=%s",
                code or name,
                query,
            )
            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "product_filter",
                    "intent": "product_compare",
                    "reason": "product_compare_clarification_followup",
                    "search_query": query,
                },
                dict(ctx.session.state),
            )

        intent = "product_kit" if pending_intent == "product_kit" else "product_card"
        query_action = "скачать комплект документов по продукту" if intent == "product_kit" else "показать карточку продукта"
        status = str(selected.get("is_active") or "").strip()
        selected_label = name or code
        if status:
            selected_label = f"{status} {selected_label}"
        return validate_dispatcher_result(
            {"status": "ok", "route": "product_info", "intent": intent, "reason": f"{intent}_clarification_followup", "search_query": f"{query_action} {selected_label}"},
            dict(ctx.session.state),
        )

    @staticmethod
    def _to_dict(value: Any) -> Dict[str, Any]:
        """Универсальная конвертация объектов с методом to_dict() в dict."""
        if hasattr(value, "to_dict"):
            return value.to_dict()
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _product_resolution_to_state(value: Any) -> Dict[str, Any]:
        data = RootAgent._to_dict(value)
        return RootAgent._canonicalize_resolution_options(data) if data else {}

    @staticmethod
    def _canonicalize_resolution_options(data: Dict[str, Any]) -> Dict[str, Any]:
        options = data.get("options")
        if not isinstance(options, list):
            return data
        canonical_options = []
        for option in options:
            if isinstance(option, dict):
                code = str(option.get("product_code") or "").strip()
                name = str(option.get("canonical_name") or "").strip()
                is_active = str(option.get("is_active") or "").strip()
                if code and name:
                    canonical = {"code": code, "name": name}
                    if is_active:
                        canonical["is_active"] = is_active
                    canonical_options.append(canonical)
        return {**data, "options": canonical_options}

    @staticmethod
    def _product_resolutions_to_state(value: Any) -> Dict[str, Any]:
        data = RootAgent._to_dict(value)
        if not data or not isinstance(data.get("items"), list):
            return data

        unique_items, seen_keys = [], set()
        for item in data["items"]:
            if not isinstance(item, dict):
                unique_items.append(item)
                continue
            dedup_key = RootAgent._product_resolution_dedup_key(item)
            if dedup_key is not None:
                if dedup_key in seen_keys:
                    continue
                seen_keys.add(dedup_key)
            unique_items.append(RootAgent._canonicalize_resolution_options(item))

        return {**data, "items": unique_items}

    @staticmethod
    def _product_resolution_dedup_key(item: Dict[str, Any]) -> tuple[str, str, str] | None:
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
        status = str(item.get("is_active") or "").strip()
        if not status and isinstance(options, list) and options:
            first_option = options[0]
            if isinstance(first_option, dict):
                status = str(first_option.get("is_active") or "").strip()
        return product_code, normalized_name, " ".join(status.casefold().split())

    @staticmethod
    def _product_filter_resolution_to_state(value: Any) -> Dict[str, Any]:
        data = RootAgent._to_dict(value)
        if not data:
            return {}
        products = []
        for item in data.get("products") or []:
            if not isinstance(item, dict):
                continue
            product = {
                "code": str(item.get("product_code") or "").strip(),
                "name": str(item.get("canonical_name") or "").strip(),
                "is_active": str(item.get("is_active") or "").strip(),
            }
            if product["code"] and product["name"]:
                products.append({key: value for key, value in product.items() if value})
        return {
            "status": data.get("status"),
            "query": data.get("query"),
            "product_codes": data.get("product_codes") or [],
            "products": products,
            "matched_terms": data.get("matched_terms") or [],
            "unmatched_terms": data.get("unmatched_terms") or [],
            "error": data.get("error"),
        }

    @staticmethod
    def _has_strong_product_filter_identity(state: Dict[str, Any]) -> bool:
        def normalize(value: Any) -> str:
            return " ".join(re.findall(r"\w+", str(value or "").casefold()))

        products = state.get("products") or []
        for raw_term in state.get("matched_terms") or []:
            term = normalize(raw_term)
            if len(term.split()) >= 2:
                return True
            for product in products:
                if term and term in {
                    normalize(product.get("code")),
                    normalize(product.get("name")),
                }:
                    return True
        return False

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
            resolution_state = self._product_filter_resolution_to_state(result)
            if resolution_state.get("products") and not self._has_strong_product_filter_identity(
                resolution_state
            ):
                logger.debug(
                    "Ignoring product_filter resolution without strong product identity: %s",
                    resolution_state,
                )
                resolution_state = {
                    "status": "not_found",
                    "query": resolution_state.get("query") or query,
                    "product_codes": [],
                    "products": [],
                    "matched_terms": [],
                    "unmatched_terms": [],
                    "error": None,
                }
            ctx.session.state["product_filter_resolution"] = resolution_state
            logger.debug(
                "product_filter_resolution state: %s",
                ctx.session.state["product_filter_resolution"],
            )
            return

        elif intent == "product_compare":
            result = await self.product_resolver.resolve_products(query)
            ctx.session.state["product_resolutions"] = self._product_resolutions_to_state(
                result
            )
            logger.debug(
                "product_resolutions state: %s",
                ctx.session.state["product_resolutions"],
            )
            return

        elif intent in {"product_card", "product_kit"}:
            result = await self.product_resolver.resolve_product(query)
            ctx.session.state["product_resolution"] = self._product_resolution_to_state(
                result
            )
            logger.debug(
                "product_resolution state: %s",
                ctx.session.state["product_resolution"],
            )
    
    def _get_explicit_intent_dispatch(self, ctx: InvocationContext, user_text: str) -> Dict[str, Any] | None:
        """
        Перехватывает явные запросы на комплект или фильтр до вызова LLM-dispatcher.
        """
        normalized = self._normalize_product_dialog_text(user_text)
        if not normalized:
            return None

        # 1. Явный запрос комплекта/документов (но не "какие документы есть" -> это фильтр)
        is_explicit_kit = bool(RE_EXPLICIT_KIT.search(normalized))
        is_asking_list = bool(RE_ASKING_LIST.search(normalized))
        
        if is_explicit_kit and not is_asking_list:
            # ЕСЛИ ПОЛЬЗОВАТЕЛЬ ЯВНО УКАЗАЛ НОВЫЙ ПРОДУКТ, ОТДАЕМ ДИСПЕТЧЕРУ
            if not self._is_blind_followup(user_text):
                return None
            # Извлекаем продукт из контекста диалога (selected_product)
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
                code = str(product.get("code") or "").strip()
                name = str(product.get("name") or "").strip()
                if code and name:
                    product_label = f"{name} (код {code})"
                else:
                    product_label = name or code

                query = f"скачать комплект документов по продукту {product_label}".strip()

                logger.info(
                    "Explicit kit dispatch: found product code=%s name=%s label=%s",
                    code,
                    name,
                    product_label,
                )
            else:
                logger.warning("Explicit kit dispatch: product NOT found in context, using raw query")
                query = user_text

            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "product_info",
                    "intent": "product_kit",
                    "reason": "explicit_kit_short_circuit",
                    "search_query": query, 
                },
                dict(ctx.session.state),
            )

        # 2. Явный запрос списка/архива/фильтра.
        # «сравни архивные A и B» — сравнение, не список архивных продуктов.
        if RE_EXPLICIT_FILTER.search(normalized) and not RE_COMPARISON.search(
            normalized
        ):
            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "product_filter",
                    "intent": "product_filter",
                    "reason": "explicit_filter_short_circuit",
                    "search_query": user_text,
                },
                dict(ctx.session.state),
            )
        # 3. Перехват контекстного согласия ("давай", "пришли", "отправь") сразу после показа карточки продукта
        last_route = ctx.session.state.get("last_route")
        last_intent = ctx.session.state.get("last_intent")
        if last_route == "product_info" and last_intent == "product_card" and normalized in RE_CONFIRMATION_WORDS:
            product = self._find_product_in_dialog_context(ctx, user_text, allow_selected_product=True)
            if not product:
                product = self._get_selected_product_from_context(ctx)
            if not product:
                product = self._get_last_product_from_state(ctx)

            if product:
                code = str(product.get("code") or "").strip()
                name = str(product.get("name") or "").strip()
                if code and name:
                    product_label = f"{name} (код {code})"
                else:
                    product_label = name or code
                query = f"скачать комплект документов по продукту {product_label}".strip()
                logger.info("Explicit confirmation kit dispatch short-circuit: found product code=%s name=%s", code, name)
                return validate_dispatcher_result(
                    {
                        "status": "ok",
                        "route": "product_info",
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
            clarification_pick = self._match_clarification_option(ctx, user_text)
            if clarification_pick:
                return self._dispatch_clarification_followup(ctx, clarification_pick)
            
            attribute_value = self._find_attribute_value_in_dialog_context(ctx, user_text)
            if attribute_value:
                attribute_name = str(context.get("attribute_name") or "").strip()
                attribute_column = str(context.get("attribute_column") or "").strip()
                attribute_label = attribute_name or attribute_column or "selected attribute"
                query = f"покажи продукты, у которых {attribute_label}: {attribute_value}"
                return validate_dispatcher_result(
                    {
                        "status": "ok",
                        "route": "product_filter",
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
                            "route": "product_info",
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
                r"\b(параметр\w*|карточк\w*|свойств\w*|характеристик\w*|подробн\w*|покаж\w*|расскаж\w*)\b",
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
        if last_route == "product_info" and last_intent == "product_card" and normalized in RE_CONFIRMATION_WORDS:
            asks_kit = True

        if not asks_kit and not asks_card and not asks_doc:
            return None

        explicit_product = None
        # Если это не слепой follow-up (пользователь явно указал продукт),
        # используем RAM-контекст только при единственном совпадении по коду.
        # Одинаковый код у нескольких продуктов не должен выбирать первый элемент.
        if not self._is_blind_followup(user_text):
            if context and self._extract_product_codes(user_text):
                explicit_product = self._find_product_in_dialog_context(
                    ctx,
                    user_text,
                    allow_selected_product=False,
                )
            if not explicit_product:
                return None
        # 1. Сначала пытаемся найти продукт стандартным путем через RAM-контекст модулей
        product = explicit_product
        if context and not product:
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
            status = product.get("is_active") or ""
            logger.info(
                "Product followup dispatch: found product code=%s name=%s is_active=%s",
                code,
                name,
                status,
            )
            search_target = name or code
            if code and name:
                search_target = f"{name} (код {code})"
            if status:
                search_target = f"{status} {search_target}"
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

            logger.info(f"Product followup short-circuit triggered. Target: {search_target}, Route: product_info, Intent: {intent}")
            return validate_dispatcher_result(
                {
                    "status": "ok",
                    "route": "product_info",
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
    def _fallback_product_message(cls, raw: str) -> str | None:
        try:
            payload = extract_json(raw)
        except Exception:
            return None

        message = str(payload.get("message") or "").strip()
        if not message:
            return None

        return cls._format_product_answer(
            {
                "mode": payload.get("mode"),
                "message": message,
                "clarification_options": payload.get("clarification_options") or [],
            }
        )

    @staticmethod
    def _merge_non_empty_payload_fields(
        context: Dict[str, Any],
        payload: Dict[str, Any],
    ) -> None:
        """Fill missing fallback context without replacing validated state."""
        for key in ("mode", "resolved_product", "clarification_options", "products"):
            if not context.get(key) and payload.get(key):
                context[key] = payload[key]

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
        validator: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]] | None,
        log_label: str,
        validation_error_user_message: str,
        response_schema: type[Any] | None = None,
        tool_calls_state_key: str | None = None,
        tool_events_state_key: str | None = None,
        validation_tool_calls_state_key: str | None = None,
        validation_tool_events_state_key: str | None = None,
        require_non_empty_object: bool = False,
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
            response_schema=response_schema,
            tool_calls_state_key=tool_calls_state_key,
            tool_events_state_key=tool_events_state_key,
            validation_tool_calls_state_key=validation_tool_calls_state_key,
            validation_tool_events_state_key=validation_tool_events_state_key,
            require_non_empty_object=require_non_empty_object,
        ):
            yield event

    async def _prepare_pipeline_context(self, ctx: Any) -> PipelineContext:
        """
        Извлекает и нормализует данные из сессии и текущего вызова ctx.
        Инициализирует базовое состояние для дальнейшей обработки.
        """
        user_text = self._extract_user_text(ctx)
        logger.info("Processing message: %s", truncate_for_log(user_text, 200))

        # Блок автоматического восстановления контекста (защита от 409 Conflict)
        sess_id = getattr(ctx.session, "id", "")
        clean_id = sess_id.split("_")[0] if sess_id else ""
        if clean_id:
            # Проверяем существующую БД: если там пусто, значит бот стёр историю через /reset
            if await is_history_empty_by_global_id(clean_id):
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
        await self._trim_dialog_memory(ctx)
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
        self._prepare_dialog_context_state(ctx)
        self._prepare_owasp_input(ctx, user_text)
        return PipelineContext(
            ctx=ctx,
            user_text=user_text,
            clean_text=user_text.strip(),
            session_id=sess_id,
            last_route=ctx.session.state.get("last_route"),
            last_intent=ctx.session.state.get("last_intent"),
            last_search_query=ctx.session.state.get("last_search_query"),
            last_product=ctx.session.state.get("last_product"),
            product_dialog_context=ctx.session.state.get(PRODUCT_DIALOG_CONTEXT_STATE_KEY),
            doc_search_context=ctx.session.state.get("_doc_search_result_parsed"),
            metadata={"clean_id": clean_id},
        )
    
    async def _check_safety_guardrails(self, pipeline_ctx: PipelineContext) -> AsyncGenerator[Event, None]:
        """Проверки Stage 0: пустой ввод, синхронизация профиля и OWASP фильтрация."""
        ctx = pipeline_ctx.ctx
        user_text = pipeline_ctx.user_text

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

        # 1. Очищаем старый стейт OWASP, который мог приехать из кэша прошлых сессий
        ctx.session.state.pop("_owasp_result_parsed", None)
        ctx.session.state.pop("owasp_result_json", None)
        # Запуск OWASP агента
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
        logger.info("Glossary terms found: %s", len(ctx.session.state["from_glossary"]))

    async def _try_short_circuit(self, pipeline_ctx: PipelineContext) -> Optional[Dict[str, Any]]:
        """
        Единая точка(Stage 1 & 2).
            Stage 1 Short-circuit: явный перехват команд комплектов/архивов без LLM.
            Stage 2 Short-circuit: контекстный follow-up по продуктам или спискам документов.
        Поочередно проверяет эвристики и при совпадении фиксирует результат.
        """
        ctx, text = pipeline_ctx.ctx, pipeline_ctx.user_text

        # Последовательность проверок по приоритету
        resolvers = (
            ("explicit_intent", self._get_explicit_intent_dispatch),
            ("contextual_smalltalk", self._contextual_smalltalk_followup_dispatch),
            ("product_followup", self._product_followup_dispatch),
            ("doc_list_followup", self._doc_list_followup_dispatch),
        )

        for stage_tag, resolver in resolvers:
            if dispatch := resolver(ctx, text):
                # Единая фиксация состояния сессии и логирование
                ctx.session.state["_dispatcher_result_parsed"] = dispatch
                ctx.session.state.pop("dispatcher_result_json", None)
                logger.info(
                    "Dispatcher skipped (%s short-circuit): reason=%s intent=%s search_query=%s",
                    stage_tag,
                    dispatch.get("reason"),
                    dispatch.get("intent"),
                    dispatch.get("search_query"),
                )
                return dispatch

        return None

    async def _run_llm_dispatcher(self, pipeline_ctx: PipelineContext) -> AsyncGenerator[Event, None]:
        """Основной вызов LLM-диспетчера роутинга."""
        ctx = pipeline_ctx.ctx
        user_text = pipeline_ctx.user_text
        # На всякий случай обновляем общий контекст перед dispatcher
        self._prepare_dialog_context_state(ctx)
        ctx.session.state["dispatcher_user_query"] = user_text
        ctx.session.state.pop("dispatcher_result_json", None)
        ctx.session.state.pop("_dispatcher_result_parsed", None)

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

    def _enrich_product_query(self, ctx: InvocationContext, dispatch: Dict[str, Any], user_text: str) -> None:
        """Вспомогательный метод для обогащения контекстных запросов продуктов."""
        sq_clean = dispatch.get("search_query", "").strip().lower()
        # Обогащение запроса для сравнения продуктов
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
                # Если продукты из контекста не упомянуты, проверяем, не является ли это запросом на сравнение новых продуктов
                if not mentioned:
                    has_explicit_codes = bool(self._extract_product_codes(user_text))
                    # Паттерн для "слепого" follow-up (например, "сравни их", "чем они отличаются")
                    blind_pattern = r"(сравни|сравнить|чем\s+отличаются|в\s+чем\s+разница|какие\s+различия|их|эти|эти\s+продукты|два\s+продукта|оба|давай\s+сравним|давайте\s+сравним|сравни\s+их|сравнить\s+их)[\s?!.]*"
                    # Передаем flags=re.IGNORECASE
                    is_blind_followup = bool(re.fullmatch(blind_pattern, user_text.strip(), flags=re.IGNORECASE)) or (not has_explicit_codes and len(user_text.split()) <= 3)
                    if is_blind_followup:
                        names = [p.get("name") or p.get("code") for p in products[:2]]
                        dispatch["search_query"] = f"сравнить {' и '.join(names)}"
                        sq_clean = dispatch["search_query"].strip().lower()
                        ctx.session.state["last_search_query"] = dispatch["search_query"]
                    else:
                        # Пользователь явно указал новые продукты для сравнения, не подменяем запрос
                        logger.info("Skipping product_compare enrichment: user specified new products.")
        # Обогащение местоимений
        pronoun_triggers = [
            "нем", "о нем", "ней", "о ней", "этом", "об этом", 
            "программе", "продукт", "продукте", "программа", "подробнее", "о нем подробнее"
        ]
        if sq_clean in pronoun_triggers or not sq_clean:
            if product := (self._get_selected_product_from_context(ctx) or self._get_last_product_from_state(ctx)):
                code, name = product.get("code") or "", product.get("name") or ""
                # Переопределяем абстрактное "нем" на жесткий поисковый запрос для агента продуктов
                status = product.get("is_active") or ""
                target = name or code
                dispatch["search_query"] = f"продукт {status} {target}".strip()
                ctx.session.state["last_search_query"] = dispatch["search_query"]

    async def _execute_target_agent(
        self, dispatch: Dict[str, Any], pipeline_ctx: PipelineContext
    ) -> AsyncGenerator[Event, None]:
        """Обогащение контекста, маршрутизация в целевой leaf-агент и сохранение истории."""
        ctx = pipeline_ctx.ctx
        user_text = pipeline_ctx.user_text
        route = dispatch["route"]
        # Сохраняем контекст текущего хода для следующих реплик
        ctx.session.state["last_user_query"] = user_text
        ctx.session.state["last_route"] = route
        ctx.session.state["last_intent"] = dispatch["intent"]
        # не затираем last_search_query пустой строкой при follow-up
        new_search_query = dispatch.get("search_query", "")
        if new_search_query:
            ctx.session.state["last_search_query"] = new_search_query

        
        # 1. Поиск документов
        if route == "doc_search":
            async for event in self._handle_doc_search(
                ctx,
                user_text,
                dispatch["intent"],
                dispatch.get("search_query", ""),
            ):
                yield event
        # 2. Персональная рекомендация по профилю клиента
        elif route == "advisor":
            async for event in self._handle_advisor(
                ctx,
                user_text,
                dispatch.get("search_query", ""),
            ):
                yield event
        # 3. Подбор продуктов
        elif route in {"product_info", "product_filter"}:
            self._enrich_product_query(ctx, dispatch, user_text)
            handler = (
                self._handle_product_info
                if route == "product_info"
                else self._handle_product_filter
            )
            async for event in handler(
                ctx,
                user_text,
                dispatch["search_query"],
                dispatch["intent"],
            ):
                yield event
        # 4. База знаний / FAQ
        elif route == "kb_answer":
            async for event in self._handle_kb_answer(
                ctx,
                user_text,
                dispatch.get("search_query", ""),
                dispatch.get("intent", "kb_answer"),
            ):
                yield event
        # 5. Smalltalk
        elif route == "smalltalk":
            async for event in self._handle_smalltalk(
                ctx,
                user_text,
                dispatch.get("intent", "smalltalk"),
            ):
                yield event
        else:        
            # Fallback для неизвестного маршрута
            logger.warning("Unknown route '%s', falling back to smalltalk", dispatch.get("route"))
            async for event in self._handle_smalltalk(
                ctx,
                user_text,
                "unknown_route",
            ):
                yield event
        # записываем контекст и в историю
        final_text = self._get_required_state_text(ctx, "_root_final_text")
        yield self._build_final_event_with_history(ctx, user_text, final_text)

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        # Подготовка контекста и валидация
        pipeline_ctx = await self._prepare_pipeline_context(ctx)
        try:
            # 1. Safety / OWASP проверки (Short-circuit Stage 0)
            async for event in self._check_safety_guardrails(pipeline_ctx):
                yield event
            # Если запрос заблокирован OWASP или это был системный запрос профиля — выходим
            owasp_state = ctx.session.state.get("_owasp_result_parsed") or {}
            if owasp_state.get("status") == "blocked" or not pipeline_ctx.user_text or is_bot_user_profile_injection_message(pipeline_ctx.user_text):
                return
            # 2. Short-circuits (Stage 1 & Stage 2: Явные команды, пагинация, контекстный follow-up)
            if dispatch := await self._try_short_circuit(pipeline_ctx):
                # Исполнение решения, принятого через явный шорткат.
                async for event in self._execute_target_agent(dispatch, pipeline_ctx):
                    yield event
                return

            # 3. LLM Диспетчеризация (Основной роутинг)
            async for event in self._run_llm_dispatcher(pipeline_ctx):
                yield event
            dispatch_decision = self._get_required_state_dict(ctx, "_dispatcher_result_parsed")
            logger.info(
                "Dispatcher result: route=%s intent=%s search_query=%s",
                dispatch_decision["route"],
                dispatch_decision["intent"],
                dispatch_decision["search_query"],
            ) 
            # 4. Исполнение целевого Leaf-агента + Синхронизация состояния и Телеметрия
            async for event in self._execute_target_agent(dispatch_decision, pipeline_ctx):
                yield event

        except AgentValidationFailure as exc:
            logger.warning(
                "RootAgent stopped after validation failure: agent=%s error=%s raw=%s",
                exc.log_label,
                exc.validation_error,
                truncate_for_log(exc.raw, 500),
            )
            # Определяем, какой агент работал
            agent_name = next((name for name in ("product_info", "product_filter", "advisor", "kb_answer", "smalltalk", "dispatcher", "doc_search") if name in exc.log_label), None)
            # Собираем контекст из состояния
            context: Dict[str, Any] = {
                "validation_error": exc.validation_error,
            }
            # Поисковый запрос — в разных ключах для разных агентов
            context["search_query"] = (
                ctx.session.state.get("product_info_search_query")
                or ctx.session.state.get("product_filter_search_query")
                or ctx.session.state.get("advisor_search_query")
                or ctx.session.state.get("doc_search_query")
                or ctx.session.state.get("search_query")
                or ctx.session.state.get("dispatcher_user_query")
                or ""
            )
            # Специфичные данные для каждого агента
            if agent_name in {"product_info", "product_filter"}:
                parsed_key = (
                    "_product_info_result_parsed"
                    if agent_name == "product_info"
                    else "_product_filter_result_parsed"
                )
                parsed = ctx.session.state.get(parsed_key) or {}
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
            elif agent_name == "smalltalk":
                parsed = ctx.session.state.get("_smalltalk_result_parsed") or {}
                context["mode"] = parsed.get("mode", "")
                context["source"] = parsed.get("source", "")
            elif agent_name == "dispatcher":
                parsed = ctx.session.state.get("_dispatcher_result_parsed") or {}
                context["route"] = parsed.get("route", "")
                context["intent"] = parsed.get("intent", "")
            # Пытаемся извлечь данные из сырого ответа
            payload = {}
            if exc.log_label in {"product_info_result_json", "product_filter_result_json"}:
                try:
                    payload = extract_json(exc.raw)
                    # Raw fields can be newer than parsed state when validation failed.
                    self._merge_non_empty_payload_fields(context, payload)
                except Exception:
                    pass
            # Пытаемся извлечь сообщение из сырого ответа 
            product_tool_usage_failure = (
                exc.log_label in {"product_info_result_json", "product_filter_result_json"}
                and "tool_usage" in exc.validation_error
            )
            # Если продуктовый агент упал на валидации, но в сыром ответе есть реальные продукты,
            # сохраняем контекст, чтобы follow-up вопросы не теряли состояние диалога.
            if (
                exc.log_label in {"product_info_result_json", "product_filter_result_json"}
                and not product_tool_usage_failure
            ):
                fallback_mode = str(payload.get("mode") or "").strip()
                fallback_products = self._normalize_dialog_products(
                    payload.get("products") or []
                )
                fallback_resolved = payload.get("resolved_product")

                has_useful_product_context = (
                    fallback_mode
                    in {
                        "product_compare",
                        "product_filter",
                        "product_card",
                        "product_kit",
                    }
                    and (
                        fallback_products
                        or (
                            isinstance(fallback_resolved, dict)
                            and (
                                fallback_resolved.get("code")
                                or fallback_resolved.get("name")
                            )
                        )
                    )
                )
                if has_useful_product_context:
                    try:
                        self._store_product_dialog_context(ctx, payload)

                        if isinstance(fallback_resolved, dict) and (
                            fallback_resolved.get("code")
                            or fallback_resolved.get("name")
                        ):
                            self._set_last_product_from_result(
                                ctx,
                                payload,
                                fallback_mode,
                            )

                        logger.info(
                            "Fallback product dialog context saved after validation failure: "
                            "mode=%s products_count=%s",
                            fallback_mode,
                            len(fallback_products),
                        )
                    except Exception:
                        logger.exception(
                            "Failed to save fallback product dialog context"
                        )
            legacy_message = (
                self._fallback_product_message(exc.raw)
                if (
                    exc.log_label in {"product_info_result_json", "product_filter_result_json"}
                    and not product_tool_usage_failure
                )
                else None
            )
            # Умный fallback
            smart_message = None
            if not legacy_message:
                smart_message = generate_agent_fallback(
                    user_text=pipeline_ctx.user_text,
                    error_type="validation_failure",
                    agent_name=agent_name,
                    context=context,
                )
            final_fallback_message = legacy_message or smart_message or exc.user_message
            if exc.log_label in {"product_info_result_json", "product_filter_result_json"}:
                logger.debug(
                    "product fallback diagnostics: legacy_used=%s smart_used=%s "
                    "blocked_by_tool_usage=%s mode=%s resolved_product=%s "
                    "clarification_options_count=%s message_preview=%s",
                    bool(legacy_message),
                    bool(smart_message),
                    product_tool_usage_failure,
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
                pipeline_ctx.user_text,
                final_fallback_message,
            )

        except Exception as exc:
            if is_response_schema_configuration_error(exc):
                logger.error(
                    "RootAgent response schema configuration failure: %s",
                    exc,
                    exc_info=True,
                )
                message = RESPONSE_SCHEMA_CONFIGURATION_ERROR_MESSAGE
            else:
                logger.error("RootAgent failure: %s", exc, exc_info=True)
                message = (
                    f"DEBUG: {type(exc).__name__}: {exc}"
                    if DEBUG_EXCEPTIONS
                    # fallback при нескольких сообщениях подряд
                    else VALIDATION_ERROR_USER_MESSAGE
                )

            yield self._build_final_event_with_history(ctx, pipeline_ctx.user_text, message)
    
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

    async def _prepare_leaf_query(self, ctx: InvocationContext, search_query: str, user_message: str) -> str:
        """Общий helper для подгрузки профиля пользователя и обогащения запроса через глоссарий."""
        # Передаем данные профиля и маршрутизации
        for key, value in self._get_user_profile(ctx).items():
            # Распаковываем все поля профиля в корневой state.
            ctx.session.state[key] = value
        base_search_query = (search_query or user_message).strip()
        if self._find_attribute_value_in_dialog_context(ctx, user_message) is not None:
            ctx.session.state["from_glossary"] = []
            return base_search_query
        return await self.glossary_lookup.expand_search_query(base_search_query)

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
        # Общий контекст уже подготовлен в _prepare_pipeline_context,
        # но можно обновить на всякий случай.
        self._prepare_dialog_context_state(ctx)
        # Если после KB-ответа вы хотите сбросить продуктовый контекст,
        # делаем это ПОСЛЕ того, как общий контекст уже записан.
        self._clear_product_dialog_context(ctx)
        effective_search_query = await self._prepare_leaf_query(ctx, search_query, user_message)
        logger.info(
            "kb_answer route: query=%s intent=%s",
            truncate_for_log(effective_search_query, 300),
            intent,
        )
        ctx.session.state["search_query"] = effective_search_query
        ctx.session.state["faq_collection"] = self.faq_collection
        ctx.session.state["kb_answer_collection"] = self.kb_collection
        ctx.session.state["intent"] = intent

        async for event in run_kb_agent_with_self_correction(
            orchestrator=self,
            ctx=ctx,
            agent=self.kb_answer_agent,
            output_key="kb_answer_result_json",
            parsed_state_key="_kb_answer_result_parsed",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
            max_retries=3,
        ):
            yield event
        kb_answer = self._get_required_state_dict(ctx, "_kb_answer_result_parsed")
        ctx.session.state["_root_final_text"] = format_text_answer(kb_answer["message"])

    async def _handle_smalltalk(
        self,
        ctx: InvocationContext,
        user_message: str,
        intent: str,
    ) -> AsyncGenerator[Event, None]:
        """
        Запуск smalltalk_agent для приветствий, прощаний и светской беседы.
        """
        logger.info(
            "smalltalk route: user_message=%s intent=%s",
            truncate_for_log(user_message, 300),
            intent,
        )
        # Обновляем общий контекст на текущий ход
        self._prepare_dialog_context_state(ctx)
        ctx.session.state["intent"] = intent
        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.smalltalk_agent,
            output_key="smalltalk_result_json",
            parsed_state_key="_smalltalk_result_parsed",
            validator=validate_smalltalk_result,
            log_label="smalltalk_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
        ):
            yield event

        smalltalk = self._get_required_state_dict(ctx, "_smalltalk_result_parsed")
        # Если smalltalk выбрал продукт, root сохраняет его в last_product.
        self._apply_smalltalk_product_selection(ctx, smalltalk)
        ctx.session.state["_root_final_text"] = format_text_answer(smalltalk["message"])

    async def _handle_product_filter(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
        intent: str,
    ) -> AsyncGenerator[Event, None]:
        if intent not in {"product_filter", "product_compare", "product_attribute_values"}:
            raise ValueError(f"product_filter route does not support intent {intent!r}")

        effective_search_query = await self._prepare_leaf_query(ctx, search_query, user_message)
        logger.info(
            "product_filter route: query=%s intent=%s",
            truncate_for_log(effective_search_query, 300),
            intent,
        )
        ctx.session.state["product_filter_intent"] = intent
        ctx.session.state["product_filter_search_query"] = effective_search_query
        await self._prepare_product_resolution_state(ctx, effective_search_query, intent)

        if intent == "product_compare":
            resolutions = ctx.session.state.get("product_resolutions")
            fallback_clarification = self._compare_fallback_clarification(resolutions)
            if fallback_clarification:
                ctx.session.state["_root_final_text"] = self._format_product_answer(
                    fallback_clarification
                )
                self._store_product_dialog_context(ctx, fallback_clarification)
                ctx.session.state.pop("_bot_action", None)
                return
            missing_message = self._compare_not_found_message(resolutions)
            if missing_message:
                product_result = {
                    "mode": "no_data",
                    "message": missing_message,
                    "clarification_options": [],
                    "resolved_product": None,
                    "products": [],
                }
                ctx.session.state["_root_final_text"] = self._format_product_answer(
                    product_result
                )
                self._store_product_dialog_context(ctx, product_result)
                ctx.session.state.pop("_bot_action", None)
                return

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.product_filter_content_agent,
            output_key="product_filter_content_result_json",
            parsed_state_key="_product_filter_content_result_parsed",
            validator=None,
            log_label="product_filter_content_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
            tool_calls_state_key="_product_filter_content_tool_calls",
            tool_events_state_key="_product_filter_content_tool_events",
            require_non_empty_object=True,
        ):
            yield event

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.product_filter_format_agent,
            output_key="product_filter_result_json",
            parsed_state_key="_product_filter_result_parsed",
            validator=validate_product_filter_result,
            log_label="product_filter_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
            validation_tool_calls_state_key="_product_filter_content_tool_calls",
            validation_tool_events_state_key="_product_filter_content_tool_events",
        ):
            yield event

        product_result = self._get_required_state_dict(ctx, "_product_filter_result_parsed")
        resolved = product_result.get("resolved_product")
        if resolved and (resolved.get("name") or resolved.get("code")):
            current_context = self._get_product_dialog_context(ctx)
            current_context["selected_product"] = {
                "code": resolved.get("code", ""),
                "name": resolved.get("name", ""),
                "folder_kit": resolved.get("folder_kit"),
            }
            ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = current_context

        ctx.session.state["_root_final_text"] = self._format_product_answer(
            product_result,
            resolved_products=self._resolved_products_for_clarification_filter(ctx),
        )
        if intent == "product_compare":
            fallback_notice = self._compare_fallback_notice(
                ctx.session.state.get("product_resolutions")
            )
            if fallback_notice:
                ctx.session.state["_root_final_text"] = "\n\n".join(
                    [fallback_notice, ctx.session.state["_root_final_text"]]
                )
        self._store_product_dialog_context(ctx, product_result)
        # сохраняем last_product, если агент выбрал конкретный продукт
        self._set_last_product_from_result(
            ctx,
            product_result,
            product_result.get("mode"),
        )
        ctx.session.state.pop("_bot_action", None)

    @staticmethod
    def _advisor_profile_from_context(ctx: InvocationContext) -> AdvisorClientProfile:
        """Восстанавливает типизированный профиль из постоянного advisor-контекста."""
        value = ctx.session.state.get(ADVISOR_DIALOG_CONTEXT_STATE_KEY)
        if not isinstance(value, dict):
            return AdvisorClientProfile()
        return AdvisorClientProfile.model_validate(value.get("profile") or {})

    @staticmethod
    def _advisor_selected_match(
        selected: AdvisorSelectedClientType | None,
    ) -> dict[str, Any] | None:
        """Сериализует единственный проверенный тип клиента."""
        if selected is None:
            return None
        return selected.model_dump(mode="json")

    def _store_advisor_dialog_context(
        self,
        ctx: InvocationContext,
        *,
        profile: AdvisorClientProfile,
        content: AdvisorContentResult,
        ranking: AdvisorRankingResult | None,
    ) -> None:
        """Сохраняет один версионированный результат advisor без временных ключей."""
        selected = content.selected_client_type
        ctx.session.state[ADVISOR_DIALOG_CONTEXT_STATE_KEY] = {
            "schema_version": ADVISOR_DIALOG_CONTEXT_SCHEMA_VERSION,
            "profile": profile.model_dump(mode="json"),
            "selected_client_type": self._advisor_selected_match(selected),
            "primary_client_type": (
                selected.definition.profile_name
                if selected
                else None
            ),
            "missing_fields": list(content.missing_fields),
            "candidate_products": [
                product.model_dump(mode="json") for product in content.products
            ],
            "top_products": (
                [item.model_dump(mode="json") for item in ranking.top_products]
                if ranking
                else []
            ),
            "exclusions": (
                [item.model_dump(mode="json") for item in ranking.excluded_candidates]
                if ranking
                else []
            ),
            "selected_product": None,
            "scoring_policy_version": (
                ranking.scoring_policy_version
                if ranking
                else self.advisor_ranking_service.policy.version
            ),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }

    async def _run_advisor_formatter(
        self,
        ctx: InvocationContext,
        payload: dict[str, Any],
    ) -> AsyncGenerator[Event, None]:
        """Передает форматтеру только уже проверенный детерминированный результат."""
        ctx.session.state["advisor_ranking_result_json"] = json.dumps(
            payload,
            ensure_ascii=False,
            default=str,
        )
        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.advisor_format_agent,
            output_key="advisor_result_json",
            parsed_state_key="_advisor_result_parsed",
            validator=validate_advisor_final_result,
            log_label="advisor_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
        ):
            yield event

    async def _handle_advisor(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
    ) -> AsyncGenerator[Event, None]:
        """Выполняет проверенный advisor-пайплайн и сохраняет итоговый контекст."""
        current_profile = self._advisor_profile_from_context(ctx)
        effective_query = str(search_query or user_message).strip()
        ctx.session.state["advisor_search_query"] = effective_query
        ctx.session.state["search_query"] = effective_query
        ctx.session.state["advisor_client_profile"] = current_profile.model_dump(mode="json")
        ctx.session.state["advisor_client_profile_json"] = current_profile.model_dump_json()
        ctx.session.state["advisor_minimum_client_type_confidence"] = (
            self.advisor_minimum_client_type_confidence
        )
        ctx.session.state["advisor_source_turn"] = str(
            getattr(ctx, "invocation_id", "") or ""
        )
        ctx.session.state["advisor_updated_at"] = datetime.now(timezone.utc).isoformat()
        advisor_context = ctx.session.state.get(ADVISOR_DIALOG_CONTEXT_STATE_KEY) or {}
        ctx.session.state["advisor_dialog_context_json"] = json.dumps(
            advisor_context,
            ensure_ascii=False,
            default=str,
        )
        log_advisor_input_state(ctx)

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.advisor_content_agent,
            output_key="advisor_content_result_json",
            parsed_state_key="_advisor_content_result_parsed",
            validator=validate_advisor_content_result,
            log_label="advisor_content_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
            tool_calls_state_key="_advisor_content_tool_calls",
            tool_events_state_key="_advisor_content_tool_events",
        ):
            yield event

        content = AdvisorContentResult.model_validate(
            self._get_required_state_dict(ctx, "_advisor_content_result_parsed")
        )
        merge_result = merge_advisor_profile(current_profile, content.profile_patch)
        if merge_result.conflicts:
            raise ValueError("Advisor profile contains unresolved explicit-value conflicts")
        profile = merge_result.profile
        selected = content.selected_client_type
        if selected is not None:
            validate_selected_client_type(
                selected,
                client_profile=profile,
                minimum_confidence=self.advisor_minimum_client_type_confidence,
            )

        if content.mode == "needs_clarification":
            self._store_advisor_dialog_context(
                ctx,
                profile=profile,
                content=content,
                ranking=None,
            )
            ctx.session.state["_root_final_text"] = str(
                content.clarification_question or ""
            ).strip()
            return

        if content.mode == "no_data":
            async for event in self._run_advisor_formatter(
                ctx,
                {"mode": "no_data", "no_data_reason": content.no_data_reason},
            ):
                yield event
            final_result = self._get_required_state_dict(ctx, "_advisor_result_parsed")
            self._store_advisor_dialog_context(
                ctx,
                profile=profile,
                content=content,
                ranking=None,
            )
            ctx.session.state["_root_final_text"] = format_text_answer(
                final_result["message"]
            )
            return

        if selected is None:
            raise ValueError("Advisor candidates require a validated Client Type")

        ranking = self.advisor_ranking_service.rank(
            products=[product.to_product_facts() for product in content.products],
            selected_client_type=selected,
        )
        ctx.session.state["advisor_ranking_result"] = ranking.model_dump(mode="json")
        format_payload = {
            "mode": "recommendation",
            **ranking.model_dump(mode="json"),
        }
        if not ranking.top_products:
            format_payload = {
                "mode": "no_data",
                "no_data_reason": "Все проверенные продукты исключены обязательными правилами.",
                "ranking": ranking.model_dump(mode="json"),
            }
        async for event in self._run_advisor_formatter(ctx, format_payload):
            yield event

        final_result = self._get_required_state_dict(ctx, "_advisor_result_parsed")
        self._store_advisor_dialog_context(
            ctx,
            profile=profile,
            content=content,
            ranking=ranking,
        )
        ctx.session.state["_root_final_text"] = format_text_answer(
            final_result["message"]
        )

    async def _handle_product_info(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
        intent: str,
    ) -> AsyncGenerator[Event, None]:
        if intent not in {"product_card", "product_kit"}:
            raise ValueError(f"product_info route does not support intent {intent!r}")

        effective_search_query = await self._prepare_leaf_query(ctx, search_query, user_message)
        logger.info(
            "product_info route: query=%s intent=%s",
            truncate_for_log(effective_search_query, 300),
            intent,
        )
        ctx.session.state["product_info_intent"] = intent
        ctx.session.state["product_info_search_query"] = effective_search_query
        await self._prepare_product_resolution_state(ctx, effective_search_query, intent)

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.product_info_content_agent,
            output_key="product_info_content_result_json",
            parsed_state_key="_product_info_content_result_parsed",
            validator=None,
            log_label="product_info_content_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
            tool_calls_state_key="_product_info_content_tool_calls",
            tool_events_state_key="_product_info_content_tool_events",
            require_non_empty_object=True,
        ):
            yield event

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.product_info_format_agent,
            output_key="product_info_result_json",
            parsed_state_key="_product_info_result_parsed",
            validator=validate_product_info_result,
            log_label="product_info_result_json",
            validation_error_user_message=VALIDATION_ERROR_USER_MESSAGE,
            validation_tool_calls_state_key="_product_info_content_tool_calls",
            validation_tool_events_state_key="_product_info_content_tool_events",
        ):
            yield event

        product_result = self._get_required_state_dict(ctx, "_product_info_result_parsed")
        resolved = product_result.get("resolved_product")
        if resolved and (resolved.get("name") or resolved.get("code")):
            current_context = self._get_product_dialog_context(ctx)
            current_context["selected_product"] = {
                "code": resolved.get("code", ""),
                "name": resolved.get("name", ""),
                "folder_kit": resolved.get("folder_kit"),
            }
            ctx.session.state[PRODUCT_DIALOG_CONTEXT_STATE_KEY] = current_context

        ctx.session.state["_root_final_text"] = self._format_product_answer(
            product_result,
            resolved_products=self._resolved_products_for_clarification_filter(ctx),
        )
        self._store_product_dialog_context(ctx, product_result)
        # сохраняем last_product после карточки / комплекта
        self._set_last_product_from_result(
            ctx,
            product_result,
            product_result.get("mode"),
        )

        if product_result["mode"] == "product_kit":
            resolved_product = product_result.get("resolved_product") or {}
            product_code = str(resolved_product.get("code") or "").strip()
            if product_code:
                ctx.session.state["_bot_action"] = {
                    "type": "send_product_kit",
                    "product_code": product_code,
                    "product_name": str(resolved_product.get("name") or "").strip(),
                    "folder_kit": str(resolved_product.get("folder_kit") or "").strip(),
                }
                return

        ctx.session.state.pop("_bot_action", None)
