import asyncio
import json
import os
import uuid
from typing import Dict, Any, Tuple
from fastapi import FastAPI, Request, HTTPException, BackgroundTasks
from langfuse import Langfuse
# Импортируем opentelemetry, чтобы получить автоматически сгенерированный ADK Trace ID
from opentelemetry import trace 

from .start_agent import app
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.events import Event
from google.genai import types as genai_types
from .config import webhook_mode_var 
from utils.logger import setup_logger

logger = setup_logger("webhoock_server")

app_fastapi = FastAPI(title="Langfuse Experiment Webhook")
langfuse = Langfuse()
# чтобы избежать проблем с внутренним кэшем ADK.
global_session_service = InMemorySessionService()

async def _collect_runner_events(runner, user_id: str, session_id: str, content: Any) -> Tuple[str, str, str]:
    """Вспомогательная функция для безопасного сбора событий из асинхронного генератора."""
    # Вспомогательная функция для извлечения документов из распарсенного JSON
    final_text, route, intent = "", "", ""
    async for event in runner.run_async(user_id=user_id, session_id=session_id, new_message=content):
        # Собираем финальный текст от root_agent
        if event.author == "root_agent" and event.content and event.content.parts:
            for part in event.content.parts:
                if part.text:
                    final_text += part.text
        # Анализируем state_delta для поиска служебных данных
        if hasattr(event, 'actions') and event.actions and getattr(event.actions, 'state_delta', None):
            delta = event.actions.state_delta
            # Диспетчер
            if '_dispatcher_result_parsed' in delta:
                route = delta['_dispatcher_result_parsed'].get('route', '')
                intent = delta['_dispatcher_result_parsed'].get('intent', '')
    return final_text, route, intent

async def run_agent_logic(input_data: Dict[str, Any], item_idx: int) -> Dict[str, Any]:
    """Асинхронная логика запуска вашего мультиагента."""
    user_query = input_data.get("user_query", "")
    runner = Runner(app=app, session_service=global_session_service)
    session_id = str(uuid.uuid4())
    user_id = "00000000-0000-0000-0000-000000000001"
    # Создаем сессию с предустановленными тестовыми данными профиля,
    # чтобы агент не падал с KeyError при попытке их прочитать из промптов или логики.
    try:
        await global_session_service.create_session(
            app_name=app.name, 
            user_id=user_id, 
            session_id=session_id,
            state={
                "first_name": "Алексей",
                "last_name": "Винников",
                "full_name": "Алексей Винников",
                "username": "webhook_user",
                "region": "Москва",
                "manager_group": "True",
                "coach_group": "True"
            }
        )
    except Exception as e:
        if "Уже существует" in str(e).lower():
            logger.info(f"⚠️ Сессия {session_id} уже существует (похоже создана Runner). Продолжаем...")
        else:
            raise e
    
    user_event = Event(
        author="user",
        invocation_id=f"inv_{uuid.uuid4().hex[:8]}",
        content=genai_types.Content(role="user", parts=[genai_types.Part(text=user_query)])
    )
    
    final_text, route, intent, status = "", "", "", "ok"
    tracer = trace.get_tracer("webhook_server")
    adk_trace_id = None
    with tracer.start_as_current_span("webhook_experiment_run") as root_span:
        try:
            final_text, route, intent = await asyncio.wait_for(
                _collect_runner_events(runner, user_id, session_id, user_event.content),
                timeout=200.0
            )
                
            span_context = root_span.get_span_context()
            if span_context.is_valid:
                # Конвертируем 128-битный trace_id в 32-значную hex-строку, которую требует Langfuse
                adk_trace_id = format(span_context.trace_id, '032x')
        
        except asyncio.TimeoutError:
            logger.error(f"⏱️ Таймаут при обработке элемента {item_idx}. Query: {user_query[:50]}...")
            status, route, intent = "timeout", "blocked", "blocked"
            final_text = "Извините, обработка запроса заняла слишком много времени."
        except Exception as e:
            logger.error(f"❌ Ошибка при обработке элемента {item_idx}: {e}", exc_info=True)
            status, route, intent = "error", "blocked", "blocked"
            final_text = "Произошла внутренняя ошибка при обработке запроса."

    if "не может быть обработан" in final_text.lower() or "переформулируйте" in final_text.lower():
        status, route = "blocked", "blocked"
    return {
        "final_text": final_text, 
        "route": route, 
        "intent": intent, 
        "status": status,
        "adk_trace_id": adk_trace_id, # Возвращаем вызывающей стороне
    }

def _parse_dataset_input(raw_input: Any) -> Dict[str, Any]:
    """
    Умный парсер input из датасета Langfuse.
    Превращает строку, JSON-строку или словарь в единый формат {"user_query": "..."}.
    """
    if isinstance(raw_input, dict):
        return raw_input
    if isinstance(raw_input, str):
        try:
            parsed = json.loads(raw_input)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
        return {"user_query": raw_input}
    return {"user_query": str(raw_input)}

async def process_dataset_background(
    dataset_name: str, 
    run_name: str, 
    run_description: str = "", 
    run_metadata: dict = None
):
    """Фоновая задача для запуска эксперимента по всему датасету."""
    if run_metadata is None:
        run_metadata = {}
    try:
        logger.info(f"🚀 Запуск эксперимента '{run_name}' для датасета '{dataset_name}'...")
        dataset = langfuse.get_dataset(dataset_name)
        total_items = len(dataset.items)
        logger.info(f"Найдено {total_items} в датасете.")
        
        for idx, item in enumerate(dataset.items):
            logger.info(f"Обработка элемента {idx + 1}/{total_items}...")
            input_data = _parse_dataset_input(item.input)

             # Включаем режим Webhook, чтобы rootagent.py НЕ создавал ручной трейс
            token = webhook_mode_var.set(True)
            try:
                result = await run_agent_logic(input_data, idx)
                adk_trace_id = result.pop("adk_trace_id", None)
                
                if adk_trace_id:
                    # 2. Привязываем к Dataset Run (этот метод у вас уже сработал в логах!)
                    try:
                        langfuse.api.dataset_run_items.create(
                            run_name=run_name,
                            dataset_item_id=item.id,
                            trace_id=adk_trace_id,
                            run_description=run_description,
                            metadata=run_metadata
                        )
                        logger.info(f"✅ элемент {idx + 1} привязан к ADK трассировке: {adk_trace_id}")
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка привязки с описанием ({e}), пробуем без run_description...")
                        # Fallback на случай, если конкретная версия SDK не поддерживает run_description
                        langfuse.api.dataset_run_items.create(
                            dataset_item_id=item.id,
                            trace_id=adk_trace_id,
                            run_name=run_name,
                            metadata=run_metadata
                        )
                else:
                    logger.info(f"⚠️ элемент {idx + 1} не удалось привязать к ADK trace_id.")
            finally:
                webhook_mode_var.reset(token)
                
        logger.info(f"✅ Эксперимент '{run_name}' завершен успешно!")
    except Exception as e:
        logger.info(f"❌ Ошибка обработки датасета {dataset_name}: {e}")

@app_fastapi.post("/webhook/langfuse-experiment")
async def handle_langfuse_webhook(request: Request, background_tasks: BackgroundTasks):
    """Webhook endpoint для запуска экспериментов из Langfuse UI."""
    try:
        payload = await request.json()
        dataset_name = payload.get("datasetName")
        if not dataset_name:
            raise HTTPException(status_code=400, detail="datasetName is required in webhook payload")
        full_model_name = os.environ.get("LLM_API_MODEL", "unknown_model")
        model_name = full_model_name.split("/")[-1]    
        run_name = payload.get("runName") or f"webhook_run_{model_name}_{uuid.uuid4().hex[:8]}"
        run_metadata = payload.get("metadata", {})
        run_description = payload.get("description", "")
        if not run_description and isinstance(run_metadata, dict):
            run_description = run_metadata.get("description", "")
        background_tasks.add_task(
            process_dataset_background, 
            dataset_name, 
            run_name,
            run_description,
            run_metadata
        )
        
        return {"status": "accepted", "dataset": dataset_name, "run_name": run_name, "description":run_description}
    except Exception as e:
        logger.error(f"Ошибка webhook: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app_fastapi, host="0.0.0.0", port=80)