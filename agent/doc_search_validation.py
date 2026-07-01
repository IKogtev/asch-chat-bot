"""Исключения и константы валидации doc_search."""
from __future__ import annotations

DOC_SEARCH_MAX_ATTEMPTS = 2
DOC_SEARCH_NO_DATA_MESSAGE = "Подходящих документов по запросу не найдено."


class DocSearchRetryableValidationError(ValueError):
    """Ошибка валидации, при которой имеет смысл повторить переранжирование."""

    def __init__(self, reason: str, detail: str = "") -> None:
        self.reason = reason
        message = f"doc_search retryable validation: {reason}"
        if detail:
            message += f": {detail}"
        super().__init__(message)
