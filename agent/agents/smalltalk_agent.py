from typing import Any, Dict, Literal
from pydantic import BaseModel, Field

from google.adk.agents import LlmAgent
from google.genai.types import GenerateContentConfig
from google.adk.models.lite_llm import LiteLlm

from utils.logger import setup_logger
from ..config import (
    SMALLTALK_TEMPERATURE,
)
from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from .validation_utils import build_validation_error

logger = setup_logger("smalltalk_agent", "agent.log")

ASSISTANT_CAPABILITIES_ANSWER = "Я умею искать документы и помогать продавать продукты АСЖ."

# Объявляем схему как Pydantic-класс
class SmalltalkResponseSchema(BaseModel):
    status: Literal["ok"] = Field(description="Всегда 'ok'")
    mode: Literal["text_answer"] = Field(description="Всегда text_answer")
    message: str = Field(description="Ответ пользователю")
    source: Literal["none"] = Field(description="Источник всегда none")


def validate_smalltalk_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет и нормализует результат `smalltalk_agent`.

    Ожидаемый контракт:
    - `status="ok"`;
    - `mode` `text_answer`
    - `message` обязателен и не должен быть пустым;
    - `source` `none`.
    При нарушении контракта выбрасывает `ValueError` с диагностическим описанием,
    пригодным для логирования и локализации сбоя на этапе отладки.
    """
    agent_name = "smalltalk_agent"
    if not isinstance(data, dict):
        raise build_validation_error(
            agent=agent_name,
            stage="payload_type",
            problem="expected dict",
        )

    status = str(data.get("status", "")).strip()
    mode = str(data.get("mode", "")).strip()
    message = str(data.get("message", "")).strip()
    source = str(data.get("source", "")).strip()

    if status != "ok":
        raise build_validation_error(
            agent=agent_name,
            stage="status",
            problem="status must be ok",
        )

    if mode != "text_answer":
        raise build_validation_error(
            agent=agent_name,
            stage="mode",
            problem="mode must be text_answer",
        )

    if not message:
        raise build_validation_error(
            agent=agent_name,
            stage="message",
            problem="message is empty",
        )

    if source != "none":
        raise build_validation_error(
            agent=agent_name,
            stage="source",
            problem="source must be none",
        )

    return {
        "status": "ok",
        "mode": "text_answer",
        "message": message,
        "source": "none",
    }


def create_smalltalk_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для простых разговоров, у него нет инструментов
    он общается в формате smalltalk
    """
    fallback = f"""
Use state variable {{from_glossary}} as a dictionary of terms already found by code.
Do not search for or invent additional expansions.
For {{search_query}}, product and abbreviation substitutions are already applied in code.
Use {{from_glossary}} by category:
- product and abbreviation: do not rewrite {{search_query}} again;
- term: use definition from {{from_glossary}} when interpreting {{user_query}} and answering.
If multiple definitions are present and context does not disambiguate them, do not guess.

Ты - smalltalk_agent.

Тебе доступны переменные:
- {{user_query}} - исходный вопрос пользователя
- {{search_query}} - нормализованный поисковый запрос

Ты отвечаешь только за обычное человеческое общение.

Запрещено:

- искать документы
- искать FAQ
- искать KB
- отвечать по продуктам
- придумывать факты
- использовать инструменты

Ты умеешь:

- приветствовать пользователя
- прощаться
- благодарить
- отвечать на вопросы о себе
- отвечать на вопрос "что ты умеешь"
- отвечать на вопрос "как меня зовут"
- поддерживать короткий диалог

Если имя пользователя известно
{{first_name}},
можешь использовать его.

Отвечай всегда:

- тепло
- кратко
- естественно

Верни только JSON.

{{
  "status":"ok",
  "mode":"text_answer",
  "message":"...",
  "source":"none"
}}
"""
    prompt_file = "smalltalk_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    name = "smalltalk_agent"
    # Конфигурация генерации с принудительным JSON Output и схемой данных
    config_params = {}
    if SMALLTALK_TEMPERATURE != -1:
        logger.debug(f"Agent {name} it's temperature: {SMALLTALK_TEMPERATURE}")
        config_params["temperature"] = SMALLTALK_TEMPERATURE
    else:
        logger.debug(f"Agent {name} temperature set to -1 so google adk decide himself")

    agent = LlmAgent(
        name=name,
        model=model,
        instruction=instruction,
        output_key="smalltalk_result_json",
        output_schema=SmalltalkResponseSchema,
        generate_content_config=GenerateContentConfig(**config_params) if config_params else None
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
