import os
from dotenv import load_dotenv
from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

load_dotenv()

MODEL = os.getenv("LLM_API_MODEL", "litellm_proxy/nst-3")
API_KEY = os.getenv("LLM_API_KEY", "")
API_URL = os.getenv("LLM_API_URL", "")

root_agent = LlmAgent(
    name="local_llm_agent",
    model=LiteLlm(
        model=MODEL,
        api_key=API_KEY,
        api_base=API_URL
    ),
    instruction=(
        "Ты полезный ассистент в Telegram. "
        "Поддерживай контекст диалога, отвечай по делу."
    ),
)