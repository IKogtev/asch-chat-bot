import os
import traceback
from typing import Any, Dict, Optional
from langfuse import get_client

class LangfuseLogger:
    def __init__(self):
        # рекомендуется использовать get_client().
        # Он автоматически считывает переменные окружения:
        # LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_BASE_URL (или LANGFUSE_HOST)
        self.client = get_client()

    def start_trace(
        self,
        user_id: str,
        input_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        name: str = "root_agent_trace",
    ):
        """
        Создаёт корневой трейс в Langfuse v4.x.
        В новой модели трейс — это корневая observation типа "span".
        """
        trace = self.client.start_observation(
            name=name,
            as_type="span",
            input=input_text,
            metadata={
                **(metadata or {}),
                "user_id": user_id,
            },
        )
        return trace

    def end_trace(
        self,
        trace,
        output: Any = None,
    ):
        """
        Завершает трейс и фиксирует выходные данные
        """
        trace.update(output=output)
        trace.end()
        self.client.flush()

    def error(
        self,
        trace,
        exc: Exception,
    ):
        """
        Логирует ошибку в трейс и завершает его
        """
        trace.update(
            output={
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        trace.end()
        self.client.flush()

    def create_span(
        self,
        trace,
        name: str,
        input: Optional[Dict[str, Any]] = None,
    ):
        """
        Создаёт дочерний span внутри существующего трейса (v4.x)
        """
        span = trace.start_observation(
            name=name,
            as_type="span",
            input=input or {},
        )
        return span

# Глобальный экземпляр для импорта
langfuse_logger = LangfuseLogger()