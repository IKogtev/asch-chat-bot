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

# ==============================================================================
# ⚙️ НАСТРОЙКИ НАГРУЗКИ (Rate Limiting)
# ==============================================================================
# Максимальное количество одновременных запросов к LLM. 
# Начнем с 1, чтобы не перегружать модель.
MAX_CONCURRENT_REQUESTS = 3 

# Задержка в секундах между началом обработки каждого элемента.
# Помогает равномерно распределить нагрузку во времени.
REQUEST_DELAY_SECONDS = 2.0 

# Глобальные объекты для контроля
_request_semaphore = asyncio.Semaphore(MAX_CONCURRENT_REQUESTS)
_is_test_running = False
_test_run_lock = asyncio.Lock()

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

async def run_agent_logic(input_data: Dict[str, Any], item_idx: int, session_id: str) -> Dict[str, Any]:
    """Асинхронная логика запуска вашего мультиагента."""
    user_query = input_data.get("user_query", "")
    runner = Runner(app=app, session_service=global_session_service)
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
        error_msg = str(e).lower()
        if "Уже существует" in error_msg or "already exists" in error_msg:
            logger.info(f"⚠️ Сессия {session_id} уже существует. Продолжаем...")
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
    global _is_test_running
    # 1. БЛОКИРОВКА: Запрещаем запуск нового теста, если старый еще идет
    async with _test_run_lock:
        if _is_test_running:
            logger.warning(f"⚠️ Тест для датасета '{dataset_name}' уже запущен. Игнорируем новый запрос, чтобы избежать DDoS.")
            return  # Тихо выходим, не создавая новый фон
        _is_test_running = True
    if run_metadata is None:
        run_metadata = {}
    try:
        logger.info(f"🚀 Запуск эксперимента '{run_name}' для датасета '{dataset_name}'...")
        dataset = langfuse.get_dataset(dataset_name)
        total_items = len(dataset.items)
        logger.info(f"Найдено {total_items} в датасете.")
        # Группировка и сортировка
        conversations = {}  # {conversation_id: [(turn, item), ...]}
        independent_items = [] # Одиночные элементы без conversation_id
        for item in dataset.items:
            meta = item.metadata or {}
            conv_id = meta.get("conversation_id")
            turn = meta.get("turn", 0)
            if conv_id:
                conversations.setdefault(conv_id, []).append((turn, item))
            else:
                independent_items.append(item)
        # Сортируем элементы внутри каждого диалога по номеру шага (turn)
        for conv_id in conversations:
            conversations[conv_id].sort(key=lambda x: x[0])
            
        logger.info(f"Найдено {len(conversations)} диалоговых сценариев и {len(independent_items)} одиночных запросов.")
        # Функция-обертка для обработки одного элемента
        async def process_single_item(item, session_id, idx_label):
            # СЕМАФОР: Ограничиваем количество одновременно выполняющихся запросов
            async with _request_semaphore:
                # ЗАДЕРЖКА: Даем модели "передохнуть" перед началом обработки
                await asyncio.sleep(REQUEST_DELAY_SECONDS)
                input_data = _parse_dataset_input(item.input)
                # Включаем режим Webhook, чтобы rootagent.py НЕ создавал ручной трейс
                token = webhook_mode_var.set(True)
                try:
                    result = await run_agent_logic(input_data, idx_label, session_id)
                    adk_trace_id = result.pop("adk_trace_id", None)
                    
                    if adk_trace_id:
                        # Привязываем к Dataset Run
                        try:
                            langfuse.api.dataset_run_items.create(
                                run_name=run_name, dataset_item_id=item.id, trace_id=adk_trace_id,
                                run_description=run_description, metadata=run_metadata
                            )
                            logger.info(f"✅ [{idx_label}] привязан к ADK trace: {adk_trace_id}")
                        except Exception as e:
                            logger.warning(f"⚠️ Ошибка привязки ({e}), пробуем без description...")
                            langfuse.api.dataset_run_items.create(
                                dataset_item_id=item.id, trace_id=adk_trace_id,
                                run_name=run_name, metadata=run_metadata
                            )
                    else:
                        logger.info(f"⚠️ [{idx_label}] не удалось привязать к ADK trace_id.")
                finally:
                    webhook_mode_var.reset(token)

        # 1. Обработка одиночных запросов (каждый в своей сессии)
        for idx, item in enumerate(independent_items):
            session_id = str(uuid.uuid4())
            logger.info(f"Обработка элемента {idx + 1}/{len(independent_items)}... из всех {total_items}")
            await process_single_item(item, session_id, f"Single-{idx+1}")
        # 2. Обработка диалоговых сценариев (одна сессия на весь диалог)
        for conv_id, items_with_turns in conversations.items():
            # Генерируем ОДИН session_id для всего диалога
            session_id = str(uuid.uuid4()) 
            logger.info(f"🗣️ Запуск диалога '{conv_id}' (шагов: {len(items_with_turns)}) в сессии {session_id}")
            
            for turn_num, (turn_idx, item) in enumerate(items_with_turns):
                label = f"Conv-{conv_id}-Turn-{turn_idx}"
                await process_single_item(item, session_id, label)
                
        logger.info(f"✅ Эксперимент '{run_name}' завершен успешно!")
    except Exception as e:
        logger.info(f"❌ Ошибка обработки датасета {dataset_name}: {e}")
    finally:
        # Снимаем блокировку, чтобы можно было запустить следующий тест
        async with _test_run_lock:
            _is_test_running = False
        logger.info("🔓 Блокировка теста снята. Можно запускать новый эксперимент.")

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