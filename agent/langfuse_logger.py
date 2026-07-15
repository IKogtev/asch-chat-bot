import os
import traceback
from typing import Any, Dict, Optional
from langfuse import get_client
from langfuse import Langfuse
from utils.logger import setup_logger
from .config import experiment_trace_var

logger = setup_logger("LANGFUSE")
class LangfuseLogger:
    def __init__(self):
        # ПРЯМАЯ инициализация. Никаких get_client(), чтобы избежать OTel-перехвата
        self.client = Langfuse(
            public_key=os.getenv("LANGFUSE_PUBLIC_KEY"),
            secret_key=os.getenv("LANGFUSE_SECRET_KEY"),
            host=os.getenv("LANGFUSE_HOST") or os.getenv("LANGFUSE_BASE_URL", "http://localhost:3000"),
        )
        logger.info("✅ Langfuse client initialized DIRECTLY (bypassing OTel).")

    def start_trace(
        self,
        user_id: str,
        input_text: str,
        metadata: Optional[Dict[str, Any]] = None,
        name: str = "root_agent_trace",
    ):
        """
        Создаёт корневой трейс. 
        В Langfuse v4 модель стала observation-centric: метод .trace() был удалён.
        Вместо него корневой трейс создаётся как корневая observation типа "span" 
        с помощью метода .start_observation(as_type="span").
        """
        existing_trace = experiment_trace_var.get()
        if existing_trace:
            # Если да, то обновляем input существующего трейса и возвращаем его.
            # Теперь все дочерние спаны (из json_leaf_runner) пойдут внутрь него!
            existing_trace.update(input=input_text)
            return existing_trace
        # В v4 атрибуты user_id/session_id лучше всего передавать через metadata 
        # при ручном управлении объектами (без контекстных менеджеров propagate_attributes),
        # чтобы гарантировать их видимость в UI Langfuse.
        merged_metadata = {**(metadata or {}), "user_id": user_id}
        
        # Создаём корневую observation, которая будет выступать в роли trace
        trace = self.client.start_observation(
            as_type="span",
            name=name,
            input=input_text,
            metadata=merged_metadata,
        )
        
        return trace

    def update_current_span(
        self,
        *,
        input=None,
        output=None,
        metadata=None,
        level=None,
    ):
        updates = {}

        if input is not None:
            updates["input"] = input

        if output is not None:
            updates["output"] = output

        if metadata is not None:
            updates["metadata"] = metadata

        if level is not None:
            updates["level"] = level

        self.client.update_current_span(**updates)

    def update_current_generation(self,):

        self.client.update_current_generation()

    def end_trace(self, trace, output: Any = None):
        """Корректно завершает трейс."""
        if output is not None:
            trace.update(output=output)
        trace.end()
        self.client.flush()

    def error(self, trace, exc: Exception):
        """Логирует ошибку в трейс."""
        trace.update(
            output={
                "error": str(exc),
                "traceback": traceback.format_exc(),
            }
        )
        trace.end()
        self.client.flush()

    def create_span(self, trace, name: str, input: Optional[Dict[str, Any]] = None, metadata: Optional[Dict[str, Any]] = None):
        """Создаёт дочерний span внутри трейса."""
        return trace.start_observation(
            as_type="span",
            name=name,
            input=input or {},
            metadata=metadata or {},
        )
langfuse_logger = LangfuseLogger()