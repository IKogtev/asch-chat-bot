import json
import os
import re
from pathlib import Path
from typing import Any, AsyncGenerator, Callable, Dict, List, Optional, Protocol

from dotenv import load_dotenv
from google.genai import types as genai_types

from google.adk.agents import BaseAgent, LlmAgent, InvocationContext
from google.adk.events import Event, EventActions
from google.adk.models.lite_llm import LiteLlm

from utils.logger import setup_logger

load_dotenv(override=True)

# =============================================================================
# CONFIG
# =============================================================================

SCRIPT_DIR = Path(__file__).parent.absolute()
PROMPTS_DIR = Path(os.getenv("AGENT_PROMPTS_DIR", str(SCRIPT_DIR / "prompts")))

LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_API_URL = os.getenv("LLM_API_URL", "").strip()
LLM_API_MODEL = os.getenv("LLM_API_MODEL", "litellm_proxy/nst-3").strip()

DOC_SEARCH_COLLECTION = os.getenv("DOC_SEARCH_COLLECTION", "documents").strip()
KB_ANSWER_COLLECTION = os.getenv("KB_ANSWER_COLLECTION", "knowledge_base").strip()
KB_TOP_K = int(os.getenv("KB_TOP_K", "5"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEBUG_EXCEPTIONS = os.getenv("DEBUG_EXCEPTIONS", "false").lower() == "true"
logger = setup_logger("agent_chain", "agent.log")


# =============================================================================
# HELPERS
# =============================================================================

def truncate_for_log(value: Any, limit: int = 1000) -> str:
    """Безопасно обрезает большие значения для логов."""
    text = "" if value is None else str(value)
    return text if len(text) <= limit else f"{text[:limit]}...<truncated>"


def load_prompt(filename: str, fallback: str) -> str:
    """Загрузить prompt из файла, иначе использовать fallback."""
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    logger.warning(f"Prompt not found: {path}")
    return fallback.strip()


def extract_json(text: str) -> Dict[str, Any]:
    """Аккуратно извлекает JSON-объект из ответа модели."""
    if not text:
        raise ValueError("Empty model response")

    text = text.strip()

    # 1. Чистый JSON
    if text.startswith("{") and text.endswith("}"):
        return json.loads(text)

    # 2. JSON внутри markdown fence
    fence_match = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, flags=re.DOTALL)
    if fence_match:
        return json.loads(fence_match.group(1))

    # 3. Первый JSON объект в тексте
    obj_match = re.search(r"(\{.*\})", text, flags=re.DOTALL)
    if obj_match:
        return json.loads(obj_match.group(1))

    raise ValueError(f"JSON object not found in model response: {text[:500]}")


def format_search_results_contract(message: str, results: List[Dict[str, Any]]) -> str:
    """
    Финальный контракт для bot_v6:
    бот умеет извлекать <bot_contract>...</bot_contract> и сохранять results.
    """
    normalized: List[Dict[str, Any]] = []
    for idx, item in enumerate(results, start=1):
        normalized.append(
            {
                "document_id": item["document_id"],
                "source_name": item["source_name"],
                "source_path": item.get("source_path"),
                "snippet": item.get("snippet", ""),
                "is_relevant": True,
                "old_rank": idx,
                "new_rank": idx,
            }
        )

    payload = {
        "mode": "search_results",
        "message": message,
        "results": normalized,
    }
    return f"<bot_contract>{json.dumps(payload, ensure_ascii=False)}</bot_contract>"


def format_text_answer(message: str) -> str:
    """Обычный текстовый ответ."""
    return (message or "").strip()


def format_reject_answer(message: str) -> str:
    """Обычный текстовый reject-ответ."""
    return (message or "").strip()


def deduplicate_results(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Минимальная дедупликация по document_id.
    Оставляем лучший результат на документ.
    """
    by_document: Dict[str, Dict[str, Any]] = {}

    for item in items:
        document_id = str(item.get("document_id") or "").strip()
        if not document_id:
            continue

        score = item.get("score")
        score_val = float(score) if isinstance(score, (int, float)) else -1.0

        existing = by_document.get(document_id)
        if existing is None:
            by_document[document_id] = dict(item)
            by_document[document_id]["_score"] = score_val
            continue

        if score_val > existing.get("_score", -1.0):
            by_document[document_id] = dict(item)
            by_document[document_id]["_score"] = score_val

    result = list(by_document.values())
    result.sort(key=lambda x: x.get("_score", -1.0), reverse=True)

    for idx, item in enumerate(result, start=1):
        item["rank"] = idx
        item.pop("_score", None)

    return result


# =============================================================================
# CONTRACT VALIDATION
# =============================================================================

def validate_owasp_result(data: Dict[str, Any]) -> Dict[str, Any]:
    required = {"status", "route", "reason"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"owasp_result missing fields: {missing}")

    if data["status"] not in {"ok", "blocked"}:
        raise ValueError(f"invalid owasp status: {data['status']}")

    if data["status"] == "ok" and data["route"] != "continue":
        raise ValueError("owasp ok result must have route=continue")

    if data["status"] == "blocked":
        if data["route"] != "reject":
            raise ValueError("owasp blocked result must have route=reject")
        if not data.get("user_message"):
            raise ValueError("owasp blocked result must have user_message")

    return data


def validate_dispatcher_result(data: Dict[str, Any]) -> Dict[str, Any]:
    required = {"status", "route", "intent", "reason", "search_query"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"dispatcher_result missing fields: {missing}")

    if data["status"] != "ok":
        raise ValueError("dispatcher status must be ok")

    if data["route"] not in {"doc_search", "kb_answer"}:
        raise ValueError(f"invalid dispatcher route: {data['route']}")

    if data["intent"] not in {"doc_search", "kb_answer", "smalltalk"}:
        raise ValueError(f"invalid dispatcher intent: {data['intent']}")

    search_query = str(data.get("search_query") or "").strip()
    if not search_query:
        raise ValueError("dispatcher search_query must be non-empty")

    if data["route"] == "doc_search" and data["intent"] != "doc_search":
        raise ValueError("doc_search route must have intent=doc_search")

    if data["route"] == "kb_answer" and data["intent"] not in {"kb_answer", "smalltalk"}:
        raise ValueError("kb_answer route must have intent=kb_answer|smalltalk")

    data["search_query"] = search_query
    return data


def validate_doc_search_result(data: Dict[str, Any]) -> Dict[str, Any]:
    required = {"status", "mode", "message", "results"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"doc_search_result missing fields: {missing}")

    if data["status"] != "ok":
        raise ValueError("doc_search status must be ok")

    if data["mode"] != "search_results":
        raise ValueError("doc_search mode must be search_results")

    if not isinstance(data["results"], list):
        raise ValueError("doc_search results must be list")

    return data


def validate_kb_answer_result(data: Dict[str, Any]) -> Dict[str, Any]:
    required = {"status", "mode", "message"}
    missing = required - data.keys()
    if missing:
        raise ValueError(f"kb_answer_result missing fields: {missing}")

    if data["status"] != "ok":
        raise ValueError("kb_answer status must be ok")

    if data["mode"] != "text_answer":
        raise ValueError("kb_answer mode must be text_answer")

    return data


# =============================================================================
# MODEL
# =============================================================================

def build_common_model() -> LiteLlm:
    """Общая ADK-модель."""
    if not LLM_API_KEY:
        raise ValueError("LLM_API_KEY is not configured")

    model = LiteLlm(
        model=LLM_API_MODEL,
        api_key=LLM_API_KEY,
        api_base=LLM_API_URL or None,
        max_tokens=4000,
        temperature=0.1,
    )
    logger.info(f"LLM initialized: {LLM_API_MODEL}")
    return model


# =============================================================================
# SEARCH BACKEND
# =============================================================================

class KbSearchBackend(Protocol):
    async def search(
        self,
        *,
        query: str,
        collection: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        """
        Должен вернуть список результатов в нормализуемом виде.
        Минимально ожидаемые поля:
        - document_id
        - source_name
        - source_path (optional)
        - snippet / content (optional)
        - score (optional)
        """
        ...


class StubKbSearchBackend:
    """
    Заглушка. Подмените на ваш реальный MCP/ADK backend.
    """

    async def search(
        self,
        *,
        query: str,
        collection: str,
        top_k: int,
    ) -> List[Dict[str, Any]]:
        logger.warning(
            "StubKbSearchBackend is used. "
            "Replace it with real kb_search backend."
        )
        return []


# =============================================================================
# LEAF AGENTS (ГОТОВЫЕ КЛАССЫ ADK)
# =============================================================================

def create_owasp_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
Ты owasp_agent.
Верни только JSON без markdown и без пояснений.

Безопасный запрос:
{
  "status": "ok",
  "route": "continue",
  "reason": "safe"
}

Небезопасный запрос:
{
  "status": "blocked",
  "route": "reject",
  "reason": "prompt_injection",
  "user_message": "Запрос отклонён по соображениям безопасности."
}
"""
    return LlmAgent(
        name="owasp_agent",
        model=model,
        instruction=load_prompt("owasp_agent-prompt.md", fallback),
        output_key="owasp_result_json",
    )


def create_dispatcher_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
Ты dispatcher_agent.
Верни только JSON без markdown и без пояснений.

Формат:
{
  "status": "ok",
  "route": "doc_search",
  "intent": "doc_search",
  "reason": "user asks to find documents",
  "search_query": "нормализованный поисковый запрос"
}

Разрешённые route:
- doc_search
- kb_answer

Разрешённые intent:
- doc_search
- kb_answer
- smalltalk

Правила:
- smalltalk идёт в route=kb_answer
- используй только snake_case
"""
    return LlmAgent(
        name="dispatcher_agent",
        model=model,
        instruction=load_prompt("dispatcher_agent-prompt.md", fallback),
        output_key="dispatcher_result_json",
    )


def create_doc_search_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
Ты doc_search_agent.
Текущий запрос пользователя:
{user_query}

Контекст поиска:
{doc_search_context_json}

Верни только JSON без markdown и без пояснений.

Формат:
{
  "status": "ok",
  "mode": "search_results",
  "message": "Вот найденные документы:",
  "results": []
}

Правила:
- не выдумывай документы;
- если документы найдены, дай короткое сообщение;
- если документов нет, верни results=[];
- не отвечай общим текстом вместо JSON.
"""
    return LlmAgent(
        name="doc_search_agent",
        model=model,
        instruction=load_prompt("doc_search_agent-prompt.md", fallback),
        output_key="doc_search_result_json",
    )


def create_kb_answer_agent(model: LiteLlm) -> LlmAgent:
    fallback = """
Ты kb_answer_agent.
Текущий запрос пользователя:
{user_query}

Контекст поиска:
{kb_answer_context_json}

Верни только JSON без markdown и без пояснений.

Формат:
{
  "status": "ok",
  "mode": "text_answer",
  "message": "Краткий ответ по базе знаний"
}

Правила:
- отвечай только на основе переданного контекста;
- если данных мало, честно скажи об этом;
- не возвращай список документов как основной режим.
"""
    return LlmAgent(
        name="kb_answer_agent",
        model=model,
        instruction=load_prompt("kb_answer_agent-prompt.md", fallback),
        output_key="kb_answer_result_json",
    )


# =============================================================================
# ROOT CUSTOM AGENT (МИНИМАЛЬНЫЙ BASEAGENT ТОЛЬКО ДЛЯ ВЕТВЛЕНИЯ)
# =============================================================================

class RootAgent(BaseAgent):
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
        doc_collection: str = DOC_SEARCH_COLLECTION,
        kb_collection: str = KB_ANSWER_COLLECTION,
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

        raw_results = await self.kb_backend.search(
            query=search_query,
            collection=self.doc_collection,
            top_k=self.top_k,
        )
        logger.info("doc_search backend returned %s raw results", len(raw_results or []))

        normalized = self._normalize_search_backend_results(raw_results)
        deduped = deduplicate_results(normalized)
        logger.info("doc_search normalized=%s deduped=%s", len(normalized), len(deduped))

        ctx.session.state["user_query"] = user_message
        ctx.session.state["doc_search_context_json"] = json.dumps(
            {
                "search_query": search_query,
                "collection": self.doc_collection,
                "results": deduped,
            },
            ensure_ascii=False,
        )

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.doc_search_agent,
            output_key="doc_search_result_json",
            parsed_state_key="_doc_search_result_parsed",
            validator=validate_doc_search_result,
            log_label="doc_search_result_json",
        ):
            yield event

        doc_result = self._get_required_state_dict(ctx, "_doc_search_result_parsed")
        ctx.session.state["_root_final_text"] = format_search_results_contract(
            message=doc_result["message"],
            results=deduped,
        )

    async def _handle_kb_answer(
        self,
        ctx: InvocationContext,
        user_message: str,
        search_query: str,
    ) -> AsyncGenerator[Event, None]:
        logger.info("kb_answer route: query=%s", truncate_for_log(search_query, 300))

        raw_results = await self.kb_backend.search(
            query=search_query,
            collection=self.kb_collection,
            top_k=self.top_k,
        )
        logger.info("kb_answer backend returned %s raw results", len(raw_results or []))

        normalized = self._normalize_search_backend_results(raw_results)
        deduped = deduplicate_results(normalized)
        logger.info("kb_answer normalized=%s deduped=%s", len(normalized), len(deduped))

        ctx.session.state["user_query"] = user_message
        ctx.session.state["kb_answer_context_json"] = json.dumps(
            {
                "search_query": search_query,
                "collection": self.kb_collection,
                "results": deduped,
            },
            ensure_ascii=False,
        )

        async for event in self._run_json_leaf_agent(
            ctx=ctx,
            agent=self.kb_answer_agent,
            output_key="kb_answer_result_json",
            parsed_state_key="_kb_answer_result_parsed",
            validator=validate_kb_answer_result,
            log_label="kb_answer_result_json",
        ):
            yield event

        kb_result = self._get_required_state_dict(ctx, "_kb_answer_result_parsed")
        ctx.session.state["_root_final_text"] = format_text_answer(kb_result["message"])

    async def _run_json_leaf_agent(
        self,
        *,
        ctx: InvocationContext,
        agent: LlmAgent,
        output_key: str,
        parsed_state_key: str,
        validator: Callable[[Dict[str, Any]], Dict[str, Any]],
        log_label: str,
    ) -> AsyncGenerator[Event, None]:
        self._clear_state_keys(ctx, [output_key, parsed_state_key])

        async for event in agent.run_async(ctx):
            yield event

        raw_value = self._get_required_state_text(ctx, output_key)
        logger.info("%s raw: %s", log_label, truncate_for_log(raw_value, 2000))

        try:
            parsed = extract_json(raw_value)
        except Exception as exc:
            raise ValueError(f"{log_label} is not valid JSON: {exc}") from exc

        try:
            ctx.session.state[parsed_state_key] = validator(parsed)
        except Exception as exc:
            raise ValueError(f"{log_label} failed validation: {exc}") from exc

    @staticmethod
    def _clear_state_keys(ctx: InvocationContext, keys: List[str]) -> None:
        for key in keys:
            try:
                ctx.session.state.pop(key, None)
            except Exception:
                pass

    @staticmethod
    def _get_required_state_text(ctx: InvocationContext, key: str) -> str:
        raw_value = ctx.session.state.get(key)
        text = str(raw_value or "").strip()
        if not text:
            raise ValueError(f"Missing or empty session.state['{key}']")
        return text

    @staticmethod
    def _get_required_state_dict(ctx: InvocationContext, key: str) -> Dict[str, Any]:
        value = ctx.session.state.get(key)
        if not isinstance(value, dict):
            raise ValueError(f"session.state['{key}'] is missing or not a dict")
        return value

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
        """
        Финальное событие root-агента.
        """
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
        """
        Приведение ответа backend к единому виду.
        """
        normalized: List[Dict[str, Any]] = []

        for item in items or []:
            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

            document_id = (
                item.get("document_id")
                or metadata.get("document_id")
                or metadata.get("DOCUMENT_ID")
            )
            if not document_id:
                continue

            source_path = (
                item.get("source_path")
                or item.get("relative_path")
                or metadata.get("source_path")
                or metadata.get("relative_path")
            )

            source_name = (
                item.get("source_name")
                or item.get("source")
                or item.get("filename")
                or metadata.get("source_name")
                or metadata.get("source")
                or metadata.get("filename")
            )
            if not source_name:
                source_name = str(source_path).split("/")[-1] if source_path else f"{document_id}.file"

            snippet = (
                item.get("snippet")
                or item.get("content")
                or metadata.get("snippet")
                or ""
            )
            if isinstance(snippet, list):
                snippet = " ".join(str(x) for x in snippet)

            normalized.append(
                {
                    "document_id": str(document_id),
                    "source_name": str(source_name),
                    "source_path": str(source_path) if source_path else None,
                    "snippet": str(snippet)[:500],
                    "score": item.get("score"),
                }
            )

        return normalized


# =============================================================================
# FACTORY
# =============================================================================

def build_agent_chain(kb_backend: Optional[KbSearchBackend] = None) -> RootAgent:
    model = build_common_model()

    return RootAgent(
        owasp_agent=create_owasp_agent(model),
        dispatcher_agent=create_dispatcher_agent(model),
        doc_search_agent=create_doc_search_agent(model),
        kb_answer_agent=create_kb_answer_agent(model),
        kb_backend=kb_backend or StubKbSearchBackend(),
        doc_collection=DOC_SEARCH_COLLECTION,
        kb_collection=KB_ANSWER_COLLECTION,
        top_k=KB_TOP_K,
    )


# Глобальный экземпляр для ADK runtime / adk web
root_agent = build_agent_chain()

__all__ = ["root_agent", "RootAgent", "build_agent_chain", "KbSearchBackend"]