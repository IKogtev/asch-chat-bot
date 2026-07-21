"""
Общий запуск LlmAgent с парсингом JSON в session.state
для оркестраторов и root.
"""
import copy
import json
import time
import ast
from typing import Any, AsyncGenerator, Callable, Dict, Mapping

from google.adk.agents import InvocationContext, LlmAgent
from google.adk.events import Event

from utils.logger import setup_logger
from .doc_search_kb_context import format_kb_hits_summary, parse_kb_search_hits
from .doc_search_validation import DocSearchRetryableValidationError
from .helpers import extract_json, truncate_for_log
from .langfuse_logger import langfuse_logger
from opentelemetry import context
from opentelemetry import trace as otel_trace
from opentelemetry.trace import Status, StatusCode
from .stage_metrics import (
    event_has_model_output,
    event_is_model_turn,
    extract_usage_tokens,
    record_stage_metrics,
    stage_name_from_log_label,
)

logger = setup_logger("json_leaf_runner", "agent.log")


class AgentValidationFailure(Exception):
    """Non-fatal validation/parsing failure for a leaf agent result."""

    def __init__(
        self,
        *,
        log_label: str,
        validation_error: str,
        raw: str,
        user_message: str,
    ) -> None:
        self.log_label = log_label
        self.validation_error = validation_error
        self.raw = raw
        self.user_message = user_message
        super().__init__(f"{log_label}: {validation_error}")


def _copy_with_updates(obj: Any, updates: Dict[str, Any]) -> Any:
    if hasattr(obj, "model_copy"):
        return obj.model_copy(deep=True, update=updates)
    cloned = copy.copy(obj)
    for key, value in updates.items():
        setattr(cloned, key, value)
    return cloned


def _has_meaningful_actions(event: Event) -> bool:
    actions = getattr(event, "actions", None)
    if not actions:
        return False
    values = getattr(actions, "__dict__", None)
    if isinstance(values, dict):
        return any(value not in (None, False, {}, [], "") for value in values.values())
    if hasattr(actions, "model_dump"):
        dumped = actions.model_dump(exclude_none=True)
        return any(value not in (None, False, {}, [], "") for value in dumped.values())
    return True


def strip_thought_parts(event: Event) -> Event | None:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts:
        return event
    filtered_parts = [
        part for part in parts if getattr(part, "thought", False) is not True
    ]
    if len(filtered_parts) == len(parts):
        return event
    if not filtered_parts and not _has_meaningful_actions(event):
        return None
    return _copy_with_updates(
        event,
        {"content": _copy_with_updates(content, {"parts": filtered_parts})},
    )


def _get_mapping_or_attr(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    return getattr(value, key, None)


def _extract_function_call_name(part: Any) -> str | None:
    function_call = (
        _get_mapping_or_attr(part, "function_call")
        or _get_mapping_or_attr(part, "functionCall")
    )
    if not function_call:
        return None
    name = _get_mapping_or_attr(function_call, "name")
    if not name:
        return None
    return str(name).strip() or None


def _safe_json_preview(value: Any, max_length: int = 500) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    return truncate_for_log(text, max_length)


def _extract_function_call_summary(part: Any) -> dict[str, str] | None:
    function_call = (
        _get_mapping_or_attr(part, "function_call")
        or _get_mapping_or_attr(part, "functionCall")
    )
    if not function_call:
        return None
    name = _get_mapping_or_attr(function_call, "name")
    if not name:
        return None
    args = (
        _get_mapping_or_attr(function_call, "args")
        or _get_mapping_or_attr(function_call, "arguments")
        or {}
    )
    return {
        "type": "call",
        "name": str(name).strip(),
        "args_preview": _safe_json_preview(args, 500),
    }


def _response_to_text(response: Any) -> str:
    if response is None:
        return ""
    if isinstance(response, str):
        return response
    if isinstance(response, list):
        parts = [_response_to_text(item) for item in response]
        return "\n".join(part for part in parts if part)
    if isinstance(response, dict):
        if response.get("type") == "text" and "text" in response:
            return str(response.get("text") or "")
        for key in ("content", "text", "result"):
            if key in response:
                nested = _response_to_text(response.get(key))
                if nested:
                    return nested
        try:
            return json.dumps(response, ensure_ascii=False, default=str)
        except Exception:
            return str(response)
    return str(response)


def _extract_function_response_raw(part: Any) -> tuple[str, Any] | None:
    function_response = (
        _get_mapping_or_attr(part, "function_response")
        or _get_mapping_or_attr(part, "functionResponse")
    )
    if not function_response:
        return None
    name = _get_mapping_or_attr(function_response, "name")
    if not name:
        return None
    response = (
        _get_mapping_or_attr(function_response, "response")
        or _get_mapping_or_attr(function_response, "result")
        or {}
    )
    return str(name).strip(), response


def _extract_kb_search_response_texts_from_event(event: Event) -> list[str]:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts and isinstance(event, dict):
        content = event.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
    if not parts:
        return []
    texts: list[str] = []
    for part in parts:
        raw = _extract_function_response_raw(part)
        if not raw:
            continue
        name, response = raw
        if name != "kb_search":
            continue
        text = _response_to_text(response).strip()
        if text:
            texts.append(text)
    return texts


def _store_doc_search_kb_hits(ctx: InvocationContext, response_texts: list[str]) -> None:
    attempt = ctx.session.state.get("doc_search_attempt")
    rerank_only = ctx.session.state.get("doc_search_rerank_only")
    if rerank_only:
        existing = ctx.session.state.get("_doc_search_kb_hits")
        existing_hits = existing if isinstance(existing, list) else []
        logger.info(
            "doc_search kb_hits: attempt=%s rerank_only=True keep_existing %s",
            attempt,
            format_kb_hits_summary(existing_hits),
        )
        return
    if not response_texts:
        logger.info(
            "doc_search kb_hits: attempt=%s no kb_search response to store",
            attempt,
        )
        return
    hits = parse_kb_search_hits(response_texts[-1])
    if hits:
        ctx.session.state["_doc_search_kb_hits"] = hits
        logger.info(
            "doc_search kb_hits: attempt=%s stored_from_kb_search %s",
            attempt,
            format_kb_hits_summary(hits),
        )
    else:
        logger.info(
            "doc_search kb_hits: attempt=%s kb_search response parsed empty",
            attempt,
        )


def _extract_function_response_summary(part: Any) -> dict[str, str] | None:
    function_response = (
        _get_mapping_or_attr(part, "function_response")
        or _get_mapping_or_attr(part, "functionResponse")
    )
    if not function_response:
        return None
    name = _get_mapping_or_attr(function_response, "name") or ""
    response = (
        _get_mapping_or_attr(function_response, "response")
        or _get_mapping_or_attr(function_response, "result")
        or {}
    )
    return {
        "type": "response",
        "name": str(name).strip(),
        "response_preview": _safe_json_preview(response, 800),
    }


def _extract_tool_event_summaries(event: Event) -> list[dict[str, str]]:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts and isinstance(event, dict):
        content = event.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
    if not parts:
        return []
    summaries = []
    for part in parts:
        call_summary = _extract_function_call_summary(part)
        if call_summary:
            summaries.append(call_summary)
            continue
        response_summary = _extract_function_response_summary(part)
        if response_summary:
            summaries.append(response_summary)
    return summaries


def _extract_function_call_names(event: Event) -> list[str]:
    content = getattr(event, "content", None)
    parts = getattr(content, "parts", None)
    if not parts and isinstance(event, dict):
        content = event.get("content")
        parts = content.get("parts") if isinstance(content, dict) else None
    if not parts:
        return []
    names = []
    for part in parts:
        name = _extract_function_call_name(part)
        if name:
            names.append(name)
    return names
# ХЕЛПЕРЫ ДЛЯ OPENTELEMETRY
def _extract_clean_state(state: Any) -> dict:
    """Безопасно извлекает только JSON-сериализуемые примитивы из стейта сессии."""
    if state is None:
        return {}
    if hasattr(state, "to_dict") and callable(getattr(state, "to_dict")):
        try:
            state_dict = state.to_dict()
        except Exception:
            state_dict = {}
    elif hasattr(state, "__dict__"):
        state_dict = state.__dict__
    elif isinstance(state, dict):
        state_dict = state
    else:
        try:
            state_dict = dict(state)
        except Exception:
            state_dict = {}

    clean = {}
    for k, v in state_dict.items():
        if k.startswith("_"):  # Пропускаем служебные приватные переменные
            continue
        # Оставляем только плоские типы или простые структуры для логов
        if isinstance(v, (str, int, float, bool, type(None))):
            clean[k] = v
        elif isinstance(v, (list, dict)):
            try:
                json.dumps(v, ensure_ascii=False)
                clean[k] = v
            except Exception:
                pass
    return clean

def _extract_text_from_event(event: Any) -> str:
    """Безопасно извлекает текстовое содержимое из событий Google ADK."""
    if not event:
        return ""
    # 1. Проверяем стандартную структуру ADK (event.content.parts)
    if hasattr(event, "content") and event.content:
        content = event.content
        if hasattr(content, "parts") and content.parts:
            parts_text = []
            for part in content.parts:
                if hasattr(part, "text") and part.text:
                    parts_text.append(part.text)
                elif isinstance(part, str):
                    parts_text.append(part)
            return "".join(parts_text)
        elif isinstance(content, str):
            return content
    # 2. Фолбек на плоский текст события
    if hasattr(event, "text") and event.text:
        return event.text
    return ""

async def instrumented_agent_run(agent: Any, ctx: Any, input_data: Any) -> AsyncGenerator[Any, None]:
    """
    Обертка над генератором ADK, которая перехватывает активный спан 
    и обогащает его входными/выходными данными для Langfuse.
    """
    full_output = ""
    span_enriched = False
    # Запускаем оригинальный генератор ADK
    async for event in agent.run_async(ctx):
        # Получаем текущий активный спан ADK (например, "invoke_agent owasp_agent")
        current_span = otel_trace.get_current_span()
        if current_span and current_span.is_recording():
            # Записываем Input на первой итерации
            if not span_enriched:
                input_str = input_data if isinstance(input_data, str) else json.dumps(input_data, ensure_ascii=False)
                # Записываем как в стандартном OpenInference, так и в специфичных для Langfuse ключах
                current_span.set_attribute("input.value", input_str)
                current_span.set_attribute("langfuse.observation.input", input_str)
                span_enriched = True
            # Собираем чанки выходного текста
            chunk_text = _extract_text_from_event(event)
            if chunk_text:
                full_output += chunk_text
                # Обновляем Output на лету (безопасно при стриминге и раннем выходе)
                current_span.set_attribute("output.value", full_output)
                current_span.set_attribute("langfuse.observation.output", full_output)
        yield event

async def run_json_leaf_agent(
    ctx: InvocationContext,
    agent: LlmAgent,
    output_key: str,
    parsed_state_key: str,
    validator: Callable[[Dict[str, Any], Dict[str, Any]], Dict[str, Any]],
    log_label: str,
    validation_error_user_message: str,
) -> AsyncGenerator[Event, None]:
    # OPENTELEMETRY: СОЗДАНИЕ ВЛОЖЕННОГО СПАНА
    tracer = otel_trace.get_tracer("json_leaf_runner")
    # Создаем спан. Благодаря OTel он автоматически станет "ребенком" активного агента ADK
    span = tracer.start_span(log_label)
    # Динамически собираем актуальные параметры сессии перед запуском агента
    state_input = _extract_clean_state(ctx.session.state)
    # Записываем входящие параметры в семантический атрибут 'input'
    span.set_attribute(
        "input.value",
        json.dumps({
            "agent_name": agent.name,
            "log_label": log_label,
            "session_state_before": state_input,
            # Оставляем корневые ключи для обратной совместимости
            "user_query": ctx.session.state.get("user_query"),
            "search_query": ctx.session.state.get("search_query"),
        }, ensure_ascii=False)
    )
    span.set_attribute("input.mime_type", "application/json")
    # Делаем спан активным в OTel-контексте для текущей корутины/генератора
    token = context.attach(otel_trace.set_span_in_context(span))    
    try:
        # МЕТРИКИ И ТАЙМИНГИ
        _doc_timing = log_label == "doc_search_result_json"
        stage_name = stage_name_from_log_label(log_label)
        _t_llm0 = time.monotonic()
        ttft_ms: int | None = None
        input_tokens = 0
        output_tokens = 0
        model_turns = 0
        usage_model_turns = 0
        output_model_turns = 0
        tool_calls: list[str] = []
        tool_event_summaries: list[dict[str, str]] = []
        kb_search_response_texts: list[str] = []
        # 1. Достаем входящий запрос для обогащения внутреннего спана ADK
        agent_input = (
            ctx.session.state.get("user_query") 
            or ctx.session.state.get("search_query") 
            or ""
        )

        async for event in instrumented_agent_run(agent, ctx, input_data=agent_input):
            tool_calls.extend(_extract_function_call_names(event))
            tool_event_summaries.extend(_extract_tool_event_summaries(event))
            
            if _doc_timing:
                kb_search_response_texts.extend(
                    _extract_kb_search_response_texts_from_event(event)
                )
                
            # Считаем TTFT и токены
            if ttft_ms is None and event_has_model_output(event):
                ttft_ms = int((time.monotonic() - _t_llm0) * 1000.0)
                
            usage = getattr(event, "usage_metadata", None)
            if usage is None and isinstance(event, dict):
                usage = event.get("usage_metadata") or event.get("usageMetadata")
                
            if usage is not None:
                usage_model_turns += 1
            elif event_is_model_turn(event):
                output_model_turns += 1
                
            in_tok, out_tok = extract_usage_tokens(usage)
            input_tokens += in_tok
            output_tokens += out_tok

            sanitized_event = strip_thought_parts(event)
            if sanitized_event is not None:
                yield sanitized_event
        model_turns = usage_model_turns if usage_model_turns > 0 else output_model_turns
        llm_ms = int((time.monotonic() - _t_llm0) * 1000.0)
        if stage_name:
            record_stage_metrics(
                ctx.session.state,
                stage_name,
                ms=llm_ms,
                ttft_ms=ttft_ms if ttft_ms is not None else llm_ms,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                tool_calls=len(tool_calls),
                model_turns=model_turns,
            )
            logger.info(
                "stage_metrics: stage=%s ms=%s ttft_ms=%s input_tokens=%s output_tokens=%s "
                "tool_calls=%s model_turns=%s",
                stage_name,
                llm_ms,
                ttft_ms if ttft_ms is not None else llm_ms,
                input_tokens,
                output_tokens,
                len(tool_calls),
                model_turns,
            )

        if _doc_timing:
            _store_doc_search_kb_hits(ctx, kb_search_response_texts)
            logger.info(
                "doc_search_result_json: attempt=%s rerank_only=%s kb_search_calls=%s",
                ctx.session.state.get("doc_search_attempt"),
                ctx.session.state.get("doc_search_rerank_only"),
                tool_calls.count("kb_search"),
            )
        # Достаем сырой результат без жесткого каста к str на первом шаге
        raw_payload = ctx.session.state.get(output_key)
        logger.debug(
            "%s tool diagnostics: calls=%s events=%s",
            log_label,
            tool_calls,
            json.dumps(tool_event_summaries, ensure_ascii=False),
        )
        logger.debug("%s raw: %s", log_label, truncate_for_log(raw_payload, 500))
        
        _t_parse0 = time.monotonic() if _doc_timing else None
        try:
            if isinstance(raw_payload, (dict, Mapping)):
                # Ветка 1: Если новый агент с output_schema уже вернул готовый Python-словарь
                extracted = dict(raw_payload)
            elif hasattr(raw_payload, "model_dump"):
                # Ветка 2: Если вернулся Pydantic объект напрямую
                extracted = raw_payload.model_dump()
            else:
                # Ветка 3: Если вернулась строка (старый формат из промпта)
                raw_str = str(raw_payload or "").strip()
                try:
                    # Шаг А: Стандартный строгий JSON (для двойных кавычек)
                    extracted = extract_json(raw_str)
                except Exception:
                    # Шаг Б: Если упал, пробуем безопасно распарсить одинарные кавычки 
                    parsed_literal = ast.literal_eval(raw_str)
                    if isinstance(parsed_literal, (dict, Mapping)):
                        extracted = dict(parsed_literal)
                    else:
                        raise ValueError("Parsed literal from string is not a dictionary")
            logger.debug("%s extracted: %s", log_label, json.dumps(extracted, ensure_ascii=False))
            validator_context = dict(getattr(ctx.session, "state", {}) or {})
            validator_context["_adk_tool_calls"] = tool_calls
            validator_context["_adk_tool_event_summaries"] = tool_event_summaries
            parsed = validator(extracted, validator_context)
        except DocSearchRetryableValidationError:
            raise
        except Exception as exc:
            logger.warning(
                "%s validation/parsing error: %s; user_query=%s; search_query=%s; intent=%s; route=%s; raw=%s",
                log_label,
                exc,
                truncate_for_log(ctx.session.state.get("user_query"), 200),
                truncate_for_log(ctx.session.state.get("search_query"), 200),
                truncate_for_log(ctx.session.state.get("intent"), 100),
                truncate_for_log(ctx.session.state.get("route"), 100),
                truncate_for_log(str(raw_payload), 500),
            )
            raise AgentValidationFailure(
                log_label=log_label,
                validation_error=str(exc),
                raw=str(raw_payload),
                user_message=validation_error_user_message,
            ) from exc

        if _doc_timing and _t_parse0 is not None:
            _parse_ms = (time.monotonic() - _t_parse0) * 1000.0
            logger.debug(
                "doc_search LLM timing: agent.run_async wall_ms=%s; json_extract+validate wall_ms=%.1f",
                llm_ms,
                _parse_ms,
            )

        ctx.session.state[parsed_state_key] = parsed
        logger.debug("%s parsed: %s", log_label, json.dumps(parsed, ensure_ascii=False))
        # --- OPENTELEMETRY: ЗАПИСЬ УСПЕШНОГО РЕЗУЛЬТАТА ---
        # Делаем финальный слепок состояния сессии
        state_output = _extract_clean_state(ctx.session.state)
        span.set_attribute(
            "output.value",
            json.dumps({
                "raw_llm_response": str(raw_payload)[:2000],
                "parsed_result": parsed,
                "session_state_after": state_output
            }, ensure_ascii=False)
        )
        span.set_attribute("output.mime_type", "application/json")
        span.set_status(Status(StatusCode.OK))
    except Exception as exc:
        # --- LANGFUSE: ЗАВЕРШЕНИЕ СПАНА С ОШИБКОЙ ---
        span.record_exception(exc)
        span.set_status(Status(StatusCode.ERROR, description=str(exc)))
        state_output = _extract_clean_state(ctx.session.state)
        span.set_attribute(
            "output.value",
            json.dumps({
                "error": str(exc),
                "type": type(exc).__name__,
                "session_state_after": state_output
            }, ensure_ascii=False)
        )
        span.set_attribute("output.mime_type", "application/json")
        raise
    finally:
        # Очищаем контекст через context.detach и закрываем спан
        context.detach(token)
        span.end()
