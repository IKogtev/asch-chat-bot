import json
import re
from pathlib import Path
from typing import Any, Dict, List


def load_prompt(filename: str, fallback: str) -> str:
    """
    Загружает промпт из файла или возвращает fallback.
    """
    from .config import PROMPTS_DIR
    
    prompt_path = PROMPTS_DIR / filename
    
    if prompt_path.exists():
        try:
            return prompt_path.read_text(encoding="utf-8").strip()
        except Exception as e:
            from utils.logger import setup_logger
            logger = setup_logger("agent_helpers", "agent.log")
            logger.warning(f"Failed to load prompt from {filename}: {e}")
    
    return fallback.strip()


def extract_json(text: str) -> Dict[str, Any]:
    """
    Извлекает JSON из текста, удаляя markdown блоки.
    """
    text = text.strip()
    
    # Удаляем markdown блоки
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*$", "", text)
    text = text.strip()
    
    # Ищем JSON объект
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON: {e}")
    
    raise ValueError(f"JSON object not found in model response: {text[:500]}")


def format_search_results_contract(message: str, results: List[Dict[str, Any]]) -> str:
    """
    Форматирует результаты поиска в контракт для бота.
    """
    payload = {
        "type": "search_results",
        "message": message,
        "results": results,
    }
    return f"<bot_contract>{json.dumps(payload, ensure_ascii=False)}</bot_contract>"


def format_text_answer(message: str) -> str:
    """
    Форматирует текстовый ответ.
    """
    return message.strip()


def format_reject_answer(message: str) -> str:
    """
    Форматирует ответ об отклонении запроса.
    """
    return message.strip()


def deduplicate_results(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Удаляет дубликаты по document_id, оставляя результат с лучшим score.
    """
    seen: Dict[str, Dict[str, Any]] = {}
    
    for item in items:
        doc_id = item.get("document_id")
        if not doc_id:
            continue
        
        score = item.get("score", 0.0)
        
        if doc_id not in seen or (score and score > seen[doc_id].get("score", 0.0)):
            seen[doc_id] = item
    
    return list(seen.values())


def truncate_for_log(text: str, max_length: int = 200) -> str:
    """
    Обрезает текст для логирования.
    """
    if not text:
        return ""
    
    text = str(text).strip()
    
    if len(text) <= max_length:
        return text
    
    return text[:max_length] + "..."