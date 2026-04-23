from typing import Any, Dict

from google.adk.agents import LlmAgent
from google.adk.models.lite_llm import LiteLlm

from ..helpers import load_prompt
from ..prompt_loader import start_prompt_watcher
from utils.logger import setup_logger
from .validation_utils import build_validation_error

logger = setup_logger("owasp_agent", "agent.log")


def validate_owasp_result(data: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """
    Проверяет и нормализует результат `owasp_agent`.

    Ожидаемый контракт:
    - `status` равен `ok` или `blocked`;
    - `route` равен `continue` или `reject`;
    - `reason` обязателен всегда;
    - при `status="ok"` обязателен `route="continue"`;
    - при `status="blocked"` обязателен `route="reject"` и непустой `user_message`.

    Возвращает нормализованный словарь с полями:
    - `status`
    - `route`
    - `reason`
    - `user_message`

    При нарушении контракта выбрасывает `ValueError` с диагностическим описанием,
    пригодным для логирования и локализации сбоя на этапе отладки.
    """
    agent_name = "owasp_agent"
    _ = context

    def _validate_payload_type(payload: Dict[str, Any]) -> None:
        if not isinstance(payload, dict):
            raise build_validation_error(
                agent=agent_name,
                stage="payload_type",
                problem=f"expected dict, got {type(payload).__name__}",
            )

    def _validate_basic_fields(payload: Dict[str, Any]) -> tuple[str, str, str, str]:
        status = str(payload.get("status", "")).strip()
        route = str(payload.get("route", "")).strip()
        reason = str(payload.get("reason", "")).strip()
        user_message = str(payload.get("user_message", "")).strip()

        if status not in ("ok", "blocked"):
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem=f"invalid status {status!r}, expected 'ok' or 'blocked'",
                data=payload,
                fields=("status", "route", "reason"),
            )

        if route not in ("continue", "reject"):
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem=f"invalid route {route!r}, expected 'continue' or 'reject'",
                data=payload,
                fields=("status", "route", "reason"),
            )

        if not reason:
            raise build_validation_error(
                agent=agent_name,
                stage="basic_fields",
                problem="reason is required",
                data=payload,
                fields=("status", "route", "reason"),
            )

        return status, route, reason, user_message

    def _validate_semantics(payload: Dict[str, Any], status: str, route: str, user_message: str) -> None:
        if status == "ok" and route != "continue":
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="status='ok' requires route='continue'",
                data=payload,
                fields=("status", "route"),
            )

        if status == "blocked" and route != "reject":
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="status='blocked' requires route='reject'",
                data=payload,
                fields=("status", "route"),
            )

        if status == "blocked" and not user_message:
            raise build_validation_error(
                agent=agent_name,
                stage="semantics",
                problem="blocked status requires non-empty user_message",
                data=payload,
                fields=("status", "route", "user_message"),
            )

    _validate_payload_type(data)
    status, route, reason, user_message = _validate_basic_fields(data)
    _validate_semantics(data, status, route, user_message)

    return {
        "status": status,
        "route": route,
        "reason": reason,
        "user_message": user_message,
    }


def create_owasp_agent(model: LiteLlm) -> LlmAgent:
    """
    Создаёт агента для проверки безопасности запросов (OWASP).
    """
    fallback = """
Ты owasp_agent.
Верни только JSON без markdown и без пояснений.

Безопасный запрос:
{
  "status": "ok",
  "route": "continue",
  "reason": "safe"
}

Небезопасный запрос:
{
  "status": "blocked",
  "route": "reject",
  "reason": "prompt_injection",
  "user_message": "Запрос отклонён по соображениям безопасности."
}
"""
    prompt_file = "owasp_agent_prompt.md"
    instruction = load_prompt(prompt_file, fallback)
    agent = LlmAgent(
        name="owasp_agent",
        model=model,
        instruction=instruction,
        output_key="owasp_result_json",
    )
    start_prompt_watcher(prompt_file, agent, logger)
    return agent
