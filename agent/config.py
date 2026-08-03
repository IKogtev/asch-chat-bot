from pathlib import Path
import os
import warnings
from dotenv import load_dotenv
from utils.logger import setup_logger

load_dotenv(override=True)

# LiteLLM / Pydantic в некоторых версиях создают безвредный UserWarning при
# сериализации ответа провайдера. На поведение агента это не влияет, но
# засоряет логи на каждом вызове модели.
warnings.filterwarnings(
    "ignore",
    message=r"^Pydantic serializer warnings:",
    category=UserWarning,
)

# =============================================================================
# PATHS
# =============================================================================
SCRIPT_DIR = Path(__file__).parent.absolute()
PROMPTS_DIR = Path(os.getenv("AGENT_PROMPTS_DIR", str(SCRIPT_DIR / "prompts")))

# =============================================================================
# LLM SETTINGS
# =============================================================================
LLM_API_KEY = os.getenv("LLM_API_KEY", "").strip()
LLM_API_URL = os.getenv("LLM_API_URL", "").strip()
LLM_API_MODEL = os.getenv("LLM_API_MODEL", "litellm_proxy/nst-3").strip()
LITELLM_REQUEST_TIMEOUT = float(os.getenv("LITELLM_REQUEST_TIMEOUT", "90"))
LITELLM_NUM_RETRIES = int(os.getenv("LITELLM_NUM_RETRIES", "1"))
LLM_MAX_OUTPUT_TOKENS = int(os.getenv("LLM_MAX_OUTPUT_TOKENS", 4096))

# =============================================================================
# AGENT TEMPERATURES
# =============================================================================
ROOT_TEMPERATURE = float(os.getenv("ROOT_TEMPERATURE", 0.0))
OWASP_TEMPERATURE = float(os.getenv("OWASP_TEMPERATURE", 0.6))
OWASP_TOP_P = float(os.getenv("OWASP_TOP_P", 0.95))
OWASP_TOP_K = int(os.getenv("OWASP_TOP_K", 20))
OWASP_MAX_OUTPUT_TOKENS = int(os.getenv("OWASP_MAX_OUTPUT_TOKENS", 2304))
OWASP_THINKING_TOKEN_BUDGET = int(os.getenv("OWASP_THINKING_TOKEN_BUDGET", 2048))
OWASP_ENABLE_THINKING = os.getenv("OWASP_ENABLE_THINKING", "true").strip().lower() == "true"
DISPATCHER_TEMPERATURE = float(os.getenv("DISPATCHER_TEMPERATURE", ROOT_TEMPERATURE))
DOC_SEARCH_TEMPERATURE = float(os.getenv("DOC_SEARCH_TEMPERATURE", ROOT_TEMPERATURE))
KB_ANSWER_TEMPERATURE = float(os.getenv("KB_ANSWER_TEMPERATURE", ROOT_TEMPERATURE))
SMALLTALK_TEMPERATURE = float(os.getenv("SMALLTALK_TEMPERATURE", ROOT_TEMPERATURE))
PRODUCT_INFO_TEMPERATURE = float(os.getenv("PRODUCT_INFO_TEMPERATURE", ROOT_TEMPERATURE))
PRODUCT_FILTER_TEMPERATURE = float(os.getenv("PRODUCT_FILTER_TEMPERATURE", ROOT_TEMPERATURE))

# =============================================================================
# COLLECTIONS
# =============================================================================
ACTIVE_DOCUMENTS_COLLECTION = os.getenv("ACTIVE_DOCUMENTS_COLLECTION", "kb_collection").strip()
KB_DOCUMENTS_COLLECTION = os.getenv("KB_DOCUMENTS_COLLECTION", "knowledge_base_collection").strip()
FAQ_DOCUMENTS_COLLECTION = os.getenv("FAQ_DOCUMENTS_COLLECTION", "faq_collection").strip()
KB_TOP_K = int(os.getenv("KB_TOP_K", "20"))
AGENT_DIALOG_MEMORY_MAX_TURNS = int(os.getenv("AGENT_DIALOG_MEMORY_MAX_TURNS", "3"))
# Сколько документов показывать в первом ответе и шаг «ещё»
DOC_SEARCH_PAGE_SIZE = int(os.getenv("SHOW_LIST_SIZE", os.getenv("DOC_SEARCH_PAGE_SIZE", "5")))

# =============================================================================
# KB_SEARCH MCP SETTINGS
# =============================================================================
KBSEARCH_MCP_URL = os.getenv("KBSEARCH_MCP_URL", "http://kbsearch:7001/kbsearch/mcp").strip()
MCP_TOKEN = os.getenv("MCP_TOKEN", "").strip()
MCP_TIMEOUT_SEC = float(os.getenv("MCP_TIMEOUT_SEC", "30"))

# =============================================================================
# FAQ_SEARCH MCP SETTINGS
# =============================================================================
FAQSEARCH_MCP_URL = os.getenv("FAQSEARCH_MCP_URL", "http://faq:7000/faq_rag/mcp").strip()
FAQSEARCH_MCP_TOKEN = os.getenv("FAQSEARCH_MCP_TOKEN", MCP_TOKEN).strip()
FAQSEARCH_MCP_TIMEOUT_SEC = float(
    os.getenv("FAQSEARCH_MCP_TIMEOUT_SEC", str(MCP_TIMEOUT_SEC))
)

# =============================================================================
# DBHUB MCP SETTINGS
# =============================================================================
DBHUB_MCP_URL = os.getenv("DBHUB_MCP_URL", "http://dbhub:8080/mcp").strip()
DBHUB_MCP_TOKEN = os.getenv("DBHUB_MCP_TOKEN", MCP_TOKEN).strip()
DBHUB_MCP_TIMEOUT_SEC = float(
    os.getenv("DBHUB_MCP_TIMEOUT_SEC", str(MCP_TIMEOUT_SEC))
)
# =============================================================================
# DATABASE
# =============================================================================
DATABASE_URL = os.getenv("DATABASE_URL")
# =============================================================================
# FALLBACK FRAZES FOR TOOLS 
# =============================================================================
COMPARE_FRAZE = str(os.getenv("COMPARE_FRAZE", ""))
PRODUCT_CARD_KIT_OFFER = str(os.getenv("PRODUCT_CARD_KIT_OFFER", "📂 Могу также прислать комплект документов по этому продукту. Напишите «комплект», если нужно."))

# =============================================================================
# LOGGING
# =============================================================================
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DEBUG_EXCEPTIONS = os.getenv("DEBUG_EXCEPTIONS", "false").lower() == "true"

logger = setup_logger("agent_chain", "agent.log")

# =============================================================================
# MODEL FACTORY
# =============================================================================
from google.adk.models.lite_llm import LiteLlm
from typing import Any, Dict, List, Optional, Protocol

def _build_model(*, enable_thinking: bool, thinking_token_budget: int = 2048) -> LiteLlm:
    extra_body: Dict[str, Any] = {
        "chat_template_kwargs": {"enable_thinking": enable_thinking},
    }
    if enable_thinking:
        extra_body["thinking_token_budget"] = thinking_token_budget

    return LiteLlm(
        model=LLM_API_MODEL,
        api_key=LLM_API_KEY,
        api_base=LLM_API_URL,
        timeout=LITELLM_REQUEST_TIMEOUT,
        num_retries=LITELLM_NUM_RETRIES,
        extra_body=extra_body,
    )


def build_common_model() -> LiteLlm:
    """
    Создает общую модель LiteLlm для агентов, которым нужен thinking mode.
    """
    return _build_model(enable_thinking=True)


def build_format_model() -> LiteLlm:
    """
    Создает модель без thinking mode для детерминированного форматирования.
    """
    return _build_model(enable_thinking=False)


def build_owasp_model() -> LiteLlm:
    """
    Создает отдельную LiteLlm с настройкой thinking mode для OWASP-классификатора.
    """
    return _build_model(
        enable_thinking=OWASP_ENABLE_THINKING,
        thinking_token_budget=OWASP_THINKING_TOKEN_BUDGET,
    )

# =============================================================================
# KB BACKEND PROTOCOL & STUB
# =============================================================================

class KbSearchBackend(Protocol):
    """
    Протокол для бэкенда поиска в базе знаний.
    """
    async def search(
        self,
        query: str,
        collection: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Выполняет поиск в базе знаний.
        
        Args:
            query: Поисковый запрос
            collection: Название коллекции
            top_k: Количество результатов
            
        Returns:
            Список найденных документов
        """
        ...


class StubKbSearchBackend:
    """
    Заглушка для бэкенда поиска (для тестирования).
    """
    async def search(
        self,
        query: str,
        collection: str,
        top_k: int = 5
    ) -> List[Dict[str, Any]]:
        """
        Возвращает пустой список результатов.
        """
        logger.warning(
            f"StubKbSearchBackend.search called: query={query[:50]}, "
            f"collection={collection}, top_k={top_k}"
        )
        return []
