# Основные
import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Annotated, Dict, List

import uvicorn
from dotenv import load_dotenv
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from utils.indexer import Indexer, IndexRuntime, IndexerConfig, metadata_document_count
from utils.logger import setup_logger
from utils.search_profile import normalize_search_profile, search_profile_config

# ============================================================================
# КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# ============================================================================

# Загрузка переменных окружения
load_dotenv()

# Определение путей
SCRIPT_DIR = Path(__file__).parent.absolute()

# Версия скрипта из имени файла
SCRIPT_VERSION = re.findall(r"v\d+", os.path.basename(__file__))[-1].replace("v", "")

KB_SERVICE_DIR = SCRIPT_DIR / "kb_service"
KB_SERVICE_DIR.mkdir(exist_ok=True, parents=True)

# Конфигурация сервера
MCP_KBSEARCH = os.getenv("MCP_KBSEARCH", "/kbsearch")
API_TOKEN = os.getenv("MCP_TOKEN")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", 7001))

# Конфигурация индексирования
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "REDACTED_EXAMPLE")
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "https://api.llm.nstcloud.ru/v1/embeddings")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", 512))
CHUNK_OVERLAP = int(os.getenv("KB_CHUNK_OVERLAP", 50))
SIMILARITY_TOP_K = int(os.getenv("KB_SIMILARITY_TOP_K", 10))
SIMILARITY_CUTOFF = float(os.getenv("KB_SIMILARITY_CUTOFF", 0.35))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "kb_collection")
USE_QDRANT = os.getenv("USE_QDRANT", "false").lower() == "true"
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
QDRANT_ALIAS = os.getenv("QDRANT_ALIAS", "kb_collection_active")
# Повторное подключение к Qdrant при недоступности на старте
KB_QDRANT_RETRY_INTERVAL = float(os.getenv("KB_QDRANT_RETRY_INTERVAL", "10"))
KB_QDRANT_INIT_TIMEOUT = float(os.getenv("KB_QDRANT_INIT_TIMEOUT", "600"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
logger = setup_logger("kbsearch_server", service_dir=KB_SERVICE_DIR, log_level=LOG_LEVEL)
logger.info("Logging level: %s", LOG_LEVEL)

idx_config = IndexerConfig(
    service_dir=KB_SERVICE_DIR,
    embed_api_url=EMBEDDING_API_URL,
    embed_api_key=EMBEDDING_API_KEY,
    embed_model_name=EMBEDDING_MODEL,
    similarity_top_k=SIMILARITY_TOP_K,
    similarity_cutoff=SIMILARITY_CUTOFF,
    use_qdrant=USE_QDRANT,
    qdrant_host=QDRANT_HOST,
    qdrant_port=QDRANT_PORT,
    qdrant_collection=QDRANT_COLLECTION,
    qdrant_alias=QDRANT_ALIAS,
    qdrant_api_key=QDRANT_API_KEY,
    logger_name="kbsearch_server",
)
indexer = Indexer(idx_config)
# ============================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ, добавили блокировку
# ============================================================================
kb_lock = asyncio.Lock()
kb_runtime = IndexRuntime()
qdrant_init_task: asyncio.Task | None = None


def get_kb_status() -> Dict:
    """Получить текущий статус KB."""
    metadata = indexer.get_active_metadata() if kb_runtime.initialized else {}
    return {
        "initialized": kb_runtime.initialized,
        "index_exists": bool(kb_runtime.initialized),
        "points_count": metadata.get("points_count", 0),
        "document_count": metadata_document_count(metadata),
        "metadata": metadata,
        "last_update": kb_runtime.last_update,
        "qdrant_init": {
            "use_qdrant": USE_QDRANT,
            "waiting": USE_QDRANT and not kb_runtime.initialized,
            "retry_in_progress": bool(
                qdrant_init_task and not qdrant_init_task.done()
            ),
            "retry_interval_sec": KB_QDRANT_RETRY_INTERVAL,
            "init_timeout_sec": KB_QDRANT_INIT_TIMEOUT,
            "qdrant_reachable": indexer.is_qdrant_reachable() if USE_QDRANT else None,
        },
    }

# ============================================================================
# ОСНОВНЫЕ ФУНКЦИИ ИНДЕКСИРОВАНИЯ
# ============================================================================
async def load_state():
    """
    Основная функция загрузки мозгов сервера
    возвращает Retriever
    """
    logger.info("Загрузка состояния индекса:")
    async with kb_lock:
        loop = asyncio.get_running_loop()
        #  загружаем индекс из нашего класса indexer 
        success = await loop.run_in_executor(None, indexer.reload_runtime)
        if success:
            # создаем индекс как ретривер
            kb_runtime.initialized = True
            kb_runtime.last_update = datetime.now().isoformat()
            logger.info("Индекс загружен и готов к работе")
            return True
        else:
            logger.warning("Индекс не найден или пуст")
            kb_runtime.initialized = False
            return False

# ============================================================================
# ИНИЦИАЛИЗАЦИЯ KB ПРИ ЗАПУСКЕ
# ============================================================================

async def qdrant_init_retry_loop() -> None:
    """
    Фоновые попытки загрузить KB из Qdrant, пока алиас и коллекция не станут доступны.
    По истечении KB_QDRANT_INIT_TIMEOUT завершает процесс (restart: unless-stopped / K8s).
    """
    if not USE_QDRANT or kb_runtime.initialized:
        return

    start = time.monotonic()
    attempt = 0
    logger.info(
        f"Фоновое подключение к Qdrant: интервал {KB_QDRANT_RETRY_INTERVAL}s, "
        f"таймаут {KB_QDRANT_INIT_TIMEOUT}s (0 = без выхода)"
    )

    while not kb_runtime.initialized:
        attempt += 1
        elapsed = time.monotonic() - start

        if KB_QDRANT_INIT_TIMEOUT > 0 and elapsed >= KB_QDRANT_INIT_TIMEOUT:
            logger.error(
                f"Таймаут загрузки KB из Qdrant ({KB_QDRANT_INIT_TIMEOUT}s, "
                f"попыток: {attempt}). Завершение процесса для перезапуска."
            )
            sys.exit(1)

        if attempt > 1:
            logger.info(
                f"Повторная загрузка KB из Qdrant (попытка {attempt}, "
                f"прошло {elapsed:.0f}s)..."
            )

        if await load_state():
            logger.info(
                f"✓ KB загружена из Qdrant после {attempt} попыток ({elapsed:.0f}s)"
            )
            return

        await asyncio.sleep(KB_QDRANT_RETRY_INTERVAL)


async def initialize_kb_on_startup() -> None:
    """Загрузка KB из Qdrant (индексация выполняется kb-manager)."""
    logger.info("=" * 70)
    logger.info("ИНИЦИАЛИЗАЦИЯ БАЗЫ ЗНАНИЙ")
    logger.info("=" * 70)

    try:
        if await load_state():
            logger.info("✓ KB инициализирована из Qdrant")
            return

        if USE_QDRANT:
            logger.warning(
                "Qdrant/KB недоступны при старте — поиск включится после успешной "
                f"загрузки (повтор каждые {KB_QDRANT_RETRY_INTERVAL}s)"
            )
            return

        logger.warning(
            "USE_QDRANT=false: локальная индексация в MCP отключена. "
            "Используйте kb-manager для наполнения Qdrant."
        )

    except Exception as e:
        logger.error(f"Критическая ошибка при инициализации KB: {e}", exc_info=True)

def get_file_link(file_name: Annotated[str, "Name of source file"], section_list: Annotated[List[str], "List of upper folders' name up to storage root folder"]) -> str:
    """
    Generates a direct download link for a file.
    Using in kb_search function to form path relative to storage root and add it to response
    """
    file_path = "/".join(section_list) + "/" + file_name
    # Очищаем путь от возможных начальных слешей
    clean_path = file_path.lstrip("/")
    return f"{clean_path}"


def build_search_prompt(results: list[dict], question: str, top_k: int) -> str:
    blocks = []
    doc_res = {}
    for item in results:
        meta = item.get("metadata") or {}
        doc_id = meta.get("document_id") or meta.get("chunk_id") or f"row_{item['rank']}"
        if doc_id not in doc_res:
            doc_res[doc_id] = item
        else:
            doc_res[doc_id]["content"] += "\n...\n" + item["content"]
            doc_res[doc_id]["rank"] = min(doc_res[doc_id]["rank"], item["rank"])

    logger.debug("\n%s", json.dumps(doc_res, indent=2, ensure_ascii=False))
    shown = 0
    for i, (doc_id, item) in enumerate(sorted(doc_res.items(), key=lambda entry: entry[1]["rank"])):
        text = item["content"].strip()
        metadata = item.get("metadata", {})

        relative_path = metadata.get("relative_path", "")
        source = metadata.get("source", "")

        block = f"""rank [{i + 1}] FILE_NAME: {source}
RELATIVE_PATH: {relative_path}

DOCUMENT_ID: {doc_id}

TEXT:
{text}
"""
        blocks.append(block)
        shown += 1
        if shown == top_k:
            break

    context = "\n---\n\n".join(blocks)

    return f"""Используй только информацию из CONTEXT.
Если ответа нет в контексте не придумывай сам.

CONTEXT
{context}

QUESTION
{question}
"""


# ============================================================================
# MCP СЕРВЕР И ENDPOINTS
# ============================================================================
# Инициализация FastMCP
mcp = FastMCP("kbsearch")
@mcp.tool()
async def kb_search(
    query: Annotated[str, "Search phrase to match against the indexed files"],
    collection: Annotated[str | None, "Target collection. If None, will be used active collection"]=None,
    filters: Annotated[dict | None,
        """
        Optional metadata filters.
        Example:
        {
        "kb_id": "01_Маркетинговые материалы",
        "section_relationships": "01_Маркетинговые материалы/02_Fort Knox"
        }
        """] = None,
    top_k: Annotated[int, "Number of results to return (default: SIMILARITY_TOP_K)"] = SIMILARITY_TOP_K,
    include_metadata: Annotated[bool, "Include document metadata in the output"] = True,
    search_profile: Annotated[
        str,
        "Scenario preset: drives hybrid vs dense on Qdrant hybrid collections and RRF tuning. "
        "Use 'doc_search', 'kb_answer', or 'default'.",
    ] = "default",
):
    """
    Search over pre-indexed files in the internal knowledge base.

    The `query` text is searched EXACTLY as provided — no rewriting, expansion, or paraphrasing is applied.
    Use this tool when a user asks something that should be matched against the indexed documents.
    `top_k` controls how many matching passages to return.
    Set `include_metadata=True` if document metadata is needed.
    For Qdrant hybrid collections (dense+sparse), `search_profile` selects search mode
    (`hybrid` RRF vs `dense`-only on hybrid vectors) and RRF tuning via env
    (see utils/search_profile.py: KB_DEFAULT_SEARCH_MODE, KB_SEARCH_MODE_*,
    KB_RRF_K_*, KB_CANDIDATE_MULT_*, KB_HYBRID_*).

    Non-hybrid (legacy dense-only) collections are not supported — reindex via kb-manager.

    Not for web search or database queries. Only searches the pre-indexed documents.
    """
    profile = normalize_search_profile(search_profile)
    profile_cfg = search_profile_config(profile)
    logger.info(
        f"Поиск: '{query}' (top_k={top_k}, search_profile={profile}, "
        f"search_mode={profile_cfg.search_mode}, rrf_k={profile_cfg.rrf_k}, "
        f"candidate_mult={profile_cfg.candidate_mult}), "
        f"collection={collection}, filters={filters}"
    )

    async with kb_lock:
        if not kb_runtime.initialized or not indexer.retriever:
            logger.warning("KB не инициализирована")
            res = ToolResult(
            content="База знаний не инициализирована",
            structured_content=None
            )
            res.isError = True
            return res
            
        if not indexer.cfg.use_qdrant and not kb_runtime.initialized:            
            res = ToolResult(
                content="Локальный FAQ не инициализирован",
                structured_content=None
            )
            res.isError = True
            return res
   
        try:
            if not indexer.cfg.use_qdrant:
                res = ToolResult(
                    content="USE_QDRANT=false: поиск недоступен. Включите Qdrant и kb-manager.",
                    structured_content=None,
                )
                res.isError = True
                return res

            if not indexer.hybrid_search_enabled(collection):
                res = ToolResult(
                    content=(
                        "Коллекция не является hybrid (dense+sparse). "
                        "Переиндексируйте через kb-manager."
                    ),
                    structured_content=None,
                )
                res.isError = True
                return res

            if profile_cfg.search_mode == "dense":
                rows = indexer.hybrid_dense_search(
                    query, collection, filters, int(top_k * 1.5), search_profile=profile
                )
            else:
                rows = indexer.hybrid_search_rrf(
                    query, collection, filters, int(top_k * 1.5), search_profile=profile
                )

            if not rows:
                res = ToolResult(
                    content="Ничего не найдено",
                    structured_content=None,
                )
                res.isError = False
                return res

            results = []
            for i, item in enumerate(rows):
                metadata = dict(item.get("metadata") or {})
                metadata["relative_path"] = get_file_link(
                    metadata.get("source", ""),
                    metadata.get("section_path", []),
                )
                entry = {
                    "rank": i,
                    "score": item["score"],
                    "dense_score": item.get("dense_score"),
                    "sparse_score": item.get("sparse_score"),
                    "lexical_score": None,
                    "content": item["text"],
                    "metadata": metadata,
                }
                results.append(entry)

            logger.info(
                f"Найдено {len(results)} результатов (Qdrant hybrid, mode={profile_cfg.search_mode})"
            )

            prompt = build_search_prompt(results, query, top_k)
            logger.debug(f"res:\n{prompt}")
            res = ToolResult(
                content=prompt,
                structured_content=None,
            )
            res.isError = False
            return res
        except Exception as e:
            logger.debug(f"Ошибка поиска: {e}")
            raise e
            
@mcp.tool()
async def get_kb_info() -> Dict:
    """
    Get status of internal knowledge base.
    """
    logger.info("Запрос информации о KB")
    return get_kb_status()

# ============================================================================
# REST API ENDPOINTS (read-only)
# ============================================================================

async def kb_status_handler(request: Request) -> JSONResponse:
    try:
        logger.debug("Запрос статуса KB")
        return JSONResponse({"success": True, "status": get_kb_status()})
    except Exception as e:
        logger.error(f"Ошибка в kb_status_handler: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)


async def kb_documents_search(request: Request) -> JSONResponse:
    payload = await request.json()
    result = await asyncio.get_running_loop().run_in_executor(
        None, indexer.filter_documents, payload
    )
    items = (result or {}).get("items") or []
    return JSONResponse({"success": True, "count": len(items), "items": items})

# ============================================================================
# СОЗДАНИЕ STARLETTE ПРИЛОЖЕНИЯ
# ============================================================================
# Создаём Starlette приложение с MCP и REST endpoints
headers = {"Authorization": f"Bearer {API_TOKEN}"} if API_TOKEN else {}
mcp_app = mcp.http_app()

# ---------------------------------------------------------------------------
# AUTH WRAPPER ДЛЯ MCP
# ---------------------------------------------------------------------------

async def auth_guard(scope, receive, send):
    """
    ASGI-обёртка над MCP-приложением с проверкой Bearer токена.
    Работает и для HTTP, и для WebSocket (если появится).
    """
    if scope["type"] == "http":
        # Для HTTP-запросов используем объект Request для удобства
        request = Request(scope, receive)
        auth_header = request.headers.get("Authorization", "")
        logger.info(f"=== AUTH CHECK, Path: {scope['path']}")

        if API_TOKEN and not auth_header.startswith(f"Bearer {API_TOKEN}"):
            response = JSONResponse({"error": "Unauthorized"}, status_code=401)
            await response(scope, receive, send)
            return
    
    # Если аутентификация не требуется или пройдена, передаем управление
    # существующему приложению mcp_app
    await mcp_app(scope, receive, send)

# ---------------------------------------------------------------------------
# LIFESPAN: MCP + KB
# ---------------------------------------------------------------------------

original_lifespan = mcp_app.router.lifespan_context

@asynccontextmanager
async def lifespan(app):
    global qdrant_init_task
    logger.info("=" * 70)
    logger.info("ИНИЦИАЛИЗАЦИЯ СЕРВЕРА")
    logger.info("=" * 70)

    # ВАЖНО: сначала даём отработать встроенному лайфспану FastMCP
    async with original_lifespan(app):
        # инициализация KB
        await initialize_kb_on_startup()

        if USE_QDRANT and not kb_runtime.initialized:
            qdrant_init_task = asyncio.create_task(qdrant_init_retry_loop())

        logger.info("=" * 70)
        logger.info(f"MCP KB SEARCH SERVER v{SCRIPT_VERSION} ЗАПУЩЕН")
        logger.info(f"MCP endpoint: {MCP_KBSEARCH}")
        logger.info(f"REST endpoints: /kb/status, /kb/documents/search")
        if USE_QDRANT and not kb_runtime.initialized:
            logger.info(
                f"Ожидание Qdrant: retry={KB_QDRANT_RETRY_INTERVAL}s, "
                f"timeout={KB_QDRANT_INIT_TIMEOUT}s"
            )
        logger.info("=" * 70)

        yield  # тут сервер живёт

        if qdrant_init_task and not qdrant_init_task.done():
            qdrant_init_task.cancel()
            try:
                await qdrant_init_task
            except asyncio.CancelledError:
                pass
        qdrant_init_task = None

    logger.info("Завершение работы сервера...")

# ---------------------------------------------------------------------------
# STARLETTE APP
# ---------------------------------------------------------------------------

app = Starlette(
    routes=[
        Mount(MCP_KBSEARCH, app=auth_guard),
        Route("/kb/status", kb_status_handler, methods=["GET"]),
        Route("/kb/documents/search", kb_documents_search, methods=["POST"]),
    ],
    lifespan=lifespan,
)
# ---------------------------------------------------------------------------
# ЗАПУСК
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logger.info(f"Запуск MCP KB Search Server v{SCRIPT_VERSION}")
    logger.info(f"Хост: {MCP_HOST}:{MCP_PORT}")
    logger.info(f"MCP base path: {MCP_KBSEARCH} (endpoint будет {MCP_KBSEARCH}/mcp)")
    logger.info(f"Модель эмбеддинга: {EMBEDDING_MODEL}")
    logger.info(f"Размер чанка: {CHUNK_SIZE}, перекрытие: {CHUNK_OVERLAP}")

    config = uvicorn.Config(
        app=app,
        host=MCP_HOST,
        port=MCP_PORT,
        log_level="info",
        access_log=True
    )
    server = uvicorn.Server(config)

    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        logger.info("Сервер остановлен пользователем")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise
