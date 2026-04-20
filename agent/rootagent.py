import re
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional

from google.genai import types as genai_types
from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event, EventActions

from utils.logger import setup_logger
from utils.doc_search_format import extract_download_ranks
from .config import DEBUG_EXCEPTIONS, FAQ_DOCUMENTS_COLLECTION, KB_DOCUMENTS_COLLECTION
from .helpers import truncate_for_log, format_text_answer, format_reject_answer
from .json_leaf_runner import run_json_leaf_agent
from .agents.owasp_agent import validate_owasp_result
from .agents.dispatcher_agent import validate_dispatcher_result
from .agents.kb_answer_agent import validate_kb_answer_result
from .agents.doc_search_orchestrator import DocSearchOrchestrator

logger = setup_logger("root_agent", "agent.log")

BOT_USER_PROFILE_MESSAGE_PREFIX = "РљРѕРЅС‚РµРєСЃС‚ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ:"

def is_bot_user_profile_injection_message(text: str) -> bool:
    t = (text or "").lstrip()
    return t.startswith(BOT_USER_PROFILE_MESSAGE_PREFIX)

class RootAgent(BaseAgent):
    """
    РћСЂРєРµСЃС‚СЂР°С‚РѕСЂ С†РµРїРѕС‡РєРё:
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
        РР·РІР»РµРєР°РµС‚ РїСЂРѕС„РёР»СЊ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ:
        1) СЃРЅР°С‡Р°Р»Р° РёР· ctx.user.state вЂ” РґРѕР»РіРѕР¶РёРІСѓС‰РµРµ СЃРѕСЃС‚РѕСЏРЅРёРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЏ,
        2) Р·Р°С‚РµРј fallback РёР· ctx.session.state вЂ” РµСЃР»Рё РїСЂРѕС„РёР»СЊ РїСЂРёРµС…Р°Р» С‚РѕР»СЊРєРѕ РІ СЃРµСЃСЃРёСЋ.
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
        РР·РІР»РµРєР°РµРј С‚РµРєСЃС‚ С‚РµРєСѓС‰РµРіРѕ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРѕРіРѕ СЃРѕРѕР±С‰РµРЅРёСЏ РёР· InvocationContext.
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
        """Р¤РёРЅР°Р»СЊРЅРѕРµ СЃРѕР±С‹С‚РёРµ root-Р°РіРµРЅС‚Р°"""
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
        """РћС‡РёСЃС‚РєР° СѓРєР°Р·Р°РЅРЅС‹С… РєР»СЋС‡РµР№ РёР· state"""
        for key in keys:
            ctx.session.state.pop(key, None)

    @staticmethod
    def _pagination_intent_from_message(user_text: str) -> Optional[str]:
        """
        РљРѕСЂРѕС‚РєРёРµ СЂРµРїР»РёРєРё Р±РµР· РЅРѕРІРѕР№ С‚РµРјС‹ вЂ” РєРѕРјР°РЅРґС‹ РїР°РіРёРЅР°С†РёРё (show_more / show_all).
        РќРµ С‚СЂРµР±СѓРµС‚ doc_search_list_items РІ state: РїСЂРё РїСѓСЃС‚РѕРј СЃРїРёСЃРєРµ РѕСЂРєРµСЃС‚СЂР°С‚РѕСЂ РІРµСЂРЅС‘С‚
        РїРѕРЅСЏС‚РЅСѓСЋ РѕС€РёР±РєСѓ; Р·Р°С‚Рѕ РЅРµ Р·Р°РїСѓСЃРєР°РµС‚СЃСЏ Р»РѕР¶РЅС‹Р№ doc_search, РµСЃР»Рё СЃРµСЃСЃРёСЏ РЅРµ СЃРѕС…СЂР°РЅРёР»Р°СЃСЊ.
        """
        t = user_text.strip().lower().replace("С‘", "Рµ")
        t = re.sub(r"\s+", " ", t)
        if not t:
            return None
        if re.fullmatch(r"РІСЃРµ[!?.вЂ¦]*", t):
            return "show_all"
        if re.fullmatch(r"(РїРѕР»РЅРѕСЃС‚СЊСЋ|С†РµР»РёРєРѕРј)([!?.вЂ¦]*)", t):
            return "show_all"
        if re.fullmatch(r"all([!?.вЂ¦]*)", t):
            return "show_all"
        if re.fullmatch(r"(РїРѕРєР°Р¶Рё|РґР°Р№|РІС‹РІРµРґРё|РѕС‚РєСЂРѕР№)\s+РІСЃРµ([!?.вЂ¦]*)", t):
            return "show_all"
        if re.fullmatch(r"(РїРѕРєР°Р¶Рё|РІС‹РІРµРґРё)\s+РїРѕР»РЅРѕСЃС‚СЊСЋ([!?.вЂ¦]*)", t):
            return "show_all"
        if re.fullmatch(r"(РµС‰С‘|РµС‰Рµ|Р±РѕР»СЊС€Рµ|РґР°Р»РµРµ|СЃР»РµРґСѓСЋС‰РёРµ)([!?.вЂ¦]*)", t):
            return "show_more"
        if re.fullmatch(r"(РµС‰С‘|РµС‰Рµ)\s+(С„Р°Р№Р»С‹|РґРѕРєСѓРјРµРЅС‚С‹)([!?.вЂ¦]*)", t):
            return "show_more"
        if re.fullmatch(r"(next|more)([!?.вЂ¦]*)", t):
            return "show_more"
        return None

    def _get_required_state_dict(self, ctx: InvocationContext, key: str) -> Dict[str, Any]:
        """РџРѕР»СѓС‡РµРЅРёРµ РѕР±СЏР·Р°С‚РµР»СЊРЅРѕРіРѕ dict РёР· state"""
        value = ctx.session.state.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"State key '{key}' must be dict, got {type(value)}")
        return value

    def _get_required_state_text(self, ctx: InvocationContext, key: str) -> str:
        """РџРѕР»СѓС‡РµРЅРёРµ РѕР±СЏР·Р°С‚РµР»СЊРЅРѕРіРѕ С‚РµРєСЃС‚Р° РёР· state"""
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
        """Р—Р°РїСѓСЃРє leaf-Р°РіРµРЅС‚Р° СЃ JSON-РІР°Р»РёРґР°С†РёРµР№ (РґРµР»РµРіРёСЂСѓРµС‚ json_leaf_runner)."""
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
                yield self._build_final_event(ctx, "РџСѓСЃС‚РѕР№ Р·Р°РїСЂРѕСЃ. РќР°РїРёС€РёС‚Рµ СЃРѕРѕР±С‰РµРЅРёРµ РµС‰С‘ СЂР°Р·.")
                return

            # РЎРёРЅС…СЂРѕРЅРёР·Р°С†РёСЏ РїСЂРѕС„РёР»СЏ РёР· Р±РѕС‚Р° (AdkApiClient.set_user_state) вЂ” РЅРµ РїРѕР»СЊР·РѕРІР°С‚РµР»СЊСЃРєРёР№ Р·Р°РїСЂРѕСЃ.
            if is_bot_user_profile_injection_message(user_text):
                logger.info("Skipping agent chain (bot user profile sync, not a user turn)")
                yield self._build_final_event(ctx, "")
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

            ranks = extract_download_ranks(user_text)
            if ranks:
                dispatch = validate_dispatcher_result(
                    {
                        "status": "ok",
                        "route": "doc_search",
                        "intent": "file_download",
                        "reason": "download_by_rank_short_circuit",
                        "search_query": "",
                    }
                )
                ctx.session.state["_dispatcher_result_parsed"] = dispatch
                ctx.session.state.pop("dispatcher_result_json", None)
                logger.info(
                    "Dispatcher skipped (file_download by rank): ranks=%s",
                    ranks,
                )
            else:
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
                        }
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
                            }
                        )
                        ctx.session.state["_dispatcher_result_parsed"] = dispatch
                        ctx.session.state.pop("dispatcher_result_json", None)
                        logger.info(
                            "Dispatcher doc_searchв†’file_download override: ranks=%s",
                            dr,
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
                else "РџСЂРѕРёР·РѕС€Р»Р° РѕС€РёР±РєР° РїСЂРё РѕР±СЂР°Р±РѕС‚РєРµ Р·Р°РїСЂРѕСЃР°. РџРѕРїСЂРѕР±СѓР№С‚Рµ РїРѕР·Р¶Рµ."
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
        Запуск kb_answer_agent для FAQ/KB-ответа или smalltalk.

        Args:
            ctx: Контекст выполнения.
            user_message: Исходный вопрос пользователя.
            search_query: Нормализованный поисковый запрос.
            intent: Тип запроса (kb_answer, smalltalk).
        """
        effective_search_query = (search_query or user_message).strip()
        logger.info("kb_answer route: query=%s intent=%s", truncate_for_log(effective_search_query, 300), intent)

        # РџРµСЂРµРјРµРЅРЅС‹Рµ РґР»СЏ РїСЂРѕРјРїС‚Р° kb_answer_agent
        user_profile = self._get_user_profile(ctx)
        # Р Р°СЃРїР°РєРѕРІС‹РІР°РµРј РІСЃРµ РїРѕР»СЏ РїСЂРѕС„РёР»СЏ РІ РєРѕСЂРµРЅСЊ state
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
        ):
            yield event

        kb_answer = self._get_required_state_dict(ctx, "_kb_answer_result_parsed")
        ctx.session.state["_root_final_text"] = format_text_answer(kb_answer["message"])

