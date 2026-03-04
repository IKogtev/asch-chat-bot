from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

# MCP (штатно через ADK)
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams

from dotenv import load_dotenv
import os
from pathlib import Path
import sys

# Добавляем путь к utils
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import setup_logger

load_dotenv(override=True)

logger = setup_logger("agent", "agent.log")

# === LLM Configuration ===
MODEL = os.getenv("LLM_API_MODEL", "litellm_proxy/nst-3")
API_KEY = os.getenv("LLM_API_KEY", "")
API_URL = os.getenv("LLM_API_URL", "")

# === MCP KB Search Configuration ===
KBSEARCH_MCP_URL = os.getenv("KBSEARCH_MCP_URL", "").strip()
MCP_TOKEN = os.getenv("MCP_TOKEN", "").strip()
MCP_TIMEOUT_SEC = float(os.getenv("MCP_TIMEOUT_SEC", "15"))

# === KB Search Defaults ===
KB_DEFAULT_COLLECTION = os.getenv("KB_DEFAULT_COLLECTION", "kb_collection")
KB_SIMILARITY_TOP_K = int(os.getenv("KB_SIMILARITY_TOP_K", "10"))

logger.info(f"Инициализация агента с моделью: {MODEL}")
logger.info(f"API URL: {API_URL}")
logger.info(f"KB Search defaults: collection={KB_DEFAULT_COLLECTION}, top_k={KB_SIMILARITY_TOP_K}")

# === Системный промпт ===
prompt_path = Path(__file__).parent / "prompts" / "agent_prompt.md"
try:
    system_prompt = prompt_path.read_text(encoding="utf-8")
    logger.info(f"Системный промпт загружен из {prompt_path}")
except Exception as e:
    logger.error(f"Ошибка загрузки промпта: {e}")
    system_prompt = "Ты полезный ассистент в Telegram."

# === Инициализация Tools ===
tools = []

# Подключаем MCP kbsearch
if KBSEARCH_MCP_URL:
    try:
        headers = {"Authorization": f"Bearer {MCP_TOKEN}"} if MCP_TOKEN else None

        kbsearch_toolset = McpToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=KBSEARCH_MCP_URL,
                headers=headers,
                timeout=MCP_TIMEOUT_SEC,
            ),
            # Фильтруем только нужные tools
            tool_filter=["kb_search", "get_kb_info"],
        )
        tools.append(kbsearch_toolset)
        logger.info(f"✓ MCP kbsearch подключен: {KBSEARCH_MCP_URL}")
        logger.info(f"  Timeout: {MCP_TIMEOUT_SEC}s, Token: {'***' if MCP_TOKEN else 'не задан'}")
    except Exception as e:
        logger.error(f"✗ Ошибка подключения MCP kbsearch: {e}", exc_info=True)
else:
    logger.warning("⚠ KBSEARCH_MCP_URL не задан — MCP kbsearch не подключён")

if not tools:
    logger.warning("⚠ Агент создается без tools — функциональность ограничена")

# === Создание агента ===
root_agent = LlmAgent(
    name="local_llm_agent",
    model=LiteLlm(model=MODEL, api_key=API_KEY, api_base=API_URL),
    instruction=system_prompt,
    tools=tools,
)

logger.info("✓ Агент успешно инициализирован")
logger.info(f"  Подключено tools: {len(tools)}")