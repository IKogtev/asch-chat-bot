import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Protocol

from dotenv import load_dotenv
from google import genai
from google.genai import types

from utils.logger import setup_logger

load_dotenv()

# =============================================================================
# CONFIG
# =============================================================================

SCRIPT_DIR = Path(__file__).parent.absolute()
PROMPTS_DIR = Path(os.getenv("AGENT_PROMPTS_DIR", str(SCRIPT_DIR / "prompts")))

LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_API_URL = os.getenv("LLM_API_URL", "").strip()
LLM_API_MODEL = os.getenv("LLM_API_MODEL", "gemini-2.0-flash-exp").strip()

DOC_SEARCH_COLLECTION = os.getenv("DOC_SEARCH_COLLECTION", "documents").strip()
KB_ANSWER_COLLECTION = os.getenv("KB_ANSWER_COLLECTION", "knowledge_base").strip()
KB_TOP_K = int(os.getenv("KB_TOP_K", "5"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logger = setup_logger("agent_chain", "agent.log")

# =============================================================================
# HELPERS
# =============================================================================

def load_prompt(filename: str, fallback: str) -> str:
    """Загрузить prompt из файла, иначе использовать fallback."""
    path = PROMPTS_DIR / filename
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    logger.warning(f"Prompt not found: {path}")
    return fallback.strip()


def extract_json(text: str) -> Dict[str, Any]:
    """
    Аккуратно извлекает JSON-объект из ответа модели.
    Ожидаем, что агент возвращает только JSON.
    """
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
    Финальный контракт для текущего bot_v6:
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
    return message.strip()


def format_reject_answer(message: str) -> str:
    """Обычный текстовый reject-ответ."""
    return message.strip()


def deduplicate_results(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Дедупликация по document_id.
    Берем первый лучший результат на документ.
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

    for item in data["results"]:
        if not isinstance(item, dict):
            raise ValueError("doc_search result item must be dict")
        for key in ("document_id", "source_name"):
            if not item.get(key):
                raise ValueError(f"doc_search result item missing {key}")

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

def build_common_model() -> genai.Client:
    """Общая LLM-модель."""
    if not LLM_API_KEY:
        raise ValueError("LLM_API_KEY is not configured")

    http_options: Dict[str, Any] = {"api_version": "v1alpha"}
    if LLM_API_URL:
        http_options["base_url"] = LLM_API_URL

    client = genai.Client(api_key=LLM_API_KEY, http_options=http_options)
    logger.info(f"LLM initialized: {LLM_API_MODEL}")
    return client


# =============================================================================
# SEARCH BACKEND PROTOCOL
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
    Заглушка. Подмените на ваш реальный MCP/ADK transport.
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
            "Replace it with real MCP/ADK kb_search backend."
        )
        return []


# =============================================================================
# BASE AGENT
# =============================================================================

@dataclass
class JsonAgent:
    name: str
    client: genai.Client
    model_name: str
    system_prompt: str
    temperature: float = 0.1
    max_output_tokens: int = 2048

    async def run_json(self, user_message: str, context: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        text = await self._generate(user_message=user_message, context=context)
        logger.debug(f"[{self.name}] raw response: {text[:1000]}")
        return extract_json(text)

    async def run_text(self, user_message: str, context: Optional[Dict[str, Any]] = None) -> str:
        return await self._generate(user_message=user_message, context=context)

    async def _generate(self, user_message: str, context: Optional[Dict[str, Any]] = None) -> str:
        parts: List[types.Part] = []

        if context:
            parts.append(types.Part(text=f"CONTEXT:\n{json.dumps(context, ensure_ascii=False, indent=2)}"))

        parts.append(types.Part(text=f"USER_MESSAGE:\n{user_message}"))

        config = types.GenerateContentConfig(
            system_instruction=self.system_prompt,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            top_p=0.95,
        )

        response = self.client.models.generate_content(
            model=self.model_name,
            contents=[types.Content(role="user", parts=parts)],
            config=config,
        )

        if not response.candidates:
            raise RuntimeError(f"[{self.name}] empty model response")

        candidate = response.candidates[0]
        if not candidate.content or not candidate.content.parts:
            raise RuntimeError(f"[{self.name}] empty candidate content")

        out: List[str] = []
        for part in candidate.content.parts:
            if getattr(part, "text", None):
                out.append(part.text)

        final = "\n".join(x.strip() for x in out if x and x.strip()).strip()
        if not final:
            raise RuntimeError(f"[{self.name}] text not found in candidate parts")

        return final


# =============================================================================
# AGENT FACTORIES
# =============================================================================

def create_owasp_agent(client: genai.Client) -> JsonAgent:
    fallback = """
Ты owasp_agent.
Верни только JSON без markdown и без пояснений.

Допустимы только 2 ответа.

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
    return JsonAgent(
        name="owasp_agent",
        client=client,
        model_name=LLM_API_MODEL,
        system_prompt=load_prompt("owasp_agent-prompt.md", fallback),
    )


def create_dispatcher_agent(client: genai.Client) -> JsonAgent:
    fallback = """
Ты dispatcher_agent.
Верни только JSON без markdown и без пояснений.

Классифицируй запрос пользователя.
Разрешённые route:
- "doc_search"
- "kb_answer"

Разрешённые intent:
- "doc_search"
- "kb_answer"
- "smalltalk"

Формат:
{
  "status": "ok",
  "route": "doc_search",
  "intent": "doc_search",
  "reason": "user asks to find documents",
  "search_query": "нормализованный поисковый запрос"
}

Правила:
- smalltalk идёт в route="kb_answer"
- не используй GENERAL
- не используй DOCUMENT_SEARCH
- используй snake_case
"""
    return JsonAgent(
        name="dispatcher_agent",
        client=client,
        model_name=LLM_API_MODEL,
        system_prompt=load_prompt("dispatcher_agent-prompt.md", fallback),
    )


def create_doc_search_agent(client: genai.Client) -> JsonAgent:
    fallback = """
Ты doc_search_agent.
Тебе передадут пользовательский запрос и результаты поиска.
Верни только JSON без markdown и без пояснений.

Формат:
{
  "status": "ok",
  "mode": "search_results",
  "message": "Вот найденные документы:",
  "results": [
    {
      "document_id": "doc_123",
      "source_name": "Имя файла.pdf",
      "source_path": "path/to/file.pdf",
      "snippet": "краткий фрагмент",
      "rank": 1
    }
  ]
}

Правила:
- не отвечай общим текстом вместо списка;
- возвращай только документы;
- не выдумывай поля;
- если ничего нет, верни results=[]
"""
    return JsonAgent(
        name="doc_search_agent",
        client=client,
        model_name=LLM_API_MODEL,
        system_prompt=load_prompt("doc_search_agent-prompt.md", fallback),
    )


def create_kb_answer_agent(client: genai.Client) -> JsonAgent:
    fallback = """
Ты kb_answer_agent.
Тебе передадут пользовательский запрос и результаты поиска по базе знаний.
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
    return JsonAgent(
        name="kb_answer_agent",
        client=client,
        model_name=LLM_API_MODEL,
        system_prompt=load_prompt("kb_answer_agent-prompt.md", fallback),
    )


# =============================================================================
# ROOT ORCHESTRATOR
# =============================================================================

class RootAgent:
    """
    Оркестратор цепочки:
    owasp_agent -> dispatcher_agent -> (doc_search_agent | kb_answer_agent)
    """

    def __init__(
        self,
        *,
        owasp_agent: JsonAgent,
        dispatcher_agent: JsonAgent,
        doc_search_agent: JsonAgent,
        kb_answer_agent: JsonAgent,
        kb_backend: KbSearchBackend,
        doc_collection: str = DOC_SEARCH_COLLECTION,
        kb_collection: str = KB_ANSWER_COLLECTION,
        top_k: int = KB_TOP_K,
    ):
        self.owasp_agent = owasp_agent
        self.dispatcher_agent = dispatcher_agent
        self.doc_search_agent = doc_search_agent
        self.kb_answer_agent = kb_answer_agent
        self.kb_backend = kb_backend
        self.doc_collection = doc_collection
        self.kb_collection = kb_collection
        self.top_k = top_k

    async def process(self, user_message: str) -> str:
        """
        Возвращает:
        - обычный текст для text_answer/reject
        - <bot_contract>...</bot_contract> для search_results
        """
        logger.info(f"Processing message: {user_message[:200]}")

        try:
            # 1. OWASP
            owasp_raw = await self.owasp_agent.run_json(user_message)
            owasp = validate_owasp_result(owasp_raw)
            logger.info(f"OWASP result: status={owasp['status']} route={owasp['route']}")

            if owasp["status"] == "blocked":
                return format_reject_answer(owasp["user_message"])

            # 2. Dispatcher
            dispatcher_raw = await self.dispatcher_agent.run_json(user_message)
            dispatch = validate_dispatcher_result(dispatcher_raw)
            logger.info(
                f"Dispatcher result: route={dispatch['route']} "
                f"intent={dispatch['intent']} search_query={dispatch['search_query']}"
            )

            # 3a. Поиск документов
            if dispatch["route"] == "doc_search":
                return await self._handle_doc_search(
                    user_message=user_message,
                    search_query=dispatch["search_query"],
                )

            # 3b. Ответ по базе знаний
            return await self._handle_kb_answer(
                user_message=user_message,
                search_query=dispatch["search_query"],
            )

        except Exception as exc:
            logger.error(f"RootAgent failure: {exc}", exc_info=True)
            return format_text_answer("Произошла ошибка при обработке запроса. Попробуйте позже.")

    async def _handle_doc_search(self, *, user_message: str, search_query: str) -> str:
        logger.info(f"doc_search route: query={search_query}")

        raw_results = await self.kb_backend.search(
            query=search_query,
            collection=self.doc_collection,
            top_k=self.top_k,
        )

        normalized = self._normalize_search_backend_results(raw_results)
        deduped = deduplicate_results(normalized)

        context = {
            "search_query": search_query,
            "collection": self.doc_collection,
            "results": deduped,
        }
        doc_raw = await self.doc_search_agent.run_json(user_message=user_message, context=context)
        doc_result = validate_doc_search_result(doc_raw)

        # Совместимость с текущим bot_v6.py
        return format_search_results_contract(
            message=doc_result["message"],
            results=doc_result["results"],
        )

    async def _handle_kb_answer(self, *, user_message: str, search_query: str) -> str:
        logger.info(f"kb_answer route: query={search_query}")

        raw_results = await self.kb_backend.search(
            query=search_query,
            collection=self.kb_collection,
            top_k=self.top_k,
        )

        normalized = self._normalize_search_backend_results(raw_results)
        deduped = deduplicate_results(normalized)

        context = {
            "search_query": search_query,
            "collection": self.kb_collection,
            "results": deduped,
        }
        kb_raw = await self.kb_answer_agent.run_json(user_message=user_message, context=context)
        kb_result = validate_kb_answer_result(kb_raw)

        return format_text_answer(kb_result["message"])

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
    client = build_common_model()

    return RootAgent(
        owasp_agent=create_owasp_agent(client),
        dispatcher_agent=create_dispatcher_agent(client),
        doc_search_agent=create_doc_search_agent(client),
        kb_answer_agent=create_kb_answer_agent(client),
        kb_backend=kb_backend or StubKbSearchBackend(),
        doc_collection=DOC_SEARCH_COLLECTION,
        kb_collection=KB_ANSWER_COLLECTION,
        top_k=KB_TOP_K,
    )


# Глобальный экземпляр
root_agent = build_agent_chain()

__all__ = ["root_agent", "RootAgent", "build_agent_chain", "KbSearchBackend"]