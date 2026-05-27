# импорт библиотек для взаимодейсвтия с ОС, логирования, асинхронности и типов
import os
import re
import asyncio
import time
import sys
from pathlib import Path
from typing import Annotated, Dict, Optional
from datetime import datetime

# Веб-сервер
import uvicorn
from dotenv import load_dotenv

# Библиотека отвечающая за MCP-сервер
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult

# Библиотка Starlette для реализации REST-API
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse
from starlette.requests import Request
from contextlib import asynccontextmanager
from utils.indexer import Indexer, IndexRuntime, IndexerConfig, metadata_document_count
from utils.logger import setup_logger
from utils.search_profile import search_profile_config

import textwrap

# -------------------------
# КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# -------------------------
# Загрузка переменных окружения
load_dotenv()

# Определение путей
SCRIPT_DIR = Path(__file__).parent.absolute()
# Версия скрипта
try:
    SCRIPT_VERSION = re.findall(r"v\d+", os.path.basename(__file__))[-1].replace("v", "")
except Exception:
    SCRIPT_VERSION = "2.0"

FAQ_SERVICE_DIR = SCRIPT_DIR / "faq_service"
FAQ_SERVICE_DIR.mkdir(exist_ok=True, parents=True)

# конфиг сервера
MCP_FAQPATH = os.getenv("MCP_FAQPATH", "/faq_rag")
API_TOKEN = os.getenv("MCP_TOKEN")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", 7000))

# Конфигурация индексирования
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "REDACTED_EXAMPLE")
OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL_EMB", "https://dsrv1.llm.nstcloud.ru/v1/embeddings")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
SIMILARITY_TOP_K = int(os.getenv("SIMILARITY_TOP_K", 5))
SIMILARITY_CUTOFF = float(os.getenv("SIMILARITY_CUTOFF", 0.0))
# Qdrant settings
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "faq_collection")
USE_QDRANT = os.getenv("USE_QDRANT", "false").lower() == "true"
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_ALIAS = os.getenv("QDRANT_ALIAS", "faq_collection_active")
# Повторное подключение к Qdrant при недоступности на старте
FAQ_QDRANT_RETRY_INTERVAL = float(os.getenv("FAQ_QDRANT_RETRY_INTERVAL", "10"))
FAQ_QDRANT_INIT_TIMEOUT = float(os.getenv("FAQ_QDRANT_INIT_TIMEOUT", "600"))
logger = setup_logger("faq_rag_server", service_dir=FAQ_SERVICE_DIR)
idx_config = IndexerConfig(
    service_dir=FAQ_SERVICE_DIR,
    embed_api_url=OPENROUTER_API_URL,
    embed_api_key=OPENROUTER_API_KEY,
    embed_model_name=EMBEDDING_MODEL,
    similarity_top_k=SIMILARITY_TOP_K,
    similarity_cutoff=SIMILARITY_CUTOFF,
    use_qdrant=USE_QDRANT,
    qdrant_host=QDRANT_HOST,
    qdrant_port=QDRANT_PORT,
    qdrant_collection=QDRANT_COLLECTION,
    qdrant_alias=QDRANT_ALIAS,
    logger_name="faq_indexer",
)
# Создаём Indexer с текущими конфигами
indexer = Indexer(idx_config)


def _execute_faq_profile_search(
    question: str,
    collection: Optional[str],
    filters: dict | None,
    top_k: int,
) -> list:
    profile_cfg = search_profile_config("faq_search")
    fetch_k = int(top_k * 1.5)
    if profile_cfg.search_mode == "dense":
        return indexer.hybrid_dense_search(
            question,
            collection,
            filters,
            fetch_k,
            search_profile="faq_search",
        )
    return indexer.hybrid_search_rrf(
        question,
        collection,
        filters,
        fetch_k,
        search_profile="faq_search",
    )
# -------------------------
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ + блокировка | Состояние сервера
# -------------------------
faq_lock = asyncio.Lock()
faq_runtime = IndexRuntime()
qdrant_init_task: asyncio.Task | None = None

# -------------------------
# СТАТУС FAQ
# -------------------------
def get_faq_status() -> Dict:
    """Получить текущий статус FAQ."""
    metadata = indexer.get_active_metadata() if faq_runtime.initialized else {}
    # documents_count соответствует количеству вопросов которые в индексе
    return {
        "initialized": faq_runtime.initialized,
        "index_exists": bool(faq_runtime.initialized),
        "documents_count": metadata_document_count(metadata),
        "metadata": metadata,
        "last_update": faq_runtime.last_update,
        "qdrant_init": {
            "use_qdrant": USE_QDRANT,
            "waiting": USE_QDRANT and not faq_runtime.initialized,
            "retry_in_progress": bool(
                qdrant_init_task and not qdrant_init_task.done()
            ),
            "retry_interval_sec": FAQ_QDRANT_RETRY_INTERVAL,
            "init_timeout_sec": FAQ_QDRANT_INIT_TIMEOUT,
            "qdrant_reachable": indexer.is_qdrant_reachable() if USE_QDRANT else None,
        },
    }

# --------- 
# Logic of loading and using index 
# --------
async def load_state():
    """
    Основная функция загрузки мозгов сервера
    возвращает Retriever и Map
    """
    logger.info("Загрузка состояния индекса:")
    async with faq_lock:
        loop = asyncio.get_running_loop()
        #  загружаем индекс из нашего класса indexer 
        success = await loop.run_in_executor(None, indexer.reload_runtime)
        if success:    
            faq_runtime.initialized = True
            faq_runtime.last_update = datetime.now().isoformat()
            logger.info("Индекс загружен и готов к работе")
            return True
        else:
            logger.warning("Индекс не найден или пуст")
            faq_runtime.initialized = False
            return False

# -------------------------
# ИНИЦИАЛИЗАЦИЯ ПРИ ЗАПУСКЕ
# -------------------------
async def qdrant_init_retry_loop() -> None:
    """
    Фоновые попытки загрузить FAQ из Qdrant, пока алиас и коллекция не станут доступны.
    По истечении FAQ_QDRANT_INIT_TIMEOUT завершает процесс (restart: unless-stopped / K8s).
    """
    if not USE_QDRANT or faq_runtime.initialized:
        return

    start = time.monotonic()
    attempt = 0
    logger.info(
        f"Фоновое подключение к Qdrant: интервал {FAQ_QDRANT_RETRY_INTERVAL}s, "
        f"таймаут {FAQ_QDRANT_INIT_TIMEOUT}s (0 = без выхода)"
    )

    while not faq_runtime.initialized:
        attempt += 1
        elapsed = time.monotonic() - start

        if FAQ_QDRANT_INIT_TIMEOUT > 0 and elapsed >= FAQ_QDRANT_INIT_TIMEOUT:
            logger.error(
                f"Таймаут загрузки FAQ из Qdrant ({FAQ_QDRANT_INIT_TIMEOUT}s, "
                f"попыток: {attempt}). Завершение процесса для перезапуска."
            )
            sys.exit(1)

        if attempt > 1:
            logger.info(
                f"Повторная загрузка FAQ из Qdrant (попытка {attempt}, "
                f"прошло {elapsed:.0f}s)..."
            )

        if await load_state():
            logger.info(
                f"✓ FAQ загружена из Qdrant после {attempt} попыток ({elapsed:.0f}s)"
            )
            return

        await asyncio.sleep(FAQ_QDRANT_RETRY_INTERVAL)


async def initialize_faq_on_startup() -> None:
    """Загрузка FAQ из Qdrant (индексация выполняется kb-manager)."""
    logger.info("=" * 70)
    logger.info("ИНИЦИАЛИЗАЦИЯ FAQ RAG ПРИ СТАРТЕ")
    logger.info("=" * 70)
    try:
        if await load_state():
            logger.info("✓ FAQ инициализирована из Qdrant")
            return

        if USE_QDRANT:
            logger.warning(
                "Qdrant/FAQ недоступны при старте — поиск включится после успешной "
                f"загрузки (повтор каждые {FAQ_QDRANT_RETRY_INTERVAL}s)"
            )
            return

        logger.warning(
            "USE_QDRANT=false: локальная индексация в MCP отключена. "
            "Используйте kb-manager для наполнения Qdrant."
        )
    
    except Exception as e:
        logger.error(f"Критическая ошибка при инициализации FAQ: {e}", exc_info=True)

# -------------------------
# MCP SERVER - FAQ RAG TOOL
# -------------------------

# инициализация FastMCP
mcp = FastMCP("faq_rag")

@mcp.tool()
async def faq_search(
    query: Annotated[
        str,
        "User question. REQUIRED. You MUST call this tool before answering."
    ],
    collection: Annotated[
        Optional[str],
        "Qdrant collection name. If not provided, the active collection alias is used."
    ] = None,
    filters: Annotated[
        dict | None,
        """
        Optional metadata filters.

        Example:
        {
          "kb_id": "default_faq",
          "category": "HR",
          "section_path": "vacations"
        }
        """
    ] = None
):
    """
    Search the official FAQ knowledge base for relevant question–answer pairs.

    You MUST call this tool when the user asks a question.

    The tool performs semantic search over the FAQ database and returns the
    most relevant entries as markdown context.

    The returned data is NOT the final answer. It is reference information that
    must be used to compose the response to the user.

    Args:
        query: User question.
        collection: Optional Qdrant collection name.
        filters: Optional metadata filters.

    Returns:
        ToolResult where `content` contains markdown with:

        - the user question
        - several relevant FAQ entries
        - official questions and answers
        - category and section metadata

    Use the returned FAQ answers as trusted information when generating
    the final response.
    """
    top_k = SIMILARITY_TOP_K
    MIN_SCORE = 0.35
    question = query
    profile_cfg = search_profile_config("faq_search")
    if profile_cfg.search_mode=="hybrid":
        logger.info(
            f"FAQ поиск: '{question}' (top_k={top_k}, search_mode={profile_cfg.search_mode}, "
            f"rrf_k={profile_cfg.rrf_k}, candidate_mult={profile_cfg.candidate_mult}), "
            f"collection={collection}, filters={filters}"
        )
    elif profile_cfg.search_mode=="dense":
        logger.info(
            f"FAQ поиск: '{question}' (top_k={top_k}, search_mode={profile_cfg.search_mode}, "
            f"collection={collection}, filters={filters}"
        )
    else:
        logger.error(f"Неправильный search_mode: {profile_cfg.search_mode}"
        res = ToolResult(
                content=f"Ошибка при поиске FAQ: Неправильный search_mode - {profile_cfg.search_mode}",
                structured_content=None
            )
        res.isError = True
        return res
    
    async with faq_lock:

        if not indexer.cfg.use_qdrant and not faq_runtime.initialized:
            return ToolResult(
                content="# Результат поиска FAQ\n\nБаза FAQ временно недоступна.",
                structured_content=None
            )

        try:
            if not indexer.cfg.use_qdrant:
                return ToolResult(
                    content="USE_QDRANT=false: поиск недоступен. Включите Qdrant и kb-manager.",
                    structured_content=None,
                )

            if not indexer.hybrid_search_enabled(collection):
                res = ToolResult(
                    content=(
                        "FAQ-коллекция не в hybrid-формате (dense+sparse). "
                        "Переиндексируйте через kb-manager."
                    ),
                    structured_content=None,
                )
                res.isError = True
                return res

            loop = asyncio.get_running_loop()
            rows = await loop.run_in_executor(
                None,
                lambda: _execute_faq_profile_search(
                    question, collection, filters, top_k
                ),
            )

            if not rows:
                return ToolResult(
                    content=textwrap.dedent(f"""
                    # Результат поиска FAQ

                    **Вопрос пользователя:** {question}

                    В официальной базе FAQ не найдено релевантных записей.
                    """).strip(),
                    structured_content=None
                )

            candidates = []

            for row in rows:
                score = row.get("score", 0.0)
                if score < MIN_SCORE:
                    continue

                payload = row.get("metadata") or {}
                answer = payload.get("answer")
                faq_question = payload.get("question") or row.get("text", "")

                category = payload.get("category", "Общее")
                section = payload.get("section_path") or "Общее"

                if not answer:
                    continue

                candidates.append({
                    "relevance_score": round(score, 3),
                    "official_question": str(faq_question).strip(),
                    "official_answer": str(answer).strip(),
                    "category": str(category).strip(),
                    "section": str(section).strip(),
                })

            candidates = sorted(
                candidates,
                key=lambda x: x["relevance_score"],
                reverse=True
            )
            candidates = [c for c in candidates if c["relevance_score"] >= MIN_SCORE]

            if not candidates:
                return ToolResult(
                    content="# Результат поиска FAQ\n\nНет достаточно релевантных результатов.",
                    structured_content=None
                )

            parts = []
            parts.append("# Результат поиска FAQ\n")
            parts.append(f"**Вопрос пользователя:** {question}\n")
            parts.append(f"Найдено **{len(candidates)}** релевантных FAQ.\n")

            for i, item in enumerate(candidates, start=1):

                parts.append(f"## Результат {i}")
                parts.append(f"**Раздел:** {item['section']}")
                parts.append(f"**Вопрос:** {item['official_question']}")
                parts.append(f"**Ответ:** {item['official_answer']}\n")
            logger.debug(f"\nРезультаты поиска:\n{parts}")
            markdown = "\n".join(parts)

            return ToolResult(
                content=markdown,
                structured_content=None
            )

        except Exception as e:

            logger.error(f"Ошибка при FAQ поиске: {e}", exc_info=True)
            res = ToolResult(
                content=f"Ошибка при поиске FAQ: {e}",
                structured_content=None
            )
            res.isError = True
            return res


@mcp.tool()
async def get_faq_info() -> Dict:
    """
    Получить информацию о статусе FAQ.
    """
    logger.debug("Запрос информации о FAQ")
    return get_faq_status()

# -------------------------
# REST API ENDPOINTS
# -------------------------
async def faq_status_handler(request: Request) -> JSONResponse:
    """Endpoint для получения статуса FAQ."""
    try:
        logger.debug("Запрос статуса FAQ")
        return JSONResponse({"success": True, "status": get_faq_status()})
    except Exception as e:
        logger.error(f"Ошибка в faq_status_handler: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def faq_documents_search(request: Request) -> JSONResponse:
    """Фильтрация документов в Qdrant (не семантический поиск)."""
    payload = await request.json()
    result = await asyncio.get_running_loop().run_in_executor(
        None, indexer.filter_documents, payload
    )
    items = (result or {}).get("items") or []
    return JSONResponse({"success": True, "count": len(items), "items": items})

# -------------------------
# Starlette app + auth_guard + lifespan
# -------------------------

# Создаем Starlette приложение с MCP и REST endpoints
mcp_app = mcp.http_app()

async def auth_guard(scope, receive, send):
    """
    ASGI-обёртка над MCP-приложением с проверкой Bearer токена.
    Работает и для HTTP, и для WebSocket (если появится).
    """
    if scope["type"] == "http":
        # Для HTTP-запросов используем объект Request для удобства
        request = Request(scope, receive)
        auth_header = request.headers.get("Authorization", "")

        if API_TOKEN and not auth_header.startswith(f"Bearer {API_TOKEN}"):
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
        
    # Если аутентификация не требуется или пройдена, передаем управление
    # существующему приложению mcp_app
    await mcp_app(scope, receive, send)

lifespan_original = mcp_app.router.lifespan_context

@asynccontextmanager
async def lifespan(app):
    global qdrant_init_task
    logger.info("=" * 70)
    logger.info("ИНИЦИАЛИЗАЦИЯ СЕРВЕРА FAQ-RAG")
    logger.info("=" * 70)
    # КРИТИЧНО - запускаем StreamableHTTPSessionManager
    async with lifespan_original(app):
        await initialize_faq_on_startup()

        if USE_QDRANT and not faq_runtime.initialized:
            qdrant_init_task = asyncio.create_task(qdrant_init_retry_loop())

        logger.info("=" * 70)
        logger.info("✓ Сервер готов")
        if USE_QDRANT and not faq_runtime.initialized:
            logger.info(
                f"Ожидание Qdrant: retry={FAQ_QDRANT_RETRY_INTERVAL}s, "
                f"timeout={FAQ_QDRANT_INIT_TIMEOUT}s"
            )
        logger.info("=" * 70)
        yield # Тут сервер живёт

        if qdrant_init_task and not qdrant_init_task.done():
            qdrant_init_task.cancel()
            try:
                await qdrant_init_task
            except asyncio.CancelledError:
                pass
        qdrant_init_task = None

    logger.info("Завершение работы сервера...")

app = Starlette(
    routes=[
        Mount(MCP_FAQPATH, app=auth_guard),
        Route("/faq/status", faq_status_handler, methods=["GET"]),
        Route("/faq/documents/search", faq_documents_search, methods=["POST"]),
    ],
    lifespan=lifespan,
)

if __name__ == '__main__':
    logger.info(f"Запуск MCP FAQ-RAG Server v{SCRIPT_VERSION}")
    logger.info(f"Хост: {MCP_HOST}:{MCP_PORT}")
    logger.info(f"MCP base path: {MCP_FAQPATH} (endpoint будет {MCP_FAQPATH}/mcp)")
    logger.info(f"Эмбеддинг модель: {EMBEDDING_MODEL}")
    logger.info(f"similarity_top_k: {SIMILARITY_TOP_K}, cutoff: {SIMILARITY_CUTOFF}")

    config = uvicorn.Config(
        app=app,
        host=MCP_HOST,
        port=MCP_PORT,
        log_level=os.getenv("LOG_LEVEL", "info").lower(),
        access_log=True,
    )
    server = uvicorn.Server(config)
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        logger.info("Сервер остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise
    
