from typing import Any, Dict, Literal
from pydantic import BaseModel, Field

from google.adk.agents import LlmAgent
from google.genai.types import GenerateContentConfig
from google.adk.models.lite_llm import LiteLlm

from utils.logger import setup_logger
from ..config import (
    LLM_MAX_OUTPUT_TOKENS,
    LLM_PRESENCE_PENALTY,
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
    mode: Literal[
        "smalltalk",
        "context_answer",
        "product_selection",
    ] = Field(description="Режим smalltalk-ответа")
    message: str = Field(description="Текст ответа")
    selected_product_code: str = Field(
        default="",
        description="Код продукта, если smalltalk выбрал продукт из контекста",
    )
    selected_product_name: str = Field(
        default="",
        description="Название продукта, если smalltalk выбрал продукт из контекста",
    )
    selected_product_folder_kit: str = Field(
        default="",
        description="Папка/идентификатор комплекта продукта, если известно",
    )

def validate_smalltalk_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет и нормализует результат `smalltalk_agent`.

    Ожидаемый контракт:
    - `status="ok"`;
    - `mode` `text_answer`
    - `message` обязателен и не должен быть пустым;
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

    selected_product_code = str(data.get("selected_product_code", "")).strip()
    selected_product_name = str(data.get("selected_product_name", "")).strip()
    selected_product_folder_kit = str(data.get("selected_product_folder_kit", "")).strip()

    if status != "ok":
        raise build_validation_error(
            agent=agent_name,
            stage="status",
            problem="status must be ok",
        )

    if mode not in {"smalltalk", "context_answer", "product_selection"}:
        mode = "smalltalk"

    if not message:
        raise build_validation_error(
            agent=agent_name,
            stage="message",
            problem="message is empty",
        )

    return {
        "status": "ok",
        "mode": mode,
        "message": message,
        "selected_product_code": selected_product_code,
        "selected_product_name": selected_product_name,
        "selected_product_folder_kit": selected_product_folder_kit,
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
    config_params = {
        "presence_penalty": LLM_PRESENCE_PENALTY,
        "max_output_tokens": LLM_MAX_OUTPUT_TOKENS,
    }
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
