import json
import re
from typing import Any, Dict, List


def load_prompt(filename: str, fallback: str) -> str:
    """
    Загружает промпт из файла из подпапки агента или возвращает fallback.
    kb_answer_agent_prompt.md -> kb_answer/kb_answer_agent_prompt.md
    """
    from .config import PROMPTS_DIR
    from utils.logger import setup_logger
            
    logger = setup_logger("agent_helpers", "agent.log")
    try:
        # извлекаем имя агента
        agent_name = filename.split("_agent")[0]
        # формируем путь
        prompt_path = PROMPTS_DIR / agent_name/ filename
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8").strip()
    except Exception as e:
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


def format_bot_contract_search_results(results: List[Dict[str, Any]]) -> str:
    """
    Контракт для сохранения списка в БД бота (mode=search_results).
    """
    payload = {"mode": "search_results", "results": results}
    return f"<bot_contract>{json.dumps(payload, ensure_ascii=False)}</bot_contract>"


# Заглушка для _root_final_text при успешном doc_search: список пользователю не из этого текста,
# а из БД через UI бота (render_results). Может попасть в историю/не-Telegram клиенты без отдельного рендера.
DOC_SEARCH_SUCCESS_HINT = "Найдены документы по запросу."


def format_bot_search_meta(payload: Dict[str, Any]) -> str:
    """Служебная разметка для обновления shown_count в БД бота."""
    return f"<bot_search_meta>{json.dumps(payload, ensure_ascii=False)}</bot_search_meta>"


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


ACK_SKIP_INTENTS = frozenset(
    {"smalltalk", "show_more", "show_all", "file_download", "needs_clarification"}
)

ACK_TEMPLATES: dict[tuple[str, str], str] = {
    ("doc_search", "doc_search"): "Подберу документы по запросу.",
    ("doc_search", "file_download"): "Подготовлю файлы из списка.",
    ("doc_search", "show_more"): "",
    ("doc_search", "show_all"): "",
    ("kb_answer", "kb_answer"): "Проверю информацию в базе знаний.",
    ("kb_answer", "needs_clarification"): "",
    ("kb_answer", "smalltalk"): "",
    ("product_selection", "product_card"): "Уточню параметры продукта.",
    ("product_selection", "product_kit"): "Подготовлю комплект документов.",
    ("product_selection", "product_filter"): "Уточню параметры продукта.",
    ("product_selection", "product_compare"): "Сравню продукты.",
}


def format_ack_message(route: str, intent: str) -> str | None:
    """Route-aware ack text; None if ack should not be sent."""
    key = (str(route or "").strip(), str(intent or "").strip())
    if key[1] in ACK_SKIP_INTENTS:
        return None
    text = ACK_TEMPLATES.get(key)
    if text is None:
        return "Обрабатываю запрос."
    return text or None


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