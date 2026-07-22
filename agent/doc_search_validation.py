"""Исключения и константы валидации doc_search."""
from __future__ import annotations

DOC_SEARCH_MAX_ATTEMPTS = 2
DOC_SEARCH_NO_DATA_MESSAGE = """
Не могу найти продукты, соответствующие запросу, пожалуйста дополни его одним из критериев, который тебе нужен:
документы (файлы содержащие текст запроса)
карточка/параметры продукта (информация о свойствах продукте)
комплект документов (введи "дай комплект по *нзвание или код продукта* и я предоставлю комплект")
! Добавь в запрос слово "архив", если нужно поискать не активные продукты.
"""


class DocSearchRetryableValidationError(ValueError):
    """Ошибка валидации, при которой имеет смысл повторить переранжирование."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        message = f"doc_search retryable validation: {reason}"
        if detail:
            message += f": {detail}"
        super().__init__(message)
