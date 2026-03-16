from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
# MCP (штатно через ADK)
from google.adk.tools.mcp_tool import McpToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from dotenv import load_dotenv
import os
from pathlib import Path
import sys
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

class PromptManager(FileSystemEventHandler):
    def __init__(self, prompt_path: Path, agent):
        self.prompt_path = prompt_path
        self.agent = agent
        self._load_prompt()

    def _load_prompt(self):
        """Внутренний метод для чтения файла"""
        try:
            new_content = self.prompt_path.read_text(encoding="utf-8")
            # Обновляем инструкцию в самом объекте агента
            self.agent.instruction = new_content
            # В некоторых версиях ADK нужно обновить и внутренний атрибут
            if hasattr(self.agent, '_instruction'):
                self.agent._instruction = new_content
                
            logger.info(f"🔄 Промпт успешно обновлен из файла: {self.prompt_path.name}")
        except Exception as e:
            logger.error(f"❌ Ошибка при автоматической загрузке промпта: {e}")

    def on_modified(self, event):
        # Проверяем, что изменился именно наш файл промпта
        if not event.is_directory and Path(event.src_path).resolve() == self.prompt_path.resolve():
            self._load_prompt()

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
KB_SIMILARITY_TOP_K = int(os.getenv("KB_SIMILARITY_TOP_K", "5"))

logger.info(f"Инициализация агента с моделью: {MODEL}")
logger.info(f"API URL: {API_URL}")
logger.info(f"KB Search defaults: collection={KB_DEFAULT_COLLECTION}, top_k={KB_SIMILARITY_TOP_K}")

# === Системный промпт ===
PROMPTS_DIR = Path(__file__).parent / "prompts"
PROMPT_FILE = PROMPTS_DIR / "agent_prompt.md"

def load_system_prompt():
    """Загрузить системный промпт из файла"""
    try:
        system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
        logger.info(f"Системный промпт загружен из {PROMPT_FILE}")
        return system_prompt
    except Exception as e:
        logger.error(f"Ошибка загрузки промпта: {e}")
        return "Ты полезный ассистент в Telegram."

system_prompt = load_system_prompt()

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
    model=LiteLlm(
        model=MODEL,
        api_key=API_KEY,
        api_base=API_URL,
        # Ограничение длины генерируемого ответа
        max_tokens=2000,
        # Температура для более предсказуемых ответов
        temperature=0.1,
        # Дополнительные параметры для управления контекстом
        extra_body={
            "max_context_length": 35000,  # Резерв для ответа модели
        }
    ),
    instruction=system_prompt,
    tools=tools,
)

logger.info("✓ Агент успешно инициализирован")
logger.info(f"  Подключено tools: {len(tools)}")
# 2. Запускаем слежку за файлом
event_handler = PromptManager(PROMPT_FILE, root_agent)
observer = Observer()
observer.schedule(event_handler, path=str(PROMPT_FILE.parent), recursive=False)
observer.start()