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
    
    # === PRODUCT_SELECTION ===
    if agent_name == "product_selection":
        return _product_selection_validation_fallback(user_text, context)
    
    # === Общее ===
    return (
        "😔 Извините, я не совсем поняла ваш запрос.\n\n"
        "Попробуйте:\n"
        "• Переформулировать вопрос конкретнее\n"
        "• Указать название продукта или тему\n"
        "• Использовать /reset, если проблема повторяется\n\n"
        f"Ваш запрос: «{_truncate(user_text, 100)}»"
    )

def _dispatcher_validation_fallback(user_text: str, context: Dict) -> str:
    """Диспетчер не смог определить маршрут."""
    validation_error = context.get("validation_error", "")
    search_query = context.get("search_query", "")
    
    # Если ошибка в том, что не заполнен search_query
    if "search_query" in validation_error.lower():
        return (
            "🔍 Я не поняла, что именно вы хотите сделать.\n\n"
            "Попробуйте конкретнее:\n"
            "• «Покажи документы по продукту Fort Knox» — для поиска документов\n"
            "• «Какие условия у продукта 8837?» — для ответа по базе знаний\n"
            "• «Покажи карточку продукта 8914» — для параметров продукта\n\n"
            f"Ваш запрос: «{_truncate(user_text, 100)}»"
        )
    
    # Если ошибка в route/intent
    if "route" in validation_error.lower() or "intent" in validation_error.lower():
        return (
            "🔍 Не удалось определить тип запроса.\n\n"
            "Я умею:\n"
            "• 📂 Искать документы (презентации, ПФ, памятки)\n"
            "• 💬 Отвечать на вопросы по продуктам АСЖ\n"
            "• 📋 Показывать карточки и комплекты документов по продуктам\n\n"
            f"Ваш запрос: «{_truncate(user_text, 100)}»"
        )
    
    return (
        "🤔 Я не смогла определить, как обработать ваш запрос.\n\n"
        "Попробуйте переформулировать:\n"
        "• Укажите конкретный продукт или тему\n"
        "• Используйте простые формулировки\n\n"
        f"Ваш запрос: «{_truncate(user_text, 100)}»"
    )


def _doc_search_validation_fallback(user_text: str, context: Dict) -> str:
    """Агент поиска документов не смог обработать запрос."""
    search_query = context.get("search_query", "") or user_text
    mode = context.get("mode", "")
    
    # Если агент вернул mode='no_data' — это не ошибка валидации, но всё равно
    if mode == "no_data":
        return (
            "📂 По вашему запросу документы не найдены.\n\n"
            "Попробуйте:\n"
            "• Указать тип документа (презентация, ПФ, памятка, сториз)\n"
            "• Использовать другое название продукта\n"
            "• Указать код продукта (например, 8837)\n\n"
            f"Ваш запрос: «{_truncate(search_query, 100)}»"
        )
    
    return (
        "📂 Не удалось выполнить поиск документов.\n\n"
        "Попробуйте:\n"
        "• Уточнить название продукта или тип документа\n"
        "• Использовать код продукта (например, 8914)\n"
        "• Переформулировать запрос проще\n\n"
        f"Ваш запрос: «{_truncate(search_query, 100)}»"
    )


def _kb_answer_validation_fallback(user_text: str, context: Dict) -> str:
    """Агент ответа по базе знаний не смог обработать запрос."""
    search_query = context.get("search_query", "") or user_text
    source = context.get("source", "")
    
    # Если источник 'none' — значит, ни FAQ, ни KB не дали данных
    if source == "none":
        return (
            "📚 К сожалению, в базе знаний нет информации по этому вопросу.\n\n"
            "Попробуйте:\n"
            "• Переформулировать вопрос другими словами\n"
            "• Указать конкретный продукт (например, Fort Knox, 8837)\n"
            "• Спросить про условия, документы или параметры продукта\n\n"
            f"Ваш запрос: «{_truncate(search_query, 100)}»"
        )
    
    return (
        "📚 Не удалось сформировать ответ по базе знаний.\n\n"
        "Попробуйте:\n"
        "• Переформулировать вопрос\n"
        "• Указать конкретный продукт или тему\n"
        "• Разбить сложный вопрос на несколько простых\n\n"
        f"Ваш запрос: «{_truncate(search_query, 100)}»"
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
                "🔍 Не удалось безопасно получить данные для сравнения продуктов.\n\n"
                "Продукты могли быть распознаны, но параметры для сравнения не были подтверждены запросом к базе данных.\n\n"
                "Попробуйте повторить запрос или указать два точных кода продуктов.\n\n"
                f"Ваш запрос: «{_truncate(search_query, 100)}»"
            )

        return (
            "🔍 Я не смогла выполнить поиск по продуктам.\n\n"
            "Попробуйте уточнить:\n"
            "• Укажите точное название продукта (например, «Fort Knox»)\n"
            "• Или его код (например, «8837»)\n"
            "• Если не уверены — опишите тип продукта\n\n"
            f"Ваш запрос: «{_truncate(search_query, 100)}»"
        )
    
    # Если ошибка в resolved_product — продукт не найден
    if "resolved_product" in validation_error:
        return _product_not_found_fallback(search_query, used_tables)
    
    # Если ошибка в clarification_options
    if "clarification_options" in validation_error:
        return (
            "🤔 Я нашла несколько похожих продуктов, но не могу их корректно отобразить.\n\n"
            "Попробуйте:\n"
            "• Указать точный код продукта (например, 8837)\n"
            "• Или полное название продукта\n\n"
            f"Ваш запрос: «{_truncate(search_query, 100)}»"
        )
    
    # Общий случай для product_selection
    return _product_not_found_fallback(search_query, used_tables)


def _product_not_found_fallback(search_query: str, used_tables: List[str]) -> str:
    """Универсальный fallback, когда продукт не найден."""
    tables_hint = ""
    if used_tables:
        tables_hint = f" (проверены таблицы: {', '.join(used_tables[:2])})"
    
    return (
        f"🤔 Я не нашла продукт по вашему запросу{tables_hint}.\n\n"
        "Попробуйте уточнить:\n"
        "• Укажите точное название продукта (например, «Fort Knox»)\n"
        "• Или его код (например, «8837»)\n"
        "• Если не уверены в названии — опишите тип продукта\n\n"
        f"Ваш запрос: «{_truncate(search_query, 100)}»"
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
            "📂 По вашему запросу документы не найдены.\n\n"
            "Попробуйте:\n"
            "• Указать тип документа (презентация, ПФ, памятка)\n"
            "• Использовать код продукта\n"
            "• Переформулировать запрос\n\n"
            f"Ваш запрос: «{_truncate(search_query, 100)}»"
        )
    
    if agent_name == "kb_answer":
        return (
            "📚 В базе знаний нет информации по этому вопросу.\n\n"
            "Уточните, пожалуйста:\n"
            "• О каком продукте идёт речь?\n"
            "• Что конкретно вас интересует (условия, документы, параметры)?\n\n"
            f"Ваш запрос: «{_truncate(search_query, 100)}»"
        )
    
    if agent_name == "product_selection":
        return _product_not_found_fallback(search_query, context.get("used_tables") or [])
    
    return (
        "❓ К сожалению, я не нашла данных по вашему запросу.\n\n"
        f"Ваш запрос: «{_truncate(search_query, 100)}»"
    )


def _generate_empty_response_fallback(user_text: str, agent_name: Optional[str]) -> str:
    """Fallback при пустом ответе."""
    return (
        "😔 Извините, я не смогла сформировать ответ на ваш запрос.\n\n"
        "Попробуйте:\n"
        "• Переформулировать вопрос проще\n"
        "• Разбить сложный запрос на несколько вопросов\n"
        "• Указать конкретный продукт или тему\n\n"
        f"Ваш запрос: «{_truncate(user_text, 100)}»"
    )


def _generate_generic_fallback(user_text: str) -> str:
    """Общий fallback."""
    return (
        "😔 Не удалось обработать запрос.\n\n"
        "Попробуйте:\n"
        "• Переформулировать вопрос\n"
        "• Указать конкретный продукт или тему\n"
        "• Использовать /reset, если проблема повторяется\n\n"
        f"Ваш запрос: «{_truncate(user_text, 100)}»"
    )


def _truncate(text: str, max_length: int) -> str:
    """Обрезает текст до указанной длины."""
    text = str(text or "").strip()
    if len(text) <= max_length:
        return text
    return text[:max_length].rsplit(" ", 1)[0] + "..."
