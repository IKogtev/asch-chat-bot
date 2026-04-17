# импорт библиотек для взаимодейсвтия с ОС, логирования, асинхронности и типов
import os, shutil
import re
import asyncio
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
# Вынесенная работа с хранилищем
from utils.storager import LocalStorage, SourceType, UpdateMode
# Вынесенная работа с индексом
from utils.indexer import Indexer, IndexRuntime, IndexerConfig
from utils.preprocessors.document_loader import DocumentLoader
# Используем одинаковую функцию для оптимального логирования
from utils.logger import setup_logger

import textwrap

# -------------------------
# КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# -------------------------
# Загрузка переменных окружения
load_dotenv()

# Определение путей
SCRIPT_DIR = Path(__file__).parent.absolute()
ROOT_DIR = SCRIPT_DIR.parent.parent
FAQ_DEFAULT_SOURCE = os.getenv("FAQ_DEFAULT_SOURCE", str(ROOT_DIR / "dvc-registry" / "inputs" / "kn_base" / "kb_asg" / "hr FAQ"))
# Версия скрипта
try:
    SCRIPT_VERSION = re.findall(r"v\d+", os.path.basename(__file__))[-1].replace("v", "")
except Exception:
    SCRIPT_VERSION = "2.0"

# пути для хранения FAQ из индексов
FAQ_SERVICE_DIR = SCRIPT_DIR / "faq_service"
# определение директорий в зависимости от окружения (докер или локально)
IN_DOCKER = os.getenv("IN_DOCKER", "false").lower() == "true"
if IN_DOCKER:
    FAQ_DOCUMENTS_DIR = FAQ_SERVICE_DIR / "faq_documents"
    FAQ_LOCAL_MOUNT = FAQ_SERVICE_DIR / "faq_local"
else: 
    FAQ_DOCUMENTS_DIR = FAQ_SERVICE_DIR / "data/documents"
    FAQ_LOCAL_MOUNT = FAQ_SERVICE_DIR / "data/local"

# создаем директории, если их нет
for p in [FAQ_SERVICE_DIR, FAQ_DOCUMENTS_DIR, FAQ_LOCAL_MOUNT]:
    p.mkdir(exist_ok=True, parents=True)

# конфиг сервера
MCP_FAQPATH = os.getenv("MCP_FAQPATH", "/faq_rag")
API_TOKEN = os.getenv("MCP_TOKEN")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", 7000))

# Конфигурация индексирования
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "REDACTED_EXAMPLE")
OPENROUTER_API_URL = os.getenv("OPENROUTER_API_URL_EMB", "https://dsrv1.llm.nstcloud.ru/v1/embeddings")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
CHUNK_SIZE = int(os.getenv("CHUNK_SIZE", 512))
CHUNK_OVERLAP = int(os.getenv("CHUNK_OVERLAP", 50))
SIMILARITY_TOP_K = int(os.getenv("SIMILARITY_TOP_K", 5))
SIMILARITY_CUTOFF = float(os.getenv("SIMILARITY_CUTOFF", 0.0))
SUPPORTED_EXTENSIONS = list(os.getenv("SUPPORTED_EXT",['.json', '.txt', '.md', '.csv', '.xlsx', '.xls']))
# Qdrant settings
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "faq_collection")
DISTANCE_METRIC = os.getenv("DISTANCE", "COSINE")
USE_QDRANT = os.getenv("USE_QDRANT", "false").lower() == "true"
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_ALIAS = os.getenv("QDRANT_ALIAS", "faq_collection_active")
# Дополнительные параметры индексации, не обязательные 
INDEX_ANSWERS = os.getenv("INDEX_ANSWERS", "false").lower() == "true"
MAP_TRUE = True
# настраиваем логирование сервера
logger = setup_logger("faq_rag_server", service_dir=FAQ_SERVICE_DIR)
#  создаем storage объект, отвечающий за всю работу с хранилищами
storage = LocalStorage(documents_dir=FAQ_DOCUMENTS_DIR, service_dir=FAQ_SERVICE_DIR,
                       local_mount=FAQ_LOCAL_MOUNT if IN_DOCKER else None,
                        in_docker=IN_DOCKER, default_path=FAQ_DEFAULT_SOURCE,
                        supported_ext=SUPPORTED_EXTENSIONS)
# создаем processor для подготовки файлов
processor = DocumentLoader(
    documents_dir=FAQ_DOCUMENTS_DIR,
    service_dir=FAQ_SERVICE_DIR,
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP
)
idx_config = IndexerConfig(
    service_dir=FAQ_SERVICE_DIR,
    documents_dir=FAQ_DOCUMENTS_DIR,
    # API настройки
    embed_api_url=OPENROUTER_API_URL,
    embed_api_key=OPENROUTER_API_KEY,
    embed_model_name=EMBEDDING_MODEL,
    # Chunking
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP,
    similarity_top_k=SIMILARITY_TOP_K,
    similarity_cutoff=SIMILARITY_CUTOFF,
    # Qdrant Config
    use_qdrant=USE_QDRANT,
    qdrant_host=QDRANT_HOST,
    qdrant_port=QDRANT_PORT,
    qdrant_collection=QDRANT_COLLECTION,
    qdrant_alias=QDRANT_ALIAS,
    distance_metric=DISTANCE_METRIC,
    version=SCRIPT_VERSION
)
# Создаём Indexer с текущими конфигами
indexer = Indexer(idx_config)
# -------------------------
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ + блокировка | Состояние сервера
# -------------------------
faq_lock = asyncio.Lock()
faq_runtime = IndexRuntime()

# -------------------------
# СТАТУС FAQ
# -------------------------
def get_faq_status() -> Dict:
    """Получить текущий статус FAQ."""
    metadata = indexer.get_active_metadata()
    # documents_count соответствует количеству вопросов которые в индексе
    return {
        "initialized": faq_runtime.initialized,
        "index_exists": bool(faq_runtime.initialized),
        "documents_count": metadata.get("documents_count", 0),
        "metadata": metadata,
        "last_update": faq_runtime.last_update,
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
        
async def background_rebuild_index(target_collection=None):
    """
    Пересборка индекса в фоне если его не существует используем
    Запускает процесс полной переиндексации. Создает новый индекс
    При успехе сервер подгружает новый индекс в память
    """    
    loop = asyncio.get_running_loop()
    docs_texts, map_data, doc_count, points_count = await loop.run_in_executor(
        None, processor.prepare_docs_texts,None, MAP_TRUE, INDEX_ANSWERS
    )
    if not docs_texts:
        return JSONResponse({"error": "No documents found"}, status_code=404)
    logger.info(f"Данные готовы ({doc_count} чанков). Запуск Indexer...")
    logger.info(f"Начинается пересборка индекса (background) {target_collection}")
    # запуск задачи пересборки
    success = await loop.run_in_executor(None, indexer.rebuild_index,
                                        docs_texts,     # Передаем подготовленные данные
                                        doc_count,
                                        points_count,
                                        target_collection                          
     )
    if success:
        logger.info("Индекс пересоздан, обновляем память сервера ...")
        # обновляем текущее состояние
        await load_state()
    else:
        logger.error("Ошибка при создании индекса")
    return success

# -------------------------
# ИНИЦИАЛИЗАЦИЯ ПРИ ЗАПУСКЕ
# -------------------------
async def initialize_faq_on_startup() -> None:
    """
    Инициализация FAQ при запуске сервера.
    
    Логика:
    1. Если индекс существует на диске - загружаем его
    2. Если документы есть - создаём новый индекс
    3. Если ничего нет - загружаем из источника по умолчанию
    """
    logger.info("=" * 70)
    logger.info("ИНИЦИАЛИЗАЦИЯ FAQ RAG ПРИ СТАРТЕ")
    logger.info("=" * 70)
    try:
        # 1 - попытка загрузить уже имеющийся
        if await load_state():
            logger.info("✓ FAQ инициализирована из сохранённого индекса")
            return
        # 2 - Проверка наличия документов
        doc_count = len(list(FAQ_DOCUMENTS_DIR.rglob("*.*")))
        if doc_count > 0:
            logger.info(f"Найдено {doc_count} документов, создаём индекс...")
            await background_rebuild_index()
            return
        # 3 - Если документов нет, пробуем загрузить дефолтные
        logger.info("Документы FAQ не найдены, загружаеим из источника по умолчанию...")
        if FAQ_DEFAULT_SOURCE and Path(FAQ_DEFAULT_SOURCE).exists():
            success = await storage.copy_from_local(source_path=Path(FAQ_DEFAULT_SOURCE),
                                                    dest_path=FAQ_DOCUMENTS_DIR,
                                                    mode=UpdateMode.REPLACE)
            if success:
                await background_rebuild_index()
                return    
        logger.warning("FAQ не инициализирован. Используйте /faq/update для загрузки документов")
    
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

    logger.info(
        f"FAQ поиск: '{question}' (top_k={top_k}), collection={collection}, filters={filters}"
    )

    async with faq_lock:

        if not indexer.cfg.use_qdrant and not faq_runtime.initialized:
            return ToolResult(
                content="# Результат поиска FAQ\n\nБаза FAQ временно недоступна.",
                structured_content=None
            )

        try:

            if collection:
                retriever = indexer.get_retriever_for_collection(collection, top_k, filters)
            else:
                retriever = indexer.get_retriever_for_collection(None, top_k, filters)

            nodes = retriever.retrieve(question)

            if not nodes:
                return ToolResult(
                    content=textwrap.dedent(f"""
                    # Результат поиска FAQ

                    **Вопрос пользователя:** {question}

                    В официальной базе FAQ не найдено релевантных записей.
                    """).strip(),
                    structured_content=None
                )

            candidates = []

            for n in nodes:

                score = getattr(n, "score", 0.0)
                if score < MIN_SCORE:
                    continue

                payload = getattr(n, "metadata", {})
                answer = payload.get("answer")
                faq_question = payload.get("question") or n.get_content()

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
async def faq_update_handler(request: Request) -> JSONResponse:
    """
    Endpoint для обновления FAQ.
    
    Query параметры:
        - source_type: "local_folder" | "s3" | "default"
        - mode: "append" | "replace"
        - source_path: путь для local_folder
        - s3_bucket: имя бакета для s3
        - s3_prefix: префикс для s3
        - s3_endpoint: URL эндпоинта S3
        - s3_access_key: ключ доступа S3
        - s3_secret_key: секретный ключ S3
    """
    if faq_lock.locked():
        logger.warning("Попытка обновления FAQ во время другой операции обновления")
        # лог ощибка 409 Conflict
        return JSONResponse({"success": False, "error": "FAQ в данный момент обновляется, попробуйте позже"}, status_code=409)
    
    logger.info("Начало операции обновления FAQ")
    
    try:
        # получаем параметры из запроса
        params = dict(request.query_params)
        source_type = SourceType(params.get("source_type", "default"))
        mode = UpdateMode(params.get("mode", "replace"))
        logger.info(f"Запрос обновления FAQ: source_type={source_type}, mode={mode}")
        ok = await storage.load_documents_from_source(source_type, params, mode)
        if not ok:
            return JSONResponse({"success": False, "error": "Ошибка загрузки данных"}, status_code=500)
        logger.info("Создание нового индекса")
        rebuild_ok = await background_rebuild_index()
        if not rebuild_ok:
            return JSONResponse({"success": False, "error": "Ошибка при создании индекса"}, status_code=500)
        return JSONResponse({"success": True, "message": "FAQ успешно обновлен", "status": get_faq_status()})
    
    except Exception as e:
        logger.error(f"Ошибка в faq_update_handler: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

async def faq_clear_handler(request: Request) -> JSONResponse:
    """
    Endpoint для очистки FAQ.
    - без параметров -> очищает активную коллекцию
    - collection=... -> очищает указанную коллекцию
    """
    try:
        if faq_lock.locked():
            return JSONResponse({"error": "Busy"}, status_code=409)
        collection = request.query_params.get("collection")
        async with faq_lock:
            result = await asyncio.get_running_loop().run_in_executor(
                None, 
                indexer.clear_index,
                collection
            )
            if result["is_alias"]:
                faq_runtime.initialized = False
        return JSONResponse({"success": True, "result": result})
    except Exception as e:
        logger.error(f"Ошибка в faq_clear_handler: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

async def faq_status_handler(request: Request) -> JSONResponse:
    """
    Endpoint для получения статуса FAQ.
    """
    try:
        logger.debug("Запрос статуса FAQ")
        return JSONResponse({"success": True, "status": get_faq_status()})
    
    except Exception as e:
        logger.error(f"Ошибка в faq_status_handler: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# -------------------------
# Uploader function
# -------------------------
async def upload_faq(request):
    """" 
    Endpoint для загрузки FAQ файлов в локальное хранилище.
    """
    try: 
        form = await request.form()
        file = form.get("file")
        if not file:
            return JSONResponse({"error": "No file"}, status_code=400)
        # сохранение теперь передано storage
        saved_path = await storage.save_uploaded_file(file, file.filename)
        return JSONResponse({"success": True, "saved_to": saved_path})
    except Exception as e:
        logger.error(f"Uploaded error: {e}")
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# Функция для очистки папки в которую закидываются файлы
async def cleanup_uploaded_files(request):
    """Удаление загруженных файлов всех"""
    for file in FAQ_LOCAL_MOUNT.iterdir():
        if file.is_dir():
            shutil.rmtree(file)
        else:
            file.unlink()
    return JSONResponse({"success": True, "message": "Все загруженные файлы удалены"})

# /faq/documents/search
async def faq_documents_search(request: Request) -> JSONResponse:
    """
    READ — фильтрация документов
    Поиск по FAQ с поддержкой Qdrant-фильтров
    usage:
    "curl -X POST http://localhost:7000/faq/documents/search \
    -H "Content-Type: application/json; charset=utf-8" \
    --data-binary @- <<EOF
    {
        "category": "Инвестиции",
        "limit": 5
    }
    EOF"
    """
    payload = await request.json()
    points = await asyncio.get_running_loop().run_in_executor(None, indexer.filter_documents, payload)
    if not points:
        return JSONResponse({"success": True, "count": 0, "items": []})
    
    return JSONResponse({
        "success": True,
        "count": len(points),
        "items": points
    })
 
# endpoint to switch collection
# /faq/collections/switch
async def faq_collections_switch(request: Request) -> JSONResponse:
    """
    Функция для переключения между коллекциями 
    Usage:
    "curl -X POST http://localhost:7000/faq/collections/switch \
    -H "Content-Type: application/json" \
    -d '{"collection":"faq_collection_v2"}'"    
    
    "curl -X POST http://localhost:7000/faq/collections/switch -H "Content-Type: application/json" -d '{"collection":"faq_collection"}'"
    """
    if faq_lock.locked():
        logger.warning("Попытка переключения индекса во время другой операции затрагивающей его")
        return JSONResponse({"success": False, "error":  "в данный момент Индекс занят, попробуйте позже"}, status_code=409)
    logger.info("Начало операции смены коллекции")
    try:
        if not indexer.cfg.use_qdrant:
            return JSONResponse({"success": False, "error": "Qdrant disabled"}, status_code=400)
        payload = await request.json()
        collection = payload.get("collection")
        if not collection:
            return JSONResponse({"success": False, "error": "collection required"}, status_code=400)
        switch = indexer.collection_switch(collection)
        if switch is bool:
            return JSONResponse({"success": False, "error": "Collection not found"}, status_code=404)
        sucess =await load_state()
        if sucess:
            logger.info(f"Switched to collection: {collection}")
            return JSONResponse(switch)
        logger.warning("Error while loading current index")
        return JSONResponse({"success": False, "error": "load_state_function_break"})
    except Exception as e:
        logger.error(f"Ошибка при смене коллекции: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)

# endpoint to delete collection
# /faq/collections/delete
async def faq_collections_delete(request: Request) -> JSONResponse:
    """
    Endpoint to delete collection
    Usage:
    "curl -X POST http://localhost:7000/faq/collections/delete -H "Content-Type: application/json" -d '{"collection":"faq_collection_v2"}'"
    """
    payload = await request.json()
    collection = payload.get("collection")
    if not collection:
        return JSONResponse({"success": False, "error": "collection required"}, status_code=400)
    success = indexer.collection_delete(collection)
    return JSONResponse({"success": success, "deleted_collection": collection})

# endpoint to prepare new collection
# /faq/collections/prepare
async def prepare_new_collection(request: Request) -> JSONResponse:
    """
    Вспомогательная функция для подготовки новой коллекции BLUE/GREEN
    Создает новую коллекцию и переключает на неё.
    Перед выполнением необходимо загрузить те документы по которым новый индекс
      в хранилище можно через s3 тогда необходимо передавать параметры для s3, или 
      через endpoint для загрузки файлов в local_folder тогда потом как источник параметры
      source_type - local_folder 
    Usage: 
    "curl -X POST http://localhost:7000/faq/collections/prepare \
    -H "Content-Type: application/json" \
    -d '{
    "version": "2",
    "delete_old": false,
    "source_type": "local_folder",
    "source_path": "."
    }'"
    """
    payload = await request.json()
    version = payload.get("version")
    delete_old = bool(payload.get("delete_old", False))
    source_type = SourceType(payload.get("source_type", "default"))
    mode = UpdateMode(payload.get("mode", "replace"))
    if not version: 
        return JSONResponse({"success": False, "error": "version required"}, status_code=400)
    try:
        logger.info(f"Подготовка новой коллекции версии {version} delete_old={delete_old}")
        logger.info(f"Запрос обновления FAQ при подготовке новой коллекции: source_type={source_type}, mode={mode}")
        ok = await storage.load_documents_from_source(source_type, payload, mode)
        if not ok:
            return JSONResponse({"success": False, "error": "Ошибка загрузки данных"}, status_code=500)
        loop = asyncio.get_running_loop()
        docs_texts, map_data, doc_count, points_count = await loop.run_in_executor(
            None, 
            processor.prepare_docs_texts,
            None,
            MAP_TRUE,  # map_true
            INDEX_ANSWERS  # index_answers
        )
        if not docs_texts:
            return JSONResponse({"error": "No documents found"}, status_code=404)
        logger.info(f"Данные готовы ({len(docs_texts)} чанков). Запуск Indexer...")
        result = await asyncio.get_running_loop().run_in_executor(
            None,
            lambda: indexer.prepare_new_collection(
                version=version,
                docs_texts=docs_texts,     # Передаем подготовленные данные
                doc_counter=doc_count,
                points_count=points_count,
                delete_old=delete_old
            )
        )
        if result.get("sucess"):
            await load_state()
        return JSONResponse(result)
    except Exception as e:
        logger.error(f"Ошибка при подготовке новой коллекции: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
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
    logger.info("=" * 70)
    logger.info("ИНИЦИАЛИЗАЦИЯ СЕРВЕРА FAQ-RAG")
    logger.info("=" * 70)
    # КРИТИЧНО - запускаем StreamableHTTPSessionManager
    async with lifespan_original(app):
        await initialize_faq_on_startup()
        logger.info("=" * 70)
        logger.info("✓ Сервер готов")
        logger.info("=" * 70)
        yield # Тут сервер живёт

    logger.info("Завершение работы сервера...")

app = Starlette(
    routes=[
        # MCP
        Mount(MCP_FAQPATH, app=auth_guard),
        # REST
        # endpoint to update collection
        Route("/faq/update", faq_update_handler, methods=["POST"]),
        # endpoint to clear index collection
        Route("/faq/clear", faq_clear_handler, methods=["POST"]),
        # endpoint to get status of active alias collection
        Route("/faq/status", faq_status_handler, methods=["GET"]),
        # endpoint to load file
        Route("/faq/upload", upload_faq, methods=["POST"]),
        # endpoint to clean all loaded files
        Route("/faq/cleanup_uploads", cleanup_uploaded_files, methods=["POST"]),
        # endpoint to search with filters
        Route("/faq/documents/search", faq_documents_search, methods=["POST"]),
        # endpoint for working with collections
        Route("/faq/collections/switch", faq_collections_switch, methods=["POST"]),
        # endpoint to delete collections
        Route("/faq/collections/delete", faq_collections_delete, methods=["POST"]),
        # endpoint to prepare new collection (blue/green)
        Route("/faq/collections/prepare", prepare_new_collection, methods=["POST"])
    ],
    lifespan=lifespan,
)

if __name__ == '__main__':
    logger.info(f"Запуск MCP FAQ-RAG Server v{SCRIPT_VERSION}")
    logger.info(f"Хост: {MCP_HOST}:{MCP_PORT}")
    logger.info(f"MCP base path: {MCP_FAQPATH} (endpoint будет {MCP_FAQPATH}/mcp)")
    logger.info(f"Эмбеддинг модель: {EMBEDDING_MODEL}")
    logger.info(f"Размер чанка: {CHUNK_SIZE}, перекрытие: {CHUNK_OVERLAP}")

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
    
