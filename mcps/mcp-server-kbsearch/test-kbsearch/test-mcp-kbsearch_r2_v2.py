import asyncio
import json
import time
from pathlib import Path
from typing import Dict, List, Any
from datetime import datetime
import logging
from urllib.parse import urlparse

import requests
import httpx
from dotenv import load_dotenv
import os

from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openai import ChatOpenAI
from langchain_classic import hub
from langchain_core.prompts import PromptTemplate, ChatPromptTemplate
from langchain_core.messages import AIMessage, ToolMessage, SystemMessage, HumanMessage
from langchain_classic.agents import AgentExecutor, create_react_agent
from langchain_core.tools import Tool
from langchain_core.runnables import RunnablePassthrough

# ============================================================================
# SETUP: ИМПОРТЫ И КОНФИГУРАЦИЯ
# ============================================================================

# Загружаем переменные окружения
load_dotenv()

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Конфигурация
MCP_SERVER_URL = os.getenv("MCP_KBSEARCH_URL", "http://localhost:7001")
MCP_ENDPOINT = os.getenv("MCP_KBSEARCH", "/kbsearch/mcp")
MCP_TOKEN = os.getenv("MCP_TOKEN", "REDACTED_EXAMPLE-here")

# OpenAI/OpenRouter конфиг
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL", "https://rp01.nstdata.ru:60123/openrouter/api/v1")
MODEL_NAME = os.getenv("OPENROUTER_API_MODEL", "qwen/qwen3-30b-a3b")

# Параметры для загрузки из S3
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://storage.yandexcloud.net")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "REDACTED_EXAMPLE")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "REDACTED_EXAMPLE")
S3_BUCKET = os.getenv("S3_BUCKET", "sandbox-2-k8s-mcp-b1ga7h8ijbukqu3mljmu")
S3_PREFIX = os.getenv("S3_PREFIX", "mcp_inputs/kbsearch")

# Результаты тестов
test_results = {
    "timestamp": datetime.now().isoformat(),
    "tests": {},
    "metrics": {}
}

def wrap_mcp_tools_for_react(mcp_tools):
    """Преобразует MCP-tools с args_schema в простые строковые Tool для ReAct."""
    react_tools = []

    for mcp_tool in mcp_tools:
        # важный трюк: биндим mcp_tool через аргумент по умолчанию,
        # чтобы не словить баг с замыканиями в цикле
        async def _coroutine(input_str: str, _tool=mcp_tool):
            # 1. если агент прислал JSON-строку - парсим
            payload = None
            if isinstance(input_str, str):
                try:
                    payload = json.loads(input_str)
                except json.JSONDecodeError:
                    payload = None

            # 2. если не JSON или не строка — пытаемся маппить по схеме
            if payload is None:
                schema = getattr(_tool, "args_schema", None)

                # если у инструмента есть pydantic-схема и 1 поле — кладём строку туда
                if schema is not None and hasattr(schema, "__fields__"):
                    fields = list(schema.__fields__.keys())
                    if len(fields) == 1:
                        payload = {fields[0]: input_str}
                    else:
                        # fallback: кладём как "query"
                        payload = {"query": input_str}
                else:
                    # вообще без схемы — кладём в "input"
                    payload = {"input": input_str}

            # 3. вызываем оригинальный MCP-инструмент
            return await _tool.ainvoke(payload)

        react_tool = Tool.from_function(
                    func=None,  # sync-реализация не нужна
                    name=mcp_tool.name,
                    description=getattr(mcp_tool, "description", "") or "",
                    coroutine=_coroutine,
                )

        react_tools.append(react_tool)
    return react_tools

# ============================================================================
# 1. HEALTHCHECK: ПРОВЕРКА ДОСТУПНОСТИ СЕРВЕРА
# ============================================================================

async def test_server_availability() -> Dict[str, Any]:
    """
    Проверить доступность MCP-server.
    
    Returns:
        Dict с результатами проверки
    """
    logger.info("=" * 70)
    logger.info("1. HEALTHCHECK: ПРОВЕРКА ДОСТУПНОСТИ СЕРВЕРА")
    logger.info("=" * 70)
    
    result = {
        "name": "Server Availability",
        "status": "FAILED",
        "details": {},
        "timestamp": datetime.now().isoformat()
    }
    
    try:
        # Проверка базовой доступности
        logger.info(f"Проверяем доступность сервера: {MCP_SERVER_URL}")
        response = requests.get(f"{MCP_SERVER_URL}/kb/status", timeout=5)
        
        if response.status_code == 200:
            logger.info("✓ Сервер доступен")
            result["details"]["http_status"] = response.status_code
            result["details"]["response"] = response.json()
            result["status"] = "PASSED"
        else:
            logger.warning(f"⚠ Сервер вернул статус {response.status_code}")
            result["details"]["http_status"] = response.status_code
            result["details"]["error"] = response.text[:200]
            
    except requests.exceptions.ConnectionError as e:
        logger.error(f"✗ Ошибка подключения: {e}")
        result["details"]["error"] = f"Connection error: {str(e)}"
        
    except Exception as e:
        logger.error(f"✗ Ошибка при проверке: {e}")
        result["details"]["error"] = str(e)
    
    test_results["tests"]["healthcheck"] = result
    return result

# ============================================================================
# 2. ИНДЕКСАЦИЯ БЗ: ЗАГРУЗКА И ИНДЕКСИРОВАНИЕ ДОКУМЕНТОВ
# ============================================================================

async def test_kb_indexing() -> Dict[str, Any]:
    """
    Загрузить и проиндексировать базу знаний из S3.
    
    Returns:
        Dict с результатами индексации
    """
    logger.info("\n" + "=" * 70)
    logger.info("2. ИНДЕКСАЦИЯ БЗ: ЗАГРУЗКА И ИНДЕКСИРОВАНИЕ ДОКУМЕНТОВ (S3)")
    logger.info("=" * 70)
    
    result = {
        "name": "KB Indexing from S3",
        "status": "FAILED",
        "details": {},
        "timestamp": datetime.now().isoformat()
    }
    
    try:
        # Проверяем наличие S3 credentials
        if not S3_ACCESS_KEY or not S3_SECRET_KEY:
            logger.error("✗ S3 credentials не найдены в .env (S3_ACCESS_KEY, S3_SECRET_KEY)")
            result["details"]["error"] = "Missing S3 credentials in .env"
            test_results["tests"]["kb_indexing"] = result
            return result
        
        logger.info(f"S3 конфигурация:")
        logger.info(f"  Endpoint: {S3_ENDPOINT}")
        logger.info(f"  Bucket: {S3_BUCKET}")
        logger.info(f"  Prefix: {S3_PREFIX}")
        
        # Отправляем запрос на обновление KB из S3
        logger.info("Отправляем запрос на загрузку и индексацию из S3...")
        
        async with httpx.AsyncClient(timeout=600.0) as client:
            # Добавляем токен если требуется
            headers = {}
            if MCP_TOKEN:
                headers["Authorization"] = f"Bearer {MCP_TOKEN}"
            
            params = {
                "source_type": "s3",
                "mode": "replace",
                "s3_endpoint": S3_ENDPOINT,
                "s3_bucket": S3_BUCKET,
                "s3_prefix": S3_PREFIX,
                "s3_access_key": S3_ACCESS_KEY,
                "s3_secret_key": S3_SECRET_KEY,
            }
            
            start_time = time.time()
            try:
                response = await client.get(
                    f"{MCP_SERVER_URL}/kb/update",
                    params=params,
                    headers=headers,
                    timeout=600.0
                )
            except httpx.TimeoutException as e:
                logger.error(f"✗ Timeout при индексации (может быть нормально для больших БЗ): {e}")
                result["status"] = "TIMEOUT"
                result["details"]["error"] = "Timeout - indexing may still be in progress"
                test_results["tests"]["kb_indexing"] = result
                return result
            
            elapsed_time = time.time() - start_time
            logger.info(f"Ответ сервера: {response.status_code} (время: {elapsed_time:.2f}s)")
            
            if response.status_code == 200:
                response_data = response.json()
                
                if response_data.get("success"):
                    logger.info("✓ Индексация из S3 успешно завершена")
                    result["status"] = "PASSED"
                    result["details"]["response"] = response_data
                    result["details"]["elapsed_time"] = elapsed_time
                    result["details"]["source"] = "S3"
                    
                    # Получаем статус KB
                    status = response_data.get("status", {})
                    result["details"]["kb_status"] = status
                    metadata = status.get('metadata', {})
                    logger.info(f"  Статус KB: {str(metadata.get('index_status'))}")
                    logger.info(f"  Документов в индексе: {status.get('documents_count')}")
                else:
                    logger.error(f"✗ Ошибка индексации: {response_data.get('error')}")
                    result["details"]["error"] = response_data.get("error")
            else:
                logger.error(f"✗ Ошибка HTTP: {response.status_code}")
                result["details"]["error"] = response.text[:500]
                
    except (asyncio.TimeoutError, httpx.TimeoutException):
        logger.error("✗ Timeout при индексации из S3 (может быть нормально для больших БЗ)")
        result["details"]["error"] = "Timeout - indexing may still be in progress"
        result["status"] = "TIMEOUT"
        
    except Exception as e:
        logger.error(f"✗ Ошибка при индексации из S3: {e}")
        result["details"]["error"] = str(e)
    
    test_results["tests"]["kb_indexing"] = result
    return result

# ============================================================================
# 3. MCP КЛИЕНТ: ПОДКЛЮЧЕНИЕ И ПОЛУЧЕНИЕ ИНСТРУМЕНТОВ
# ============================================================================
async def test_mcp_client_connection() -> Dict[str, Any]:
    """
    Создать MCP-клиент и получить список инструментов (через langchain-mcp-adapters).
    """
    logger.info("\n" + "=" * 70)
    logger.info("3. MCP КЛИЕНТ: ПОДКЛЮЧЕНИЕ И ПОЛУЧЕНИЕ ИНСТРУМЕНТОВ")
    logger.info("=" * 70)

    result = {
        "name": "MCP Client Connection",
        "status": "FAILED",
        "details": {},
        "timestamp": datetime.now().isoformat()
    }

    client: MultiServerMCPClient | None = None

    try:
        mcp_url = f"{MCP_SERVER_URL}{MCP_ENDPOINT}"
        logger.info(f"Подключаемся к MCP серверу (streamable_http): {mcp_url}")

        parsed_url = urlparse(mcp_url)
        host = parsed_url.hostname or "localhost"
        port = parsed_url.port or 8001
        logger.info(f"Разобранный адрес: host={host}, port={port}")

        headers = {"Authorization": f"Bearer {MCP_TOKEN}"} if MCP_TOKEN else {}
        print(headers)
        # Формируем конфигурацию для сервера
        server_config = {
            "transport": "streamable_http",
            "url": mcp_url,
        }
        if headers:
            server_config["headers"] = headers

        # Создаём MultiServerMCPClient (можно несколько серверов, но нам пока один)
        client = MultiServerMCPClient(
            {
                "kbsearch": server_config
            }
        )

        logger.info("Получаем список инструментов через MultiServerMCPClient...")
        tools = await client.get_tools()  # все инструменты со всех MCP-серверов

        if not tools:
            logger.error("✗ Не удалось получить список инструментов или список пуст")
            result["details"]["error"] = "No tools found or failed to retrieve"
            test_results["tests"]["mcp_client"] = result
            return result

        logger.info(f"✓ Получено инструментов: {len(tools)}")

        tools_info = []
        for tool in tools:
            tool_name = getattr(tool, "name", "Unknown")
            tool_desc = getattr(tool, "description", "No description")

            # Для StructuredTool / DynamicStructuredTool берём pydantic-схему
            args_schema = getattr(tool, "args_schema", None)
            if args_schema is not None:
                try:
                    parameters = args_schema.schema()
                except Exception:
                    parameters = {}
            else:
                parameters = {}

            logger.info(f"  - {tool_name}: {tool_desc[:80]}...")

            tools_info.append(
                {
                    "name": tool_name,
                    "description": tool_desc,
                    "parameters": parameters,
                }
            )

        result["status"] = "PASSED"
        result["details"]["tools_count"] = len(tools)
        result["details"]["tools"] = tools_info

        # Проверяем наличие инструмента kbsearch
        tool_names = [t["name"] for t in tools_info]

        if "kb_search" in tool_names:
            logger.info("✓ Инструмент 'kb_search' найден")
            result["details"]["kb_search_found"] = True
        else:
            logger.warning("⚠ Инструмент 'kb_search' не найден")
            logger.warning(f"  Доступные инструменты: {tool_names}")
            result["details"]["kb_search_found"] = False
            result["status"] = "PASSED_PARTIAL"

    except Exception as e:
        logger.error(f"✗ Неожиданная ошибка при работе MCP клиента: {e}", exc_info=True)
        result["details"]["error"] = str(e)
        result["details"]["error_type"] = type(e).__name__

    # MultiServerMCPClient стейтлесс — явного disconnect не требуется
    test_results["tests"]["mcp_client"] = result
    return result

# ============================================================================
# 4. LANGCHAIN AGENT: СОЗДАНИЕ И ПРОВЕРКА ИНСТРУМЕНТОВ
# ============================================================================

async def test_langchain_agent() -> Dict[str, Any]:
    """
    Создать LangChain агента с инструментом из MCP-server.
    """
    logger.info("\n" + "=" * 70)
    logger.info("4. LANGCHAIN AGENT: СОЗДАНИЕ И ПРОВЕРКА ИНСТРУМЕНТОВ")
    logger.info("=" * 70)
    
    result = {
        "name": "LangChain Agent",
        "status": "FAILED",
        "details": {},
        "timestamp": datetime.now().isoformat()
    }
    
    client: MultiServerMCPClient | None = None
    
    try:
        mcp_url = f"{MCP_SERVER_URL}{MCP_ENDPOINT}"
        logger.info(f"Создаём LangChain агента с MCP инструментами от {mcp_url}")

        headers = {"Authorization": f"Bearer {MCP_TOKEN}"} if MCP_TOKEN else {}

        # Формируем конфигурацию для сервера
        server_config = {
            "transport": "streamable_http",
            "url": mcp_url,
        }
        if headers:
            server_config["headers"] = headers

        # Создаём MultiServerMCPClient
        client = MultiServerMCPClient({"kbsearch": server_config})
        
        # Получаем инструменты
        tools = await client.get_tools()
        
        if not tools:
            logger.error("✗ Инструменты не получены")
            result["status"] = "FAILED"
            result["details"]["error"] = "No tools available"
            test_results["tests"]["langchain_agent"] = result
            return result
        
        logger.info(f"✓ Получено инструментов: {len(tools)}")
        
        # 1. Создаём LLM
        llm = ChatOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_API_URL,
            model_name=MODEL_NAME,
            temperature=0.1,
        )
        logger.info(f"✓ LLM создана: {MODEL_NAME}")

        # ---------- ПРОМПТЫ ----------
        # Шаг 1: планирование / выбор инструментов
        planning_system_prompt = """
Ты ассистент, у тебя есть инструмент MCP kb_search для поиска по базе знаний
компании АльфаСтрахование-Жизнь.

Сейчас твоя задача:
- решить, нужно ли вызывать инструмент kb_search,
- если нужно — сформировать tool_calls с понятными аргументами,
- НЕ писать финальный ответ пользователю.

Отвечай в формате, понятном для tool calling (tool_calls).
        """

        planning_prompt = ChatPromptTemplate.from_messages(
            [
                ("system", planning_system_prompt),
                ("human", "{input}"),
            ]
        )

        # Шаг 2: финальный ответ (после вызова kb_search)
        system_prompt_str = """
Ты ассистент, использующий инструмент kb_search для поиска информации о продуктах компании АльфаСтрахование-Жизнь.

У тебя уже есть:
- твой предыдущий ответ с планом и вызовами инструментов
- результаты вызова kb_search (сырые данные из базы знаний)

Твоя задача:

1. Кратко поясни, что ты делал (ход рассуждений), но без лишней воды.
2. Выведи 3 самых релевантных ответа из результатов kb_search (если он вызывался).
3. Затем дай чёткий итоговый ответ для пользователя.

Формат ответа (СОБЛЮДАЙ ЗАГОЛОВКИ):

### Ход рассуждений
...

### Результат запроса в базу знаний
...

### Ответ
...
        """

        # ---------- BIND TOOLS ----------

        llm_with_tools = llm.bind_tools(tools)
        logger.info("✓ LLM привязана к MCP-инструментам")

        # Проверяем инструменты
        tool_names = [tool.name for tool in tools if hasattr(tool, "name")]
        result["details"]["available_tools"] = tool_names
        result["details"]["tools_count"] = len(tool_names)
        result["details"]["agent_type"] = "Tool-calling (2-step)"

        logger.info(f"✓ Агент готов к работе с {len(tool_names)} инструментами: {tool_names}")

        # ---------- ТЕСТОВЫЙ ЗАПРОС ----------

        test_query = "На какую минимальную сумму можно купить продукт Fort Knox?"
        logger.info(f"Выполняем тестовый вызов агента с запросом: '{test_query}'")

        try:
            # 1) ШАГ: планирование + tool_calls
            planning_messages = planning_prompt.format_messages(input=test_query)
            planning_ai: AIMessage = await llm_with_tools.ainvoke(planning_messages)

            logger.info("✓ Получен план от LLM (step 1)")
            logger.debug(f"PLANNING MESSAGE: {planning_ai}")

            tool_calls = getattr(planning_ai, "tool_calls", []) or []
            kb_results = []      # сюда сложим сырые результаты инструментов
            tool_messages = []   # сообщения для второго вызова LLM

            # 2) ВЫЗОВ ИНСТРУМЕНТОВ
            for call in tool_calls:
                tool_name = call.get("name")
                tool_args = call.get("args", {})

                logger.info(f"→ Вызов инструмента: {tool_name} с аргументами {tool_args}")

                matching_tool = next((t for t in tools if t.name == tool_name), None)
                if matching_tool is None:
                    logger.warning(f"Инструмент '{tool_name}' не найден среди доступных: {tool_names}")
                    continue

                # MCP tools, как правило, поддерживают ainvoke
                tool_result = await matching_tool.ainvoke(tool_args)

                kb_results.append(
                    {
                        "tool": tool_name,
                        "args": tool_args,
                        "result": tool_result,
                    }
                )

                tool_msg = ToolMessage(
                    name=tool_name,
                    content=str(tool_result),
                    tool_call_id=call.get("id"),  # может быть None, это ок
                )
                tool_messages.append(tool_msg)

            if not tool_calls:
                logger.info("LLM не запросила вызов инструментов (tool_calls пустой)")

            # 3) ШАГ: финальный ответ (рассуждения + выборка + ответ)
            final_messages = [
                SystemMessage(content=system_prompt_str),
                HumanMessage(content=test_query),
                planning_ai,         # что планировала модель
                *tool_messages,      # результаты инструментов
            ]

            final_ai: AIMessage = await llm.ainvoke(final_messages)
            final_text = final_ai.content if isinstance(final_ai.content, str) else str(final_ai.content)

            logger.info(f"✓ Финальный ответ агента получен ({len(final_text)} символов)")
            logger.info(f"\n{'='*70}")
            logger.info("ПОЛНЫЙ ФИНАЛЬНЫЙ ОТВЕТ АГЕНТА:")
            logger.info(f"{'='*70}")
            logger.info(final_text)
            logger.info(f"{'='*70}\n")

            # ---------- Сохранение в result ----------

            result["details"]["agent_test_call"] = {
                "query": test_query,
                "planning_message": str(planning_ai),   # рассуждение / plan + tool_calls
                "tool_calls": kb_results,               # сырые результаты из KB
                "final_response": final_text,           # формат с тремя секциями
                "response_length": len(final_text),
                "status": "PASSED",
            }
            result["status"] = "PASSED"

        except Exception as e:
            logger.error(f"✗ Ошибка при тестовом вызове агента: {e}", exc_info=True)
            result["details"]["agent_test_call"] = {
                "query": test_query,
                "error": str(e),
                "status": "FAILED",
            }

    except Exception as e:
        logger.error(f"✗ Ошибка при создании LangChain агента: {e}", exc_info=True)
        result["details"]["error"] = str(e)
        result["details"]["error_type"] = type(e).__name__
    
    test_results["tests"]["langchain_agent"] = result
    return result

# ============================================================================
# 5. ФУНКЦИОНАЛЬНЫЕ ТЕСТЫ: ЗАПРОСЫ К KB
# ============================================================================

async def test_kb_queries() -> Dict[str, Any]:
    """
    Провести функциональные тесты с типовыми запросами через агента.
    """
    logger.info("\n" + "=" * 70)
    logger.info("5. ФУНКЦИОНАЛЬНЫЕ ТЕСТЫ: ЗАПРОСЫ К KB ЧЕРЕЗ АГЕНТА")
    logger.info("=" * 70)
    
    result = {
        "name": "KB Functional Tests",
        "status": "FAILED",
        "details": {},
        "queries": [],
        "timestamp": datetime.now().isoformat()
    }
    
    test_queries = [
        {
            "name": "Запрос срока продукта Fort Knox",
            "query": "На какой минимальный срок можно купить продукт Fort Knox?",
            "expected_keywords": ["срок", "Fort Knox"]
        },
        {
            "name": "Запрос описания продукта Защищенный капитал",
            "query": "Расскажи о продукте Защищенный капитал",
            "expected_keywords": ["Защищенный капитал"]
        },
        {
            "name": "Запрос с контекстом",
            "query": "Какие условия и ограничения у НСЖ АльфаВыгода?",
            "expected_keywords": ["3 года", "От 30 000 ₽ до 20 000 000 ₽", "АльфаВыгода"]
        }
    ]
    
    client: MultiServerMCPClient | None = None
    
    try:
        mcp_url = f"{MCP_SERVER_URL}{MCP_ENDPOINT}"
        logger.info(f"Создаём MCP клиент для функциональных тестов: {mcp_url}")

        headers = {"Authorization": f"Bearer {MCP_TOKEN}"} if MCP_TOKEN else {}

        # Формируем конфигурацию для сервера
        server_config = {
            "transport": "streamable_http",
            "url": mcp_url,
        }
        if headers:
            server_config["headers"] = headers

        # Создаём MultiServerMCPClient
        client = MultiServerMCPClient({"kb_search": server_config})
        
        # Получаем инструменты
        tools = await client.get_tools()
        logger.info(f"✓ Получено инструментов MCP: {len(tools)}")

        react_tools = wrap_mcp_tools_for_react(tools)
        logger.info(f"✓ Сконфигурировано инструментов для ReAct: {len(react_tools)}")

        
        if not tools:
            logger.warning("⚠ Инструменты не найдены")
            result["status"] = "SKIPPED"
            result["details"]["error"] = "No tools available"
            test_results["tests"]["kb_queries"] = result
            return result
        
        logger.info(f"✓ Получено инструментов: {len(tools)}")
        
        # ---------- LLM + ReAct агент ----------
        llm = ChatOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_API_URL,
            model_name=MODEL_NAME,
            temperature=0.1,
        )
        logger.info(f"✓ LLM создана: {MODEL_NAME}")

        # ReAct-агент, который сам решает, когда вызывать kb_search
        # Требуются импорты:
        # from langchain.agents import create_react_agent, AgentExecutor
        #prompt = hub.pull("hwchase17/react")
        # Создаем явный промпт, чтобы указать агенту использовать 'query' в качестве аргумента
        prompt_template = """Ответь на следующие вопросы как можно лучше. У тебя есть доступ к следующим инструментам:

{tools}

Используй следующий формат:

Question: вопрос, на который ты должен ответить
Thought: ты всегда должен думать, что делать
Action: действие, которое нужно предпринять, должно быть одним из [{tool_names}]
Action Input: входные данные для действия. Если инструмент принимает аргумент 'query', передай его в формате JSON, например: {{"query": "твой запрос"}}
Observation: результат действия
... (этот цикл Thought/Action/Action Input/Observation может повторяться N раз)
Thought: теперь я знаю окончательный ответ
Final Answer: окончательный ответ на исходный вопрос

Начинай!

Question: {input}
Thought:{agent_scratchpad}"""
        prompt = PromptTemplate.from_template(prompt_template)
        agent = create_react_agent(llm, tools, prompt)
        agent_executor = AgentExecutor(
            agent=agent,
            tools=react_tools,
            verbose=True,
            return_intermediate_steps=True,
            handle_parsing_errors=True,  # Включаем обработку ошибок парсинга
            max_iterations=5,  # Ограничиваем количество итераций
            early_stopping_method="generate",  # Генерируем ответ при ошибке
         ).with_config({"run_name": "Agent"}) | RunnablePassthrough.assign(query=lambda x: x["input"])
        logger.info("✓ ReAct-агент сконфигурирован с MCP-инструментами")

        # Выполняем тестовые запросы
        passed_count = 0
        for i, test_query in enumerate(test_queries, 1):
            logger.info(f"\n{'-'*70}")
            logger.info(f"Тест {i}/{len(test_queries)}: {test_query['name']}")
            logger.info(f"Запрос: {test_query['query']}")
            logger.info(f"Ожидаемые ключевые слова: {', '.join(test_query['expected_keywords'])}")
            
            query_result = {
                "test_name": test_query["name"],
                "query": test_query["query"],
                "status": "FAILED",
                "response": None,
                "response_length": 0,
                "keywords_found": [],
                "keywords_expected": test_query["expected_keywords"],
                "elapsed_time": 0,
                "tool_calls_count": 0
            }
            
            try:
                start_time = time.time()
                
                # ОДИН вызов ReAct-агента:
                # - внутри сам решает, вызывать ли kb_search
                # - внутри одного вызова может сделать несколько tool calls
                agent_output = await asyncio.wait_for(
                    agent_executor.ainvoke({"input": test_query["query"]}),
                    timeout=60.0,
                )

                elapsed_time = time.time() - start_time
                query_result["elapsed_time"] = elapsed_time

                # Финальный ответ агента (без рассуждений)
                final_text = agent_output.get("output", "") if isinstance(agent_output, dict) else str(agent_output)
                final_text = final_text or ""
                query_result["response"] = final_text
                query_result["response_length"] = len(final_text)

                # Кол-во вызовов инструментов внутри одного вызова агента
                intermediate_steps = agent_output.get("intermediate_steps", []) if isinstance(agent_output, dict) else []
                query_result["tool_calls_count"] = len(intermediate_steps)

                logger.info(f"  ✓ Финальный ответ получен ({len(final_text)} символов, {elapsed_time:.2f}s)")
                logger.info("  --- Финальный ответ агента ---")
                logger.info(final_text)
                logger.info("  --- Конец ответа ---")

                # Проверяем ключевые слова только по финальному ответу
                response_lower = final_text.lower()
                found_keywords = [
                    kw for kw in test_query["expected_keywords"]
                    if kw.lower() in response_lower
                ]
                query_result["keywords_found"] = found_keywords
                
                # Определяем статус теста
                if not final_text:
                    query_result["status"] = "FAILED"
                    logger.error("  ✗ Пустой ответ от агента")
                elif len(found_keywords) == len(test_query["expected_keywords"]):
                    query_result["status"] = "PASSED"
                    passed_count += 1
                    logger.info(f"  ✓ Тест пройден. Найдены все ключевые слова: {', '.join(found_keywords)}")
                elif len(found_keywords) > 0:
                    query_result["status"] = "PASSED_PARTIAL"
                    logger.warning(
                        f"  ⚠ Тест пройден частично. Найдено {len(found_keywords)}/"
                        f"{len(test_query['expected_keywords'])} ключевых слов: {', '.join(found_keywords)}"
                    )
                else:
                    query_result["status"] = "FAILED"
                    logger.warning("  ✗ Ключевые слова не найдены в ответе")

            except asyncio.TimeoutError:
                query_result["status"] = "TIMEOUT"
                query_result["error"] = "Превышено время ожидания ответа от агента"
                logger.error("  ✗ Тест не пройден: превышено время ожидания")
            except Exception as e:
                query_result["status"] = "ERROR"
                query_result["error"] = str(e)
                logger.error(f"  ✗ Ошибка при выполнении запроса: {e}")
            
            result["queries"].append(query_result)

        # Обновляем общий статус
        total_queries = len(test_queries)
        partial_count = sum(1 for q in result["queries"] if q["status"] == "PASSED_PARTIAL")
        
        if passed_count == total_queries:
            result["status"] = "PASSED"
        elif passed_count + partial_count > 0:
            result["status"] = "PASSED_PARTIAL"
        else:
            result["status"] = "FAILED"
            
        result["details"]["total_queries"] = total_queries
        result["details"]["passed_queries"] = passed_count
        result["details"]["partial_queries"] = partial_count
        result["details"]["failed_queries"] = total_queries - passed_count - partial_count
        result["details"]["success_rate"] = f"{(passed_count/total_queries)*100:.1f}%"
        
        logger.info(f"\n{'='*70}")
        logger.info(f"ИТОГИ ФУНКЦИОНАЛЬНЫХ ТЕСТОВ:")
        logger.info(f"  Всего тестов: {total_queries}")
        logger.info(f"  Пройдено полностью: {passed_count}")
        logger.info(f"  Пройдено частично: {partial_count}")
        logger.info(f"  Не пройдено: {total_queries - passed_count - partial_count}")
        logger.info(f"  Успешность: {result['details']['success_rate']}")
        logger.info(f"{'='*70}")

    except Exception as e:
        result["status"] = "ERROR"
        result["details"]["error"] = str(e)
        result["details"]["error_type"] = type(e).__name__
        logger.error(f"✗ Критическая ошибка в ходе функциональных тестов: {e}", exc_info=True)
    
    test_results["tests"]["kb_queries"] = result
    return result


# ============================================================================
# 6. ОТЧЁТ О РЕЗУЛЬТАТАХ ТЕСТИРОВАНИЯ
# ============================================================================

def generate_test_report() -> str:
    """
    Сформировать краткий отчёт о результатах тестирования.
    
    Returns:
        Форматированный отчёт в виде строки
    """
    logger.info("\n" + "=" * 70)
    logger.info("ИТОГОВЫЙ ОТЧЁТ О ТЕСТИРОВАНИИ")
    logger.info("=" * 70)
    
    report = []
    report.append("\n" + "=" * 70)
    report.append("ОТЧЁТ О ТЕСТИРОВАНИИ MCP KB SEARCH SERVER")
    report.append("=" * 70)
    report.append(f"Время: {test_results['timestamp']}\n")
    
    # Сводка по тестам
    report.append("СВОДКА ПО ТЕСТАМ:")
    report.append("-" * 70)
    
    test_statuses = {}
    for test_name, test_data in test_results["tests"].items():
        status = test_data.get("status", "UNKNOWN")
        test_statuses[status] = test_statuses.get(status, 0) + 1
        
        status_icon = "✓" if status == "PASSED" else "⚠" if status in ["PASSED_PARTIAL", "TIMEOUT"] else "✗"
        report.append(f"{status_icon} {test_data['name']}: {status}")
    
    report.append("\n" + "-" * 70)
    report.append("СТАТИСТИКА:")
    report.append("-" * 70)
    
    for status, count in test_statuses.items():
        report.append(f"  {status}: {count}")
    
    # Детальная информация по каждому тесту
    report.append("\n" + "=" * 70)
    report.append("ДЕТАЛЬНАЯ ИНФОРМАЦИЯ:")
    report.append("=" * 70)
    
    for test_name, test_data in test_results["tests"].items():
        report.append(f"\n{test_data['name']} [{test_data['status']}]")
        report.append("-" * 70)
        
        if test_name == "kb_queries":
            # Специальная обработка для функциональных тестов
            for query in test_data.get("queries", []):
                report.append(f"  Тест: {query['test_name']}")
                report.append(f"    Статус: {query['status']}")
                report.append(f"    Запрос: {query['query']}")
                report.append(f"    Время ответа: {query['elapsed_time']:.2f}s")
                report.append(f"    Длина ответа: {query['response_length']} символов")
                if query.get("keywords_found"):
                    report.append(f"    Найденные ключевые слова: {', '.join(query['keywords_found'])}")
                if query.get("error"):
                    report.append(f"    Ошибка: {query['error']}")
        else:
            # Общая информация по тесту
            details = test_data.get("details", {})
            for key, value in details.items():
                if key != "response" and key != "error":
                    if isinstance(value, (dict, list)):
                        report.append(f"  {key}: {json.dumps(value, ensure_ascii=False, indent=2)[:200]}")
                    else:
                        report.append(f"  {key}: {value}")
                elif key == "error":
                    report.append(f"  ✗ Ошибка: {value}")
    
    # Метрики производительности
    if test_results.get("metrics"):
        report.append("\n" + "=" * 70)
        report.append("МЕТРИКИ ПРОИЗВОДИТЕЛЬНОСТИ:")
        report.append("=" * 70)
        
        for metric_name, metric_value in test_results["metrics"].items():
            report.append(f"  {metric_name}: {metric_value}")
    
    # Рекомендации
    report.append("\n" + "=" * 70)
    report.append("РЕКОМЕНДАЦИИ:")
    report.append("=" * 70)
    
    failed_tests = [t for t, d in test_results["tests"].items() if d.get("status") == "FAILED"]
    
    if not failed_tests:
        report.append("✓ Все тесты пройдены успешно!")
    else:
        report.append(f"⚠ Обнаружены проблемы в следующих тестах:")
        for test in failed_tests:
            report.append(f"  - {test}")
        report.append("\nДля отладки проверьте логи выше.")
    
    report.append("\n" + "=" * 70)
    
    report_text = "\n".join(report)
    logger.info(report_text)
    
    return report_text

# ============================================================================
# MAIN: ЗАПУСК ВСЕХ ТЕСТОВ
# ============================================================================

async def run_all_tests():
    """
    Запустить все тесты последовательно.
    """
    logger.info("\n" + "=" * 70)
    logger.info("ЗАПУСК ПОЛНОГО НАБОРА ТЕСТОВ MCP KB SEARCH SERVER")
    logger.info("=" * 70)
    
    try:
        # 1. Проверка доступности сервера
        await test_server_availability()
        
        # 2. Индексация БЗ
        await test_kb_indexing()
        
        # 3. Подключение MCP клиента
        await test_mcp_client_connection()
        
        # 4. Создание LangChain агента
        await test_langchain_agent()
        
        # 5. Функциональные тесты
        await test_kb_queries()
        
        # 6. Генерация отчёта
        report = generate_test_report()
        
        # Сохраняем отчёт в файл
        report_path = Path("test_report.txt")
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(report)
        
        logger.info(f"\n✓ Отчёт сохранён в файл: {report_path.absolute()}")
        
        # Сохраняем результаты в JSON
        json_path = Path("test_results.json")
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(test_results, f, ensure_ascii=False, indent=2)
        
        logger.info(f"✓ Результаты сохранены в файл: {json_path.absolute()}")
        
        return test_results
        
    except Exception as e:
        logger.error(f"✗ Критическая ошибка при запуске тестов: {e}", exc_info=True)
        raise

# ============================================================================
# ENTRY POINT
# ============================================================================

if __name__ == "__main__":
    try:
        # Запускаем асинхронные тесты
        results = asyncio.run(run_all_tests())
        
        # Выводим финальный статус
        logger.info("\n" + "=" * 70)
        logger.info("ТЕСТИРОВАНИЕ ЗАВЕРШЕНО")
        logger.info("=" * 70)
        
        # Определяем общий статус
        all_statuses = [t.get("status") for t in results["tests"].values()]
        
        if all(s == "PASSED" for s in all_statuses):
            logger.info("✓ ВСЕ ТЕСТЫ ПРОЙДЕНЫ УСПЕШНО")
            exit(0)
        elif any(s == "FAILED" for s in all_statuses):
            logger.warning("✗ НЕКОТОРЫЕ ТЕСТЫ НЕ ПРОЙДЕНЫ")
            exit(1)
        else:
            logger.info("⚠ ТЕСТЫ ЗАВЕРШЕНЫ С ПРЕДУПРЕЖДЕНИЯМИ")
            exit(0)
            
    except KeyboardInterrupt:
        logger.info("\n⚠ Тестирование прервано пользователем")
        exit(130)
    except Exception as e:
        logger.error(f"\n✗ Критическая ошибка: {e}", exc_info=True)
        exit(1)