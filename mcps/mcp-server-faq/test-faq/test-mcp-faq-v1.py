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
MCP_SERVER_URL = os.getenv("MCP_FAQ_URL", "http://localhost:7000")
MCP_ENDPOINT = os.getenv("MCP_FAQPATH_ENDPOINT", "/faq_rag/mcp")
MCP_TOKEN = os.getenv("MCP_TOKEN", "REDACTED_EXAMPLE-here")

# docker автоматом дописывает путь до local_faq сейчас, поэтому можно просто пусто передавать,
#  иначе если надо что/то из под папки добавить то её передавать
FAQ_SOURCE_PATH = Path("")
# FAQ_SOURCE_PATH_SUBDIR = Path("/test")

# OpenAI/OpenRouter конфиг
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "REDACTED_EXAMPLE")
OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL", "https://api.llm.nstcloud.ru/v1")
MODEL_NAME = os.getenv("OPENROUTER_API_MODEL", "Qwen/Qwen3-30B-A3B")
INDEX_ANSWERS = os.getenv("INDEX_ANSWERS", "false").lower() == "true"

# Параметры для загрузки из S3
S3_ENDPOINT = os.getenv("S3_ENDPOINT", "https://storage.yandexcloud.net")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "REDACTED_EXAMPLE")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "REDACTED_EXAMPLE")
S3_BUCKET = os.getenv("S3_BUCKET", "sandbox-2-k8s-mcp-b1ga7h8ijbukqu3mljmu")
S3_PREFIX = os.getenv("S3_PREFIX", "mcp_inputs/faq")


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
        response = requests.get(f"{MCP_SERVER_URL}/faq/status", timeout=5)
        
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
# 2. ИНДЕКСАЦИЯ БЗ: ЗАГРУЗКА И ИНДЕКСИРОВАНИЕ ДОКУМЕНТОВ ЧИСТО ИЗ ПАПКИ
# ============================================================================

async def test_faq_indexing() -> Dict[str, Any]:
    """Проверка готовности индекса через /faq/status (индексация — kb-manager)."""
    logger.info("\n" + "=" * 70)
    logger.info("2. СТАТУС FAQ (индексация через kb-manager, не MCP)")
    logger.info("=" * 70)

    result = {
        "name": "FAQ index status",
        "status": "FAILED",
        "details": {},
        "timestamp": datetime.now().isoformat(),
    }

    try:
        response = requests.get(f"{MCP_SERVER_URL}/faq/status", timeout=30)
        result["details"]["http_status"] = response.status_code
        if response.status_code != 200:
            result["details"]["error"] = response.text[:500]
            test_results["tests"]["faq_indexing"] = result
            return result

        payload = response.json()
        status = payload.get("status", {})
        result["details"]["faq_status"] = status
        if status.get("initialized"):
            result["status"] = "PASSED"
            logger.info("✓ FAQ MCP загрузил индекс из Qdrant")
        else:
            result["status"] = "SKIPPED"
            result["details"]["note"] = "FAQ MCP ожидает данные в Qdrant (kb-manager)"
            logger.warning("⚠ FAQ MCP ещё не инициализирован — проверьте kb-manager/Qdrant")
    except Exception as e:
        result["details"]["error"] = str(e)
        logger.error(f"✗ Ошибка проверки статуса FAQ: {e}")

    test_results["tests"]["faq_indexing"] = result
    return result

# ============================================================================
# 3. ИНДЕКСАЦИЯ БЗ S3: ЗАГРУЗКА И ИНДЕКСИРОВАНИЕ ДОКУМЕНТОВ ИЗ S3
# ===========================================================================
async def test_faq_indexing_s3() -> Dict[str, Any]:
    """Legacy placeholder: MCP больше не индексирует из S3."""
    result = {
        "name": "FAQ Indexing from S3 (removed)",
        "status": "SKIPPED",
        "details": {"note": "Endpoint /faq/update удалён; используйте kb-manager"},
        "timestamp": datetime.now().isoformat(),
    }
    test_results["tests"]["faq_indexing_s3"] = result
    return result
# ============================================================================
# 4. MCP КЛИЕНТ: ПОДКЛЮЧЕНИЕ И ПОЛУЧЕНИЕ ИНСТРУМЕНТОВ
# ============================================================================
async def test_mcp_client_connection() -> Dict[str, Any]:
    """
    Создать MCP-клиент и получить список инструментов (через langchain-mcp-adapters).
    """
    logger.info("\n" + "=" * 70)
    logger.info("4. MCP КЛИЕНТ: ПОДКЛЮЧЕНИЕ И ПОЛУЧЕНИЕ ИНСТРУМЕНТОВ")
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
        port = parsed_url.port or 8000
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
                "faq_search": server_config
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

        # Проверяем наличие инструмента faq_search
        tool_names = [t["name"] for t in tools_info]

        if "faq_search" in tool_names:
            logger.info("✓ Инструмент 'faq_search' найден")
            result["details"]["faq_search_found"] = True
        else:
            logger.warning("⚠ Инструмент 'faq_search' не найден")
            logger.warning(f"  Доступные инструменты: {tool_names}")
            result["details"]["faq_search_found"] = False
            result["status"] = "PASSED_PARTIAL"

    except Exception as e:
        logger.error(f"✗ Неожиданная ошибка при работе MCP клиента: {e}", exc_info=True)
        result["details"]["error"] = str(e)
        result["details"]["error_type"] = type(e).__name__

    # MultiServerMCPClient стейтлесс — явного disconnect не требуется
    test_results["tests"]["mcp_client"] = result
    return result

# ============================================================================
# 5. LANGCHAIN AGENT: СОЗДАНИЕ И ПРОВЕРКА ИНСТРУМЕНТОВ
# ============================================================================

async def test_langchain_agent() -> Dict[str, Any]:
    """
    Создать LangChain агента с инструментом из MCP-server.
    """
    logger.info("\n" + "=" * 70)
    logger.info("5. LANGCHAIN AGENT: СОЗДАНИЕ И ПРОВЕРКА ИНСТРУМЕНТОВ")
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
        client = MultiServerMCPClient({"faq_search": server_config})
        
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
Ты ассистент, у тебя есть инструмент MCP faq_search для поиска по часто задаваемым вопросам (FAQ)
Сейчас твоя задача:
- решить, нужно ли вызывать инструмент faq_search
- если нужно вызывать его с collection=faq_collection и filters.kb_id=workers_inside 
Корректный Вызов инструмента faq_search:
{{
  "query": "...",
  "collection": "faq_collection",
  "filters": {{
    "kb_id": "workers_inside"
  }}
}}
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

        # Шаг 2: финальный ответ (после вызова faq_search)
        system_prompt_str = """
Ты ассистент, использующий инструмент faq_search для поиска информацию для сотрудников в базе знаний FAQ.

У тебя уже есть:
- твой предыдущий ответ с планом и вызовами инструментов
- результаты вызова faq_search (сырые данные из базы знаний)

Твоя задача:

1. Кратко поясни, что ты делал (ход рассуждений), но без лишней воды.
2. Выведи 3 самых релевантных ответа из результатов faq_search (если он вызывался).
3. Затем дай чёткий итоговый ответ для пользователя.

Формат ответа (СОБЛЮДАЙ ЗАГОЛОВКИ):

### Ход рассуждений
...

### Результат запроса вопросно-ответную базу
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

        test_query = "Можно ли взять отпуск без сохранения зарплаты во время  испытательного срока?"
        logger.info(f"Выполняем тестовый вызов агента с запросом: '{test_query}'")

        try:
            # 1) ШАГ: планирование + tool_calls
            planning_messages = planning_prompt.format_messages(input=test_query)
            planning_ai: AIMessage = await llm_with_tools.ainvoke(planning_messages)

            logger.info("✓ Получен план от LLM (step 1)")
            logger.debug(f"PLANNING MESSAGE: {planning_ai}")

            tool_calls = getattr(planning_ai, "tool_calls", []) or []
            faq_results = []      # сюда сложим сырые результаты инструментов
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

                faq_results.append(
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
                "tool_calls": faq_results,               # сырые результаты из faq
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
# 6. ФУНКЦИОНАЛЬНЫЕ ТЕСТЫ: ЗАПРОСЫ К FAQ
# ============================================================================

async def test_faq_queries() -> Dict[str, Any]:
    """
    Провести функциональные тесты с типовыми запросами через агента.
    """
    logger.info("\n" + "=" * 70)
    logger.info("6. ФУНКЦИОНАЛЬНЫЕ ТЕСТЫ: ЗАПРОСЫ К FAQ ЧЕРЕЗ АГЕНТА")
    logger.info("=" * 70)
    
    result = {
        "name": "FAQ Functional Tests",
        "status": "FAILED",
        "details": {},
        "queries": [],
        "timestamp": datetime.now().isoformat()
    }
    
    test_queries = [
        {
            "name": "Увольнение",
            "query": "Кому сдавать технику?",
            "expected_keywords": ["304", "3 этаж", "техника"]
        },
        {
            "name": "Выплаты",
            "query": "Что необходимо предоставить для оформления единовременной выплаты при рождении ребенка?",
            "expected_keywords": ["выплата", "ребенка", "оформление"]
        },
        {
            "name": "Свободной форме вопрос Воинский Учет",
            "query": "как встать на воинский учет и зачем вставать?",
            "expected_keywords": ["регистрации", "проживания", "военкомат", "воинский учет"]
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
        client = MultiServerMCPClient({"faq_search": server_config})
        
        # Получаем инструменты
        tools = await client.get_tools()
        logger.info(f"✓ Получено инструментов MCP: {len(tools)}")

        react_tools = wrap_mcp_tools_for_react(tools)
        logger.info(f"✓ Сконфигурировано инструментов для ReAct: {len(react_tools)}")

        
        if not tools:
            logger.warning("⚠ Инструменты не найдены")
            result["status"] = "SKIPPED"
            result["details"]["error"] = "No tools available"
            test_results["tests"]["faq_queries"] = result
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

        # ReAct-агент, который сам решает, когда вызывать faqsearch
        # Создаем явный промпт, чтобы указать агенту использовать 'query' в качестве аргумента
        prompt_template = """Ответь на следующие вопросы как можно лучше. У тебя есть доступ к следующим инструментам:

{tools}

Используй следующий формат:

Question: вопрос, на который ты должен ответить
Thought: ты всегда должен думать, что делать
Action: действие, которое нужно предпринять, должно быть одним из [{tool_names}]
Action Input: входные данные для действия. Передавай аргументы СТРОГО в формате JSON.
- нужно вызывать его с collection=faq_collection и filters.kb_id=workers_inside 
Корректный Вызов инструмента faq_search:
{{
  "query": "...",
  "collection": "faq_collection",
  "filters": {{
    "kb_id": "workers_inside"
  }}
}}

Observation: результат действия
... (этот цикл Thought/Action/Action Input/Observation может повторяться N раз)
Thought: теперь я знаю окончательный ответ
Final Answer: окончательный ответ на исходный вопрос, должен строится на основе faq_search и ответа по вопросу, задача находить наиболее схожий вопрос, и брать контекст из ему сопоставимого ответа

Начинай!

Question: {input}
Thought:{agent_scratchpad}"""

# Аргументы faq_search:
# - query: string
# - collection: string
# - filters: object (опционально), например:
#   {{ "kb_id": "workers_inside", "category": "...", "section_path": "..." }}
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
                # - внутри сам решает, вызывать ли faqsearch
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
    
    test_results["tests"]["faq_queries"] = result
    return result

# ============================================================================
# 7. ЗАГРУЗОЧНЫЕ ТЕСТЫ: ЗАГРУЗКА НОВОЙ ИНФОРМАЦИИ В DOCKER И В ИНДЕКС
# ============================================================================


# ============================================================================
# 8. ОТЧЁТ О РЕЗУЛЬТАТАХ ТЕСТИРОВАНИЯ
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
    report.append("ОТЧЁТ О ТЕСТИРОВАНИИ MCP FAQ SEARCH SERVER")
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
        
        if test_name == "faq_queries":
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
    logger.info("ЗАПУСК ПОЛНОГО НАБОРА ТЕСТОВ MCP FAQ SEARCH SERVER")
    logger.info("=" * 70)
    
    try:
        # 1. Проверка доступности сервера
        await test_server_availability()
        
        # 2. Индексация БЗ
        # await test_faq_indexing()
        
        # 3. Индексация из S3
        # await test_faq_indexing_s3()
        
        # 4. Подключение MCP клиента
        await test_mcp_client_connection()
        
        # 5. Создание LangChain агента
        await test_langchain_agent()
        
        # 6. Функциональные тесты
        await test_faq_queries()
        
        # 7. Генерация отчёта
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
