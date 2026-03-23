# Основные
from starlette.applications import Starlette
from starlette.routing import Mount, Route
from starlette.responses import JSONResponse
from starlette.requests import Request
from contextlib import asynccontextmanager

# FastMCP
from typing import Annotated, Dict, List
from fastmcp import FastMCP
from fastmcp.tools.tool import ToolResult
# Утилиты
import asyncio
from pathlib import Path
from datetime import datetime
import uvicorn
from dotenv import load_dotenv
import re
import json
import os, shutil

# вынесенная работа с хранилищем, аналогичная с faq 
from utils.storager import LocalStorage, SourceType, UpdateMode
# Вынесенная работа с индексом
from utils.indexer import Indexer, IndexRuntime, IndexerConfig
from utils.preprocessors.document_loader import DocumentLoader
# Используем одинаковую функцию для оптимального логирования качевала из faq идентична
from utils.logger import setup_logger

# ============================================================================
# КОНФИГУРАЦИЯ И ИНИЦИАЛИЗАЦИЯ
# ============================================================================

# Загрузка переменных окружения
load_dotenv()

# Определение путей
SCRIPT_DIR = Path(__file__).parent.absolute()
ROOT_DIR = SCRIPT_DIR.parent.parent
KB_DEFAULT_SOURCE = os.getenv("KB_DEFAULT_SOURCE", str(ROOT_DIR / "dvc-registry" / "inputs" / "kn_base" / "products"))

# Версия скрипта из имени файла
SCRIPT_VERSION = re.findall(r"v\d+", os.path.basename(__file__))[-1].replace("v", "")

# Пути для хранения KB и индексов (локальные, независимые от DVC)
KB_SERVICE_DIR = SCRIPT_DIR / "kb_service"
IN_DOCKER = os.getenv("IN_DOCKER", "false").lower() == "true"
if IN_DOCKER:
    KB_DOCUMENTS_DIR = KB_SERVICE_DIR / "kb_documents"
    KB_LOCAL_MOUNT = KB_SERVICE_DIR / "kb_local"
else:
    KB_DOCUMENTS_DIR = KB_SERVICE_DIR / "data/documents"
    KB_LOCAL_MOUNT = KB_SERVICE_DIR / "data/local"

# Создаём директории, если их нет
for p in [KB_SERVICE_DIR, KB_DOCUMENTS_DIR, KB_LOCAL_MOUNT]:
    p.mkdir(exist_ok=True, parents=True)

# Конфигурация сервера
MCP_KBSEARCH = os.getenv("MCP_KBSEARCH", "/kbsearch")
API_TOKEN = os.getenv("MCP_TOKEN")
MCP_HOST = os.getenv("MCP_HOST", "0.0.0.0")
MCP_PORT = int(os.getenv("MCP_PORT", 7001))

# Конфигурация индексирования
EMBEDDING_API_KEY = os.getenv("EMBEDDING_API_KEY", "REDACTED_EXAMPLE")
EMBEDDING_API_URL = os.getenv("EMBEDDING_API_URL", "https://dsrv1.llm.nstcloud.ru/v1/embeddings")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
CHUNK_SIZE = int(os.getenv("KB_CHUNK_SIZE", 512))
CHUNK_OVERLAP = int(os.getenv("KB_CHUNK_OVERLAP", 50))
SIMILARITY_TOP_K = int(os.getenv("KB_SIMILARITY_TOP_K", 10))
SIMILARITY_CUTOFF = float(os.getenv("KB_SIMILARITY_CUTOFF", 0.35))
SUPPORTED_EXTENSIONS = list(os.getenv("SUPPORTED_EXT",['.txt', '.pdf', '.docx', '.md']))

# Qdrant settings
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "kb_collection")
DISTANCE_METRIC = os.getenv("DISTANCE", "COSINE")
USE_QDRANT = os.getenv("USE_QDRANT", "false").lower() == "true"
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
QDRANT_ALIAS = os.getenv("QDRANT_ALIAS", "kb_collection_active")
# Дополнительные параметры индексации, не обязательные 
INDEX_ANSWERS = os.getenv("INDEX_ANSWERS", "false").lower() == "true"
MAP_TRUE = False

# настраиваем логирование сервера
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")

logger = setup_logger("kbsearch_server", service_dir=KB_SERVICE_DIR, log_level=LOG_LEVEL)

logger.info(f"Logging level: {LOG_LEVEL}")
#  создаем storage объект, отвечающий за всю работу с хранилищами
storage = LocalStorage(documents_dir=KB_DOCUMENTS_DIR, service_dir=KB_SERVICE_DIR,
                       local_mount=KB_LOCAL_MOUNT if IN_DOCKER else None,
                        in_docker=IN_DOCKER, default_path=KB_DEFAULT_SOURCE,
                        supported_ext=SUPPORTED_EXTENSIONS)
# создаем processor для подготовки файлов
processor = DocumentLoader(
    documents_dir=KB_DOCUMENTS_DIR,
    service_dir=KB_SERVICE_DIR,
    chunk_size=CHUNK_SIZE,
    chunk_overlap=CHUNK_OVERLAP
)
idx_config = IndexerConfig(
    service_dir=KB_SERVICE_DIR,
    documents_dir=KB_DOCUMENTS_DIR,
    # API настройки
    embed_api_url=EMBEDDING_API_URL,
    embed_api_key=EMBEDDING_API_KEY,
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
logger.debug(f"Indexer config:\n{idx_config}")
# ============================================================================
# ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ, добавили блокировку
# ============================================================================
kb_lock = asyncio.Lock()
kb_runtime = IndexRuntime()


def get_kb_status() -> Dict:
    """Получить текущий статус KB."""
    metadata = indexer.get_active_metadata()
    return {
        "initialized": kb_runtime.initialized,
        "index_exists": bool(kb_runtime.initialized),
        "points_count": metadata.get("points_count", 0),
        "document_count": metadata.get("document_count", 0),
        "metadata": metadata,
        "last_update": kb_runtime.last_update
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
        
async def background_rebuild_index(target_collection=None):
    """
    Пересборка индекса в фоне
    Запускает процесс полной переиндексации. Создает новый индекс
    При успехе сервер подгружает новый индекс в память
    """
    loop = asyncio.get_running_loop()
    docs_texts, map_data, doc_count, points_count = await loop.run_in_executor(
        None, processor.prepare_docs_texts, None, MAP_TRUE, INDEX_ANSWERS
    )
    if not docs_texts:
        return JSONResponse({"error": "No documents found"}, status_code=404)
    logger.info(f"Данные готовы ({len(docs_texts)} чанков). Запуск Indexer...")
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


# ============================================================================
# ИНИЦИАЛИЗАЦИЯ KB ПРИ ЗАПУСКЕ
# ============================================================================

async def initialize_kb_on_startup() -> None:
    """
    Инициализация KB при запуске сервера.
    
    Логика:
    1. Если индекс существует на диске - загружаем его
    2. Если документы есть - создаём новый индекс
    3. Если ничего нет - загружаем из источника по умолчанию
    """
    logger.info("=" * 70)
    logger.info("ИНИЦИАЛИЗАЦИЯ БАЗЫ ЗНАНИЙ")
    logger.info("=" * 70)
    
    try:
        # Шаг 1: Пытаемся загрузить существующий индекс
        if await load_state():
            logger.info("✓ KB инициализирована из сохранённого индекса")
            return
        
        # Шаг 2: Проверяем наличие документов
        doc_count = len(list(KB_DOCUMENTS_DIR.rglob("*.*")))
        if doc_count > 0:
            logger.info(f"Найдено {doc_count} документов, создаём новый индекс...")
            await background_rebuild_index()
            return
        
        # Шаг 3: Загружаем из источника по умолчанию
        logger.info("Документы не найдены, загружаем из источника по умолчанию...")
        
        if KB_DEFAULT_SOURCE and Path(KB_DEFAULT_SOURCE).exists():
            success = await storage.copy_from_local(
                source_path=Path(KB_DEFAULT_SOURCE),
                dest_path=KB_DOCUMENTS_DIR,
                mode=UpdateMode.REPLACE
            )
            
            if success:
                await background_rebuild_index()
                return
        
        # Если всё не сработало
        logger.warning("⚠ KB не инициализирована. Сервер работает в режиме ожидания.")
        logger.warning(f"  Используйте endpoint /kb/update для загрузки документов")
        
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

def normalize_text(text: str) -> str:
    text = (text or "").lower()
    text = text.replace("ё", "е")
    text = re.sub(r"[_/\\\-+]+", " ", text)
    text = re.sub(r"[^\w\sа-яА-Яa-zA-Z]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def expand_query_terms(query: str) -> list[str]:
    q = normalize_text(query)
    terms = [t for t in q.split() if len(t) >= 2]

    aliases = set(terms)
    joined = " ".join(terms)

    # минимальный словарь под ваши продуктовые запросы
    if "форт нокс" in joined or "fort knox" in joined or "форт" in joined or "fort" in joined or "нокс" in joined or "knox" in joined:
        aliases.update(["fort knox", "форт нокс"])

    if "защищенный капитал" in joined or "защищённый капитал" in joined:
        aliases.update(["защищенный капитал", "защищённый капитал", "zash ish ennyiy kapital"])
    if "aльфа" in joined or "alpha" in joined:
        aliases.update(["alpha", "альфа", "al pha"])
    if "инвестиции" in joined:
        aliases.update(['investicii'])

    return list(aliases)


def overlap_score(text: str, terms: list[str]) -> float:
    text_norm = normalize_text(text)
    if not text_norm or not terms:
        return 0.0

    hits = sum(1 for term in terms if term in text_norm)
    return hits / max(len(terms), 1)


def phrase_score(text: str, query: str) -> float:
    text_norm = normalize_text(text)
    query_norm = normalize_text(query)
    logger.debug(f"Phrase score{text_norm} : {query_norm}")
    if not text_norm or not query_norm:
        return 0.0
    return 1.0 if query_norm in text_norm else 0.0


def get_section_text(section_path) -> str:
    if isinstance(section_path, list):
        return " ".join(str(x) for x in section_path if x)
    return str(section_path or "")


def metadata_to_searchable_fields(metadata: dict) -> tuple[str, str]:
    source = str(metadata.get("source", "") or "")
    section_text = get_section_text(metadata.get("section_path", []))
    return source, section_text


def low_info_penalty(content: str) -> float:
    text = (content or "").strip()
    if not text:
        return 0.35

    if len(text) < 20:
        return 0.25

    letters = sum(ch.isalpha() for ch in text)
    digits = sum(ch.isdigit() for ch in text)

    # почти один номер / id
    if letters == 0 and digits > 0:
        return 0.35

    # URL / короткие служебные хвосты
    text_norm = normalize_text(text)
    if text_norm.startswith("www ") or "http" in text_norm:
        return 0.20

    if letters > 0 and digits > letters:
        return 0.20

    return 0.0


def compute_lexical_score(query: str, content: str, metadata: dict) -> float:
    terms = expand_query_terms(query)
    source, section_text = metadata_to_searchable_fields(metadata)

    content_overlap = overlap_score(content, terms)
    source_overlap = overlap_score(source, terms)
    section_overlap = overlap_score(section_text, terms)

    content_phrase = phrase_score(content, query)
    source_phrase = phrase_score(source, query)
    section_phrase = phrase_score(section_text, query)

    # Для file_search сильнее бустим source и section_path
    score = (
        0.1 * content_overlap +
        0.15 * source_overlap +
        0.1 * section_overlap +
        0.25 * content_phrase +
        0.25 * source_phrase +
        0.25 * section_phrase
    )
    return min(score, 1.0)


def compute_final_score(dense_score: float, lexical_score: float, content: str) -> float:
    base = 0.35 * float(dense_score or 0.0) + 0.65 * float(lexical_score or 0.0)
    penalty = low_info_penalty(content)
    return base - penalty

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
):
    """
    Search over pre-indexed files in the internal knowledge base.

    The `query` text is searched EXACTLY as provided — no rewriting, expansion, or paraphrasing is applied.
    Use this tool when a user asks something that should be matched against the indexed documents.
    `top_k` controls how many matching passages to return.
    Set `include_metadata=True` if document metadata is needed.

    Not for web search or database queries. Only searches the pre-indexed documents.
    """
    logger.info(f"Поиск: '{query}' (top_k={top_k}), collection={collection}, filters={filters}")

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
            candidate_k = max(top_k * 5, 30)
            retriever = indexer.get_retriever_for_collection(collection, candidate_k, filters)
            nodes = retriever.retrieve(query)

            if not nodes:
                res = ToolResult(
                    content="Ничего не найдено",   
                    structured_content=None
                )
                res.isError = False
                return res

            rescored = []
            for node in nodes:
                content = node.get_content()
                metadata = node.metadata or {}
                dense_score = float(node.score or 0.0)
                lexical_score = compute_lexical_score(query, content, metadata)
                final_score = compute_final_score(dense_score, lexical_score, content)

                rescored.append({
                    "node": node,
                    "dense_score": dense_score,
                    "lexical_score": lexical_score,
                    "final_score": final_score,
                })

            rescored.sort(key=lambda x: x["final_score"], reverse=True)
            rescored = rescored[:top_k]

            results = []
            for i, item in enumerate(rescored):
                node = item["node"]
                result = {
                    "rank": i,
                    "score": item["final_score"],
                    "dense_score": item["dense_score"],
                    "lexical_score": item["lexical_score"],
                    "content": node.get_content(),
                }

                if include_metadata:
                    result["metadata"] = node.metadata or {}
                if item["final_score"]>=SIMILARITY_CUTOFF:
                    results.append(result)

            logger.info(f"Найдено {len(results)} результатов после hybrid rerank")

            for res in results:
                metadata = res.get("metadata") or {}
                metadata["relative_path"] = get_file_link(
                    metadata.get("source", ""),
                    metadata.get("section_path", [])
                )
                res["metadata"] = metadata

            

            def cleanup_label(text: str) -> str:
                text = re.sub(r"^\d+[_\-\s]*", "", text)
                return text.strip()

            def make_title(metadata: dict) -> str:
                section = metadata.get("section_path", [])
                source = metadata.get("source", "")

                cleaned = [cleanup_label(x) for x in section if x]

                if len(cleaned) >= 2:
                    return " — ".join(cleaned[-2:])
                if cleaned:
                    return cleaned[-1]
                return source
            
            def build_prompt(results: list[dict], question: str) -> str:
                blocks = []
                doc_res = {}
                for item in results:
                    doc_id = item["metadata"]["document_id"]
                    if not doc_id in doc_res.keys():
                        doc_res.update({doc_id:item})
                    else:
                        doc_res[doc_id]["content"] += "\n..." + item["content"]
                        doc_res[doc_id]["rank"] = min(doc_res[doc_id]["rank"], item["rank"])
                        
                logger.debug("\n%s", json.dumps(doc_res, indent=2, ensure_ascii=False))
                
                for i, (doc_id, item) in enumerate(sorted(doc_res.items(), key=lambda item: item[1]["rank"])):
                    text = item["content"].strip()
                    metadata = item.get("metadata", {})
                    title = make_title(metadata)
                    relative_path = metadata.get("relative_path", "")

                    block = f"""rank [{i+1}] {title}
RELATIVE_PATH: {relative_path}

DOCUMENT_ID: {doc_id}

{text}
"""
                    blocks.append(block)

                context = "\n---\n\n".join(blocks)

                return f"""Используй только информацию из CONTEXT.
Если ответа нет в контексте не придумывай сам.

CONTEXT
{context}

QUESTION
{question}
"""
            prompt = build_prompt(results, query)
            res = ToolResult(
                            content=prompt,
                            structured_content=None
                                            )
            res.isError = False
            return res

        except Exception as e:
            logger.error(f"Ошибка при поиске: {e}", exc_info=True)
            res = ToolResult(
            content=f"Ошибка при поиске: {e}",
            structured_content=None
        )
            res.isError = True
            return res
@mcp.tool()
async def get_kb_info() -> Dict:
    """
    Get status of internal knowledge base.
    """
    logger.info("Запрос информации о KB")
    return get_kb_status()

# ============================================================================
# REST API ENDPOINTS (для управления KB)
# ============================================================================

async def kb_update_handler(request: Request) -> JSONResponse:
    """
    Endpoint для обновления базы знаний.
    
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

    if kb_lock.locked():
        logger.warning("Попытка обновления KB во время блокировки.")
        return JSONResponse(
            {"success": False, "error": "Обновление базы знаний уже выполняется."},
            status_code=409 # Conflict
        )

    async with kb_lock:
        logger.info("="*50)
        logger.info("НАЧАТО ОБНОВЛЕНИЕ БАЗЫ ЗНАНИЙ")
        logger.info("="*50)
        
    try:
        # Получаем параметры из запроса
        params = dict(request.query_params)
        source_type = SourceType(params.get("source_type", "default"))
        mode = UpdateMode(params.get("mode", "replace"))
        logger.info(f"Запрос обновления KB: source_type={source_type}, mode={mode}")
        ok = await storage.load_documents_from_source(source_type, params, mode)
        if not ok:
            return JSONResponse({"success": False, "error": "Ошибка загрузки данных"}, status_code=500)
        logger.info("Создание нового индекса")
        rebuild_ok = await background_rebuild_index()
        if not rebuild_ok:
            return JSONResponse({"success": False, "error": "Ошибка при создании индекса"}, status_code=500)
        
        return JSONResponse({"success": True, "message": "База знаний успешно обновлена и проиндексирована.", "status": get_kb_status()})

    except Exception as e:
        logger.error(f"Критическая ошибка при обновлении KB: {e}", exc_info=True)
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500
        )


async def kb_clear_handler(request: Request) -> JSONResponse:
    """
    Endpoint для очистки базы знаний.
    - без параметров -> очищает активную коллекцию
    - collection=... -> очищает указанную коллекцию
    """
    try:
        if kb_lock.locked():
            return JSONResponse({"error": "Busy"}, status_code=409)
        collection = request.query_params.get("collection")
        async with kb_lock:
            logger.info("Запрос очистки KB")
            result=await asyncio.get_running_loop().run_in_executor(
                None,
                indexer.clear_index,
                collection
            )
            if result["is_alias"]:
                kb_runtime.initialized=False
        return JSONResponse({"success": True, "result": result})
            
    except Exception as e:
        logger.error(f"Ошибка в kb_clear_handler: {e}", exc_info=True)
        return JSONResponse(
            {"success": False, "error": str(e)},
            status_code=500
        )

async def kb_status_handler(request: Request) -> JSONResponse:
    """
    Endpoint для получения статуса базы знаний.
    """
    try:
        logger.debug("Запрос статуса KB")
        return JSONResponse({"success": True, "status": get_kb_status()})
        
    except Exception as e:
        logger.error(f"Ошибка в kb_status_handler: {e}", exc_info=True)
        return JSONResponse({"success": False, "error": str(e)}, status_code=500)
    
# ============================================================================
# ENDPOINDS FUNCTIONS
# ============================================================================
async def upload_kb(request):
    """
    Endpoint для загрузки KB файлов в локальное хранилище.
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
    for file in KB_LOCAL_MOUNT.iterdir():
        if file.is_dir():
            shutil.rmtree(file)
        else:
            file.unlink()
    return JSONResponse({"success": True, "message": "Все загруженные файлы удалены"})

# endpoint для фильтрации (TODO) работает но беда с кириллицей
# /kb/documents/search
async def kb_documents_search(request: Request) -> JSONResponse:
    """
    READ — фильтрация документов
    Поиск по KB с поддержкой Qdrant-фильтров
    usage:
    "curl -X POST http://localhost:7001/kb/documents/search \
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
    if points==[]:
        return JSONResponse({
            "success": True,
            "count": 0,
            "items": []
        })
    return JSONResponse({
        "success": True,
        "count": len(points),
        "items": points
    })
 

# endpoint to switch collection
# /kb/collections/switch
async def kb_collections_switch(request: Request) -> JSONResponse:
    """
    Функция для переключения между коллекциями 
    Usage:
    "curl -X POST http://localhost:7001/kb/collections/switch \
    -H "Content-Type: application/json" \
    -d '{"collection":"kb_collection_v2"}'"    
    
    "curl -X POST http://localhost:7001/kb/collections/switch -H "Content-Type: application/json" -d '{"collection":"kb_collection"}'"
    """
    if kb_lock.locked():
        logger.warning("Попытка переключения индекса во время другой операции затрагивающей его")
        return JSONResponse({"success": False, "error":  "в данный момент Индекс занят, попробуйте позже"}, status_code=409)
    
    async with kb_lock:
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
# /kb/collections/delete
async def kb_collections_delete(request: Request) -> JSONResponse:
    """
    Endpoint to delete collection
    Usage:
    "curl -X POST http://localhost:7001/kb/collections/delete -H "Content-Type: application/json" -d '{"collection":"kb_collection"}'"
    """
    payload = await request.json()
    collection = payload.get("collection")
    if not collection:
        return JSONResponse({"success": False, "error": "collection required"}, status_code=400)
    success = indexer.collection_delete(collection)
    return JSONResponse({"success": success, "deleted_collection": collection})

# endpoint to prepare new collection
# /kb/collections/prepare
async def prepare_new_collection(request: Request) -> JSONResponse:
    """
    Вспомогательная функция для подготовки новой коллекции BLUE/GREEN
    Создает новую коллекцию и переключает на неё.
    Перед выполнением необходимо загрузить те документы по которым новый индекс
      в хранилище можно через s3 тогда необходимо передавать параметры для s3, или 
      через endpoint для загрузки файлов в local_folder тогда потом как источник параметры
      source_type - local_folder 
    Usage: 
    "curl -X POST http://localhost:7001/kb/collections/prepare \
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
        logger.info(f"Запрос обновления KB при подготовке новой коллекции: source_type={source_type}, mode={mode}")
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
    logger.info("=" * 70)
    logger.info("ИНИЦИАЛИЗАЦИЯ СЕРВЕРА")
    logger.info("=" * 70)

    # ВАЖНО: сначала даём отработать встроенному лайфспану FastMCP
    async with original_lifespan(app):
        # инициализация KB
        await initialize_kb_on_startup()

        logger.info("=" * 70)
        logger.info(f"MCP KB SEARCH SERVER v{SCRIPT_VERSION} ЗАПУЩЕН")
        logger.info(f"MCP endpoint: {MCP_KBSEARCH}")
        logger.info(f"REST endpoints: /kb/status, /kb/update, /kb/clear")
        logger.info("=" * 70)

        yield  # тут сервер живёт

    logger.info("Завершение работы сервера...")

# ---------------------------------------------------------------------------
# STARLETTE APP
# ---------------------------------------------------------------------------

app = Starlette(
    routes=[
        # MCP:
        Mount(MCP_KBSEARCH, app=auth_guard),
        # REST:
        Route("/kb/update", kb_update_handler, methods=["GET", "POST"]),
        Route("/kb/clear", kb_clear_handler, methods=["POST"]),
        Route("/kb/status", kb_status_handler, methods=["GET"]),
        Route("/kb/upload", upload_kb, methods=["POST"]),
        Route("/kb/cleanup_uploads", cleanup_uploaded_files, methods=["POST"]),
        # endpoint to filters and crud
        Route("/kb/documents/search", kb_documents_search, methods=["POST"]),
        # endpoint for working with collections
        Route("/kb/collections/switch", kb_collections_switch, methods=["POST"]),
        Route("/kb/collections/delete", kb_collections_delete, methods=["POST"]),
        # endpoint to prepare new collection (blue/green)
        Route("/kb/collections/prepare", prepare_new_collection, methods=["POST"])
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