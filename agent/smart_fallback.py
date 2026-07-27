from typing import Optional, Dict, Any, List

def generate_agent_fallback(
    user_text: str,
    error_type: str,
    agent_name: Optional[str] = None,
    context: Optional[Dict[str, Any]] = None,
) -> str:
    """
    Генерирует контекстно-зависимое fallback-сообщение.
    
    Args:
        user_text: Исходный запрос пользователя
        error_type: Тип ошибки (validation_failure, no_data, empty_response)
        agent_name: Какой агент работал
        context: Контекст (что искал, какие данные были, etc.)
    """
    context = context or {}
    
    if error_type == "validation_failure":
        return _generate_validation_fallback(user_text, agent_name, context)
    
    if error_type == "no_data":
        return _generate_no_data_fallback(user_text, agent_name, context)
    
    if error_type == "empty_response":
        return _generate_empty_response_fallback(user_text, agent_name)
    
    return _generate_generic_fallback(user_text)


def _generate_validation_fallback(
    user_text: str,
    agent_name: Optional[str],
    context: Dict[str, Any],
) -> str:
    """Генерирует подсказку при ошибке валидации."""
    
    # === DISPATCHER ===
    if agent_name == "dispatcher":
        return _dispatcher_validation_fallback(user_text, context)
    
    # === DOC_SEARCH ===
    if agent_name == "doc_search":
        return _doc_search_validation_fallback(user_text, context)
    
    # === KB_ANSWER ===
    if agent_name == "kb_answer":
        return _kb_answer_validation_fallback(user_text, context)
    
    # === PRODUCT AGENTS ===
    if agent_name in {"product_info", "product_filter"}:
        return _product_selection_validation_fallback(user_text, context)
    
    # === Общее ===
    return (
        "😕 Не могу обработать запрос — не хватает деталей.\n\n"
        "Укажи название или код продукта и что нужно: документы, параметры или комплект.\n"
        "Пример: «Покажи параметры 8837».\n"
        "Если ошибка повторится — отправь /reset и задай вопрос заново.\n"
    )

def _dispatcher_validation_fallback(user_text: str, context: Dict) -> str:
    """Диспетчер не смог определить маршрут."""
    validation_error = context.get("validation_error", "")
    search_query = context.get("search_query", "")
    
    # Если ошибка в том, что не заполнен search_query
    if "search_query" in validation_error.lower():
        return (
            "🔎 Не понимаю, что нужно найти. Попробуй один из запросов:\n\n"
            "• Найти документы: «Покажи документы по Fort Knox»\n"
            "• Параметры продукта: «Покажи карточку продукта 8914»\n"
            "• Поиск в архиве: «Найди в архиве форт нокс на 1 год»\n\n"
            "Если проблема осталась, используй команду /reset"
        )
    
    # Если ошибка в route/intent
    if "route" in validation_error.lower() or "intent" in validation_error.lower():
        return (
            "🧭 Не могу определить типа запроса.\n\n"
            "Укажи название или код продукта и цель: \n"
            "📂 найти документы,\n"
            "📋 показать параметры,\n"
            "📦 выдать комплект,\n"
            "💬 вопрос по условиям.\n"
            "Пример: «Покажи параметры 8837».\n"
            "Если проблема осталась, используй команду /reset\n\n"
        )
    
    return (
        "🤔 Не могу выбрать способ обработки запроса.\n\n"
        "Напиши одной фразой: что нужно сделать + название или код продукта.\n"
        "Пример: «Покажи параметры 8837».\n"
        "Для архива добавь «архив». \n"
        "Если проблема осталась, используй команду /reset\n\n"
    )


def _doc_search_validation_fallback(user_text: str, context: Dict) -> str:
    """Агент поиска документов не смог обработать запрос."""
    search_query = context.get("search_query", "") or user_text
    mode = context.get("mode", "")
    
    # Если агент вернул mode='no_data' — это не ошибка валидации, но всё равно
    if mode == "no_data":
        return (
            "📂 Документы не найдены. Уточни тип файла и продукт:\n\n"
            "• «Покажи презентацию по 8837»\n"
            "• «Найди памятку по Fort Knox»\n"
            "Для неактивного продукта добавь в запрос слово «архив». \n"
            "Если проблема осталась, используй команду /reset\n\n"
        )
    
    return (
        "📂 Не получилось выполнить поиск документов. \n\n"
        "Проверь название или код продукта и тип файла, затем повтори запрос.\n"
        "Пример: «Найди памятку по 8914».\n"
        "Для неактивного продукта добавь в запрос слово «архив». \n"
        "Если проблема осталась, используй команду /reset\n\n"
    )


def _kb_answer_validation_fallback(user_text: str, context: Dict) -> str:
    """Агент ответа по базе знаний не смог обработать запрос."""
    search_query = context.get("search_query", "") or user_text
    source = context.get("source", "")
    
    # Если источник 'none' — значит, ни FAQ, ни KB не дали данных
    if source == "none":
        return (
            "📚 К сожалению, мне не удалось найти ответ по базе знаний.\n\n"
            "Уточни название или код продукта и тему: условия, параметры, ограничения или документы.\n"
            "Пример: «Какие условия у Fort Knox?»\n"
            "Для неактивного продукта добавь в запрос слово «архив».\n"
            "Если проблема осталась, используй команду /reset\n\n"
        )
    
    return (
        "📚 Не удалось сформировать ответ по базе знаний.\n\n"
        "Попробуй задать вопрос иначе и укажи название или код продукта.\n"
        "Пример: «Какие параметры у 8837?»\n"
        "Если ошибка повторится используй команду /reset\n"
    )


def _product_selection_validation_fallback(user_text: str, context: Dict) -> str:
    """Агент выбора продукта не смог обработать запрос."""
    validation_error = context.get("validation_error", "")
    search_query = context.get("search_query", "") or user_text
    mode = context.get("mode", "")
    resolved_product = context.get("resolved_product")
    clarification_options = context.get("clarification_options") or []
    used_tables = context.get("used_tables") or []
    
    # Если ошибка в tool_usage — агент не вызвал execute_sql
    if "tool_usage" in validation_error:
        if mode == "product_compare":
            return (
                "⚖️ Не могу подтвердить данные для сравнения.\n\n"
                "Укажи два точных названия или кода и критерии.\n\n"
                "Пример: «Сравни 8837 и 8914 по сроку и условиям».\n\n"
                "Если не сработает — /reset"
            )

        return (
            "Ой! 🔎 Не могу найти нужный продукт. Попробуй так:\n\n"
            "• активный — укажи точное название или код: «Fort Knox» / «8837»;\n"
            "• архивный — «Найди параметры архивного 8837»;\n"
            "• название или код неизвестны — «Покажи активные продукты» и выбери нужный.\n\n"
            "Если ошибка повторится, используй команду /reset"
        )
    
    # Если ошибка в resolved_product — продукт не найден
    if "resolved_product" in validation_error:
        return _product_not_found_fallback(search_query, used_tables)
    
    # Если ошибка в clarification_options
    if "clarification_options" in validation_error:
        return (
            "🔎 Нашла несколько похожих продуктов, но не могу показать варианты.\n\n"
            "Укажи точное название или код.\n"
            "Не знаешь их — отправь «Покажи активные продукты» и выбери нужный.  \n"
            "Для поиска по архиву добавь слово «архив».\n\n"
            "Если ошибка повторится, используй команду /reset"
        )
    
    # Общий случай для product_selection
    return _product_not_found_fallback(search_query, used_tables)


def _product_not_found_fallback(search_query: str, used_tables: List[str]) -> str:
    """Универсальный fallback, когда продукт не найден."""
    tables_hint = ""
    if used_tables:
        tables_hint = f"{' '.join(used_tables[:2])})"
    
    return (
        f"🔎 Не могу найти продукт по запросу{tables_hint}.\n\n"
        "Уточни цель:\n"
        "• документы — «Найди документы по 8837»;\n"
        "• карточка или параметры — «Покажи параметры 8837»;\n"
        "• комплект — «Дай комплект по 8837».\n"
        "Для неактивного продукта добавь «архив». Не знаешь код — «Покажи активные продукты».n\n"
        "Если поиск снова не сработает, используй команду /reset"
    )


def _generate_no_data_fallback(
    user_text: str,
    agent_name: Optional[str],
    context: Dict[str, Any],
) -> str:
    """Fallback, когда данные не найдены (mode='no_data')."""
    search_query = context.get("search_query", "") or user_text
    
    if agent_name == "doc_search":
        return (
            "📂 По запросу ничего не нашлось. \n\n"
            "Попробуй задать вопрос иначе: тип документа + название или код продукта\n"
            "Пример: «Покажи памятку по 8837».\n"
            "Для архива добавь «архив».\n"
            "Если поиск зависает — /reset\n\n"
        )
    
    if agent_name == "kb_answer":
        return (
            "📚 В базе знаний нет информации по этому вопросу.\n\n"
            "Укажи название или код продукта и что нужно: условия, параметры, ограничения или документы.\n"
            "Пример: «Какие условия у 8837?»\n"
            "Для поиска по архиву добавь слово «архив».\n\n"
            "Если ошибка повторится, используй команду /reset"
        )
    
    if agent_name in {"product_info", "product_filter"}:
        return _product_not_found_fallback(search_query, context.get("used_tables") or [])
    
    return (
        f"❓ Не нашла данные по запросу «{_truncate(search_query, 100)}»\n\n"
        f"Добавь название или код продукта и цель: документы, параметры, комплект или сравнение."
        "Для архива добавь «архив». Если после уточнения ответа нет — /reset"
    )


def _generate_empty_response_fallback(user_text: str, agent_name: Optional[str]) -> str:
    """Fallback при пустом ответе."""
    return (
        "😔 Извини, я не смогла сформировать ответ. \n\n"
        "Пожалуйста, повтори запрос одной короткой фразой и укажи название или код продукта.\n"
        "Если снова получишь пустой ответ — отправь /reset и задай вопрос еще раз.\n"
    )


def _generate_generic_fallback(user_text: str) -> str:
    """Общий fallback."""
    return (
        "⚠️ Не получилось обработать запрос. \n\n"
        "Попробуй задать вопрос иначе и укажи название или код продукта.\n"
        "Если ошибка повторится — отправь /reset и начни новый диалог.\n"
    )


def _truncate(text: str, max_length: int) -> str:
    """Обрезает текст до указанной длины."""
    text = str(text or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rsplit(" ", 1)[0] + "..."
