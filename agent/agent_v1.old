from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm
from dotenv import load_dotenv
import os
from pathlib import Path
import sys

# Добавляем путь к utils
sys.path.insert(0, str(Path(__file__).parent.parent))
from utils.logger import setup_logger

load_dotenv()

# Настройка логгера для агента
logger = setup_logger('agent', 'agent.log')

MODEL = os.getenv("LLM_API_MODEL", "litellm_proxy/nst-3")
API_KEY = os.getenv("LLM_API_KEY", "")
API_URL = os.getenv("LLM_API_URL", "")

logger.info(f"Инициализация агента с моделью: {MODEL}")
logger.info(f"API URL: {API_URL}")

# Загрузка системного промпта
prompt_path = Path(__file__).parent / "prompts" / "agent_prompt.md"
try:
    with open(prompt_path, 'r', encoding='utf-8') as f:
        system_prompt = f.read()
    logger.info(f"Системный промпт загружен из {prompt_path}")
except Exception as e:
    logger.error(f"Ошибка загрузки промпта: {e}")
    system_prompt = "Ты полезный ассистент в Telegram."

root_agent = LlmAgent(
    name="local_llm_agent",
    model=LiteLlm(
        model=MODEL,
        api_key=API_KEY,
        api_base=API_URL
    ),
    instruction=system_prompt,
)

logger.info("Агент успешно инициализирован")