from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from typing import List
from pathlib import Path
from dotenv import load_dotenv
from app.services.qdrant_service import QdrantService, CollectionType
from app.models import (
    DocumentInfo, SearchRequest, SearchResult, SwitchCollectionRequest, 
    DeleteCollectionRequest, DeleteKBRequest, SwitchAliasRequest, SyncInterval
    )
from app.utils.preprocessors.document_loader import DocumentLoader as DocumentLoaderFAQ
import hashlib, os, uuid, shutil, asyncio
from app.utils.logger import setup_logger
from app.services.file_storage_service import FileStorageService
from pathlib import Path
import httpx
from urllib.parse import unquote
import aiofiles, shutil
from datetime import datetime, timedelta

BOT_API = "http://bot:8001/broadcast"

PROMPTS_STORAGE_ROOT = Path(os.getenv("PROMPTS_STORAGE_ROOT", "/app/data/prompts"))
PROMPTS_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
ADK_AGENT_URL = os.getenv("ADK_AGENT_URL", "http://adk-agent:8010")

logger = setup_logger(name="Test", service_dir="App")

load_dotenv()
app = FastAPI(
    title="Qdrant Document Manager",
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    max_age=3600,
)

# Initialize services
KB_STORAGE_ROOT= Path(os.getenv("KB_STORAGE_ROOT", "/data/kb_documents"))
KB_STORAGE_ROOT = KB_STORAGE_ROOT.resolve()
KB_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
# folders subdirs for faq and kb
KB_ROOT = KB_STORAGE_ROOT / "kb"
FAQ_ROOT = KB_STORAGE_ROOT / "faq"
KB_ROOT.mkdir(parents=True, exist_ok=True)
FAQ_ROOT.mkdir(parents=True, exist_ok=True)
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", 6333))
collection_name = os.getenv("QDRANT_COLLECTION", "kb_collection")
qdrant_url = f"{QDRANT_HOST}:{QDRANT_PORT}"
# Embedding API configuration
embedding_api_base = os.getenv("EMBEDDING_API_BASE", "https://dsrv1.llm.nstcloud.ru/v1/embeddings")
embedding_api_key = os.getenv("EMBEDDING_API_KEY", "REDACTED_EXAMPLE")
embedding_model = os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B")
embedding_dimensions = int(os.getenv("EMBEDDING_DIMENSIONS", "1024"))
SUPPORTED_FAQ_EXTENSIONS = {".md", ".csv", ".xls", ".xlsx", ".txt", ".pdf", ".docx"}
SUPPORTED_KB_EXTENSIONS = {".md", ".txt", ".pdf", ".docx",".csv", ".xls", ".xlsx"}
PLATFORM_VERSION = os.getenv("PLATFORM_VERSION", "0.5.1")
# Chunking configuration
chunk_size = int(os.getenv("CHUNK_SIZE", "512"))
chunk_overlap = int(os.getenv("CHUNK_OVERLAP", "50"))

# Initialize LangChain-based service
qdrant_service = QdrantService(
    collection_name=collection_name,
    embedding_api_base=embedding_api_base,
    embedding_api_key=embedding_api_key,
    embedding_model=embedding_model,
    embedding_dimensions=embedding_dimensions,
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    qdrant_host=QDRANT_HOST,
    qdrant_port=QDRANT_PORT
)
kb_file_storage = FileStorageService(
    root_path=KB_ROOT,
    qdrant_service=qdrant_service,
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    service_dir=Path("app"),
    ext_allowed = SUPPORTED_KB_EXTENSIONS
)

faq_file_storage = FileStorageService(
    root_path=FAQ_ROOT,
    qdrant_service=qdrant_service,
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    service_dir=Path("app"),
    ext_allowed=SUPPORTED_FAQ_EXTENSIONS
)
# sync settings
sync_lock = asyncio.Lock()
sync_update_event = asyncio.Event()
sync_settings = {
    "interval_hours": 3,
    "interval_seconds": None,
    "last_sync": None,
    "next_sync":None,
    "running": False
}

# Mount static files
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")

def get_interval_delta():
    if sync_settings.get("interval_seconds"):
        return timedelta(seconds=sync_settings["interval_seconds"])
    return timedelta(hours=sync_settings["interval_hours"])

async def sync_function(iter_dir, storager, collection_type):
    """
    syncron function which takes params:
        iter_dir - dir to itterate KB_ROOT, FAQ_ROOT
        storager - prepared storager object,
        collection_type - to work with collection of 1 type
    """
    for folder in iter_dir.iterdir():
        if not folder.is_dir():
            continue

        kb_id = folder.name
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda kid=kb_id: storager.sync(
                    kb_id=kid,
                    collection_type=collection_type
                )
            )
        except Exception as e:
            logger.info(f"[SYNC SERVICE] Error syncing {kb_id}: {e}")

async def run_sync_all_safe():
    if sync_lock.locked():
        logger.info("SYNC alrady_running")
        return {"status": "already_running"}

    async with sync_lock:
        logger.info("[SYNC] started")
        sync_settings["running"] = True
        try:
            await run_sync_all_once()
            sync_settings["last_sync"] = datetime.now().isoformat()
            sync_settings["next_sync"] = (datetime.now()+get_interval_delta()).isoformat()
        finally:
            sync_settings["running"] = False

    return {"status": "completed"}


#  TODO добавлять параллелизм или нет? У нас не вытянет, если добавлять то вот наброски кода: 
# SEM = asyncio.Semaphore(3)
# async def sync_kb(kb_id):

#     async with SEM:

#         loop = asyncio.get_running_loop()

#         await loop.run_in_executor(
#             None,
#             lambda: file_storage_service.sync(
#                 kb_id=kb_id,
#                 collection_type="kb"
#             )
#         )
# async def run_sync_all_once():

#     if not KB_STORAGE_ROOT.exists():
#         return

#     tasks = []

#     for folder in KB_STORAGE_ROOT.iterdir():
#         if folder.is_dir():
#             tasks.append(sync_kb(folder.name))

#     await asyncio.gather(*tasks)

#     logger.info("status success Sync completed")
async def run_sync_all_once():
    logger.info(f"Collection_type now is: {qdrant_service.collection_type}")
    if qdrant_service.collection_type == CollectionType.FAQ:
        await sync_function(FAQ_ROOT, faq_file_storage, "faq")
    elif qdrant_service.collection_type== CollectionType.DOCUMENTS:
        await sync_function(KB_ROOT, kb_file_storage, "kb")
    else: 
        logger.error(f"Something went wrong: {qdrant_service.collection_type}")
    return {
        "status": "success",
        "message": "SYNC completed"
    }     

@app.on_event("startup")
async def startup_event():
    """Initialize Qdrant collection on startup"""
    qdrant_service.ensure_collection()
    asyncio.create_task(run_sync_all_safe())
    asyncio.create_task(start_scheduler())

async def start_scheduler():
    await asyncio.sleep(10)
    await auto_sync()

async def auto_sync():
    logger.info("[AUTO SYNC] loop started")
    if not sync_settings["next_sync"]:
        sync_settings["next_sync"] = (
            datetime.now() + get_interval_delta()
        ).isoformat()
    while True:
        now = datetime.now()
        next_sync = datetime.fromisoformat(sync_settings["next_sync"])
        sleep_time= max((next_sync-now).total_seconds(), 0)
        logger.info(f"[AUTO SYNC] sleeping {sleep_time} sec")    
        try:
            await asyncio.wait_for(sync_update_event.wait(), timeout=sleep_time)
            logger.info("[AUTO SYNC] interval updated")
            sync_update_event.clear()
            continue
        except asyncio.TimeoutError:
            pass
        if not sync_lock.locked():
            await run_sync_all_safe()

@app.get("/api/sync/settings")
async def get_sync_settings():
    return sync_settings

@app.post("/api/sync/settings")
async def set_sync_settings(data: SyncInterval):
    sync_settings["interval_hours"] = data.hours
    now = datetime.now()
    sync_settings["next_sync"] = (now+get_interval_delta()).isoformat()
    sync_update_event.set()
    return {"status": "updated", "interval": data.hours, "next_sync": sync_settings["next_sync"]}

@app.post("/api/filesystem/sync_all")
async def manual_sync_all():
    """
    Эндпоинт для ручной синхронизации по кнопке.
    Вызывает ту же логику, но один раз и сразу возвращает ответ.
    """
    result = await run_sync_all_safe()
    return result


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main UI"""
    with open(static_path / "index.html") as f:
        return f.read()


@app.get("/api/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "qdrant_url": qdrant_url, "collection": collection_name}


@app.get("/api/documents", response_model=List[DocumentInfo])
async def list_documents():
    """List all documents in the collection"""
    try:
        documents = qdrant_service.list_documents()
        return documents
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

async def save_upload_to_tmp(file: UploadFile) -> Path:
    upload_id = uuid.uuid4().hex
    tmp_dir = Path("/tmp/uploads") / upload_id 
    tmp_dir.mkdir(parents=True, exist_ok=True)    
    tmp_file = tmp_dir / (file.filename or "unknown")
    content = await file.read()
    tmp_file.write_bytes(content)
    return tmp_file

def validate_extensions(ext: str, collection_type: str):
    allowed = SUPPORTED_FAQ_EXTENSIONS if collection_type=="faq" else SUPPORTED_KB_EXTENSIONS
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported {collection_type.upper()} format: {ext}"
            f"\n Supported formats are: {', '.join(allowed)}"
        )

@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form("default"),
    user_id: str = Form("anonymous"),
    upload_mode: str = Form("check"),  # check, replace, keep-both, force
    collection_type: str = Form("faq")  # faq or kb
):
    """Upload and process a document
    
    upload_mode:
    - check: Check for duplicates and conflicts (default)
    - replace: Replace existing file with same name
    - keep-both: Keep both versions with incremented version number
    - force: Skip all checks and upload anyway
    """
    tmp_file = None
    try:
        # get type of collection:
        collection_type = collection_type
        filename = file.filename or "unknown"
        ext = Path(filename).suffix.lower()
        validate_extensions(ext, collection_type)
        tmp_file = await save_upload_to_tmp(file)
        logger.info(f"collection_type : {collection_type}")
        if collection_type == CollectionType.FAQ:
            kb_dir = FAQ_ROOT/kb_id
        else: 
            kb_dir = KB_ROOT/kb_id
        kb_dir.mkdir(parents=True, exist_ok=True)
        final_file_path = kb_dir/filename
        shutil.copy(tmp_file, final_file_path)
        # Read file content
        # Compute SHA256 hash of the file content
        source_hash = hashlib.sha256(tmp_file.read_bytes()).hexdigest()
        loader = DocumentLoaderFAQ(
            documents_dir=kb_dir, 
            service_dir=Path("app"),
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )
        documents, _, docs_count, points_count = loader.prepare_docs_texts(
            map_true=(collection_type=="faq"),
            index_answers=False,
            user_id=user_id,
            kb_id=kb_id,
            filepath=str(final_file_path.resolve())
        )

        if not documents:
            raise HTTPException(400, "FAQ preprocessing failed No files")
                
        hashes = [d["meta"].get("doc_hash") for d in documents if d.get("meta", {}).get("doc_hash")]

        if upload_mode == "check":
            duplicates = qdrant_service.check_duplicates(kb_id, hashes)
            if duplicates:
                return JSONResponse(
                    status_code=409,
                    content={
                        "conflict_type": "duplicate",
                        "duplicates": duplicates[:10],
                        "message": "Duplicate content detected"
                    }
                )

        # 4. replace / keep-both логика (для KB)
        if collection_type == "kb":
            existing = qdrant_service.check_filename_exists(kb_id, filename)

            if existing and upload_mode == "replace":
                qdrant_service.delete_document(existing["document_id"])

            elif existing and upload_mode == "keep-both":
                max_version = qdrant_service.get_max_version(kb_id, filename)
                for d in documents:
                    d["meta"]["version"] = max_version + 1

        # 5. загрузка в Qdrant 
        qdrant_service.upload_points_qdrant(documents, docs_count, points_count)    

        return JSONResponse({
            "success": True,
            "collection_type": collection_type,
            "kb_id": kb_id,
            "source_name": filename,
            "source_type": ext.lstrip("."),
            "document_count": docs_count,
            "points_count": points_count,
            "source_hash": source_hash,
            "message": "Document uploaded successfully",
            "document_id": documents[0]["meta"].get("document_id") if documents else None   
        })

    finally:
        if tmp_file:
            shutil.rmtree(tmp_file.parent, ignore_errors=True)

@app.get("/api/documents/{document_id}")
async def get_document(document_id: str):
    """Get all chunks for a specific document"""
    try:
        chunks = qdrant_service.get_document_chunks(document_id)
        if not chunks:
            raise HTTPException(status_code=404, detail="Document not found")
        
        # Format response to match expected structure
        formatted_chunks = []
        for chunk in chunks:
            formatted_chunks.append({
                "point_id": chunk["point_id"],
                "chunk_index": chunk["chunk_index"],
                "text": chunk["text"],
                "answer": chunk["answer"],
                "payload": chunk["metadata"]
            })
        
        return formatted_chunks
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/documents/{document_id}")
async def delete_document(document_id: str):
    """Delete a document and all its chunks"""
    try:
        success = qdrant_service.delete_document(document_id)
        if not success:
            raise HTTPException(status_code=404, detail="Document not found")
        return {"message": "Document deleted successfully", "document_id": document_id}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/search", response_model=List[SearchResult])
async def search_documents(request: SearchRequest):
    """Search documents by text query"""
    try:
        results = qdrant_service.search(
            query=request.query,
            limit=request.limit,
            filters=request.filters
        )
        return results
    except Exception as e:
        logger.info(f"[SEARCH ERROR], {e}")
        return []


@app.get("/api/collections/info")
async def collection_info():
    """Get collection information"""
    try:
        info = qdrant_service.get_collection_info()
        info['platform_version'] = PLATFORM_VERSION
        info['last_sync'] = sync_settings["last_sync"]
        info['next_sync'] = sync_settings['next_sync']
        return info
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/collections/refresh_metadata")
async def refresh_collection_metadata():
    """Пересчитать document_count в метаданных по фактическим данным коллекции"""
    try:
        result = qdrant_service.refresh_collection_metadata()
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/knowledge-bases")
async def list_knowledge_bases():
    """List all knowledge bases with their documents"""
    try:
        knowledge_bases = qdrant_service.list_knowledge_bases()
        return knowledge_bases
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/knowledge-bases/delete")
def delete_knowledge_base(req: DeleteKBRequest):
    try:
        qdrant_service.delete_kb(
            kb_id=req.kb_id,
            collection_name=req.collection_name
        )

        return {
            "status": "ok",
            "kb_id": req.kb_id
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/collections")
def get_collections():
    try: 
        info =qdrant_service.list_collections()
        return info
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to fetch collections: {str(e)}")

@app.post("/api/collections/switch")
def switch_collection(req: SwitchCollectionRequest):
    try:
        qdrant_service.switch_collection(
            req.collection_name,
            CollectionType(req.collection_type)
            )
        return {
            "status": "ok",
            "current_collection": qdrant_service.collection_name,
            "current_type": qdrant_service.collection_type
        }

    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.post("/api/collections/delete")
async def delete_collection(req: DeleteCollectionRequest):
    active = qdrant_service.get_active_collections()
    collection = req.collection
    if not collection:
        raise HTTPException(400, "collection required")
    for ctype, info in active.items():
        if info and info["collection"] == collection:
            raise HTTPException(400, f"Cannot delete active collection '{collection}' of type '{ctype}'")

    success = qdrant_service.collection_delete(collection)

    if not success:
        raise HTTPException(404, f"Collection '{collection}' not found")

    return {
        "success": True,
        "deleted_collection": collection
    }

@app.post("/api/collections/create")
async def create_collection(request: Request):
    payload = await request.json()

    version = payload.get("version")
    collection_type = payload.get("type", "faq")  # faq | kb
    collection_name = f"{collection_type}_collection_v{str(version)}"
    if not version:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "collection version required and type"}
        )

    try:
        created = qdrant_service.create_collection(
            collection_name = collection_name
        )
        return {
            "success": created,
            "collection": collection_name,
            "type": collection_type
        }
    except ValueError as e:
        return JSONResponse(
            status_code=409,
            content={"success": False, "error": str(e)}
        )

@app.get("/api/collections/active")
def get_active_collections():
    return qdrant_service.get_active_collections()

@app.post("/api/collections/switch-alias")
def switch_collection_alias(req: SwitchAliasRequest):
    try:
        qdrant_service.switch_alias(
            collection_name=req.collection_name,
            collection_type=req.collection_type
        )
        return {"status": "ok"}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))

@app.get("/api/collections/by-type")
def get_collections_by_type():
    collections = qdrant_service.qdrant_client.get_collections().collections

    result = {
        "faq": [],
        "kb": []
    }

    for c in collections:
        if c.name.startswith("faq_"):
            result["faq"].append(c.name)
        elif c.name.startswith("kb_"):
            result["kb"].append(c.name)

    return result

@app.get("/api/documents/download/{document_id}")
async def download_document(document_id: str):
    chunks = qdrant_service.get_document_chunks(document_id)
    if not chunks:
        raise HTTPException(404, "Document not found")

    raw_path = chunks[0]["metadata"].get("file_path")

    print("RAW PATH:", repr(raw_path))

    if not raw_path:
        raise HTTPException(404, "No file_path in metadata")

    file_path = Path(raw_path.strip())

    print("CHECKING:", file_path)
    print("EXISTS:", file_path.exists())

    if not file_path.exists():
        raise HTTPException(
            404,
            f"File not found on disk: {file_path}"
        )

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream"
    )

@app.post("/api/filesystem/sync")
async def filesystem_sync(
    kb_id: str = Form("01_Маркетинговые материалы"),
    collection_type: str = Form("kb")
):
    if collection_type == CollectionType.FAQ:
        faq_file_storage.sync(kb_id, collection_type)
    elif collection_type== CollectionType.DOCUMENTS:
        kb_file_storage.sync(kb_id, collection_type) 
    return {"status": "sync_completed"}

@app.get("/api/filesystem/folders")
async def get_folders():
    # meanwhile show only kb_tree 
    return kb_file_storage.build_tree()

@app.get("/api/filesystem/download")
async def download_filesystem_file(path: str):
    path = unquote(path)
    file_path = (KB_STORAGE_ROOT / path).resolve()

    # защита от выхода из root
    if not str(file_path).startswith(str(KB_STORAGE_ROOT)):
        raise HTTPException(403, "Invalid path")

    if not file_path.exists():
        raise HTTPException(404, "File not found")

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream"
    )

@app.post("/api/news/send")
async def send_news(data: dict):

    text = data.get("text")
    if not text or not text.strip():
        raise HTTPException(status_code=400, detail="Text is required")
    
    async with httpx.AsyncClient() as client:
        try:
            r = await client.post(
                BOT_API,
                json={"text": text},
                timeout=30
            )
            r.raise_for_status()

            return r.json()
        except httpx.HTTPError as e:
            logger.error(f"Broadcast failed: {e}")
            raise HTTPException(status_code=502, detail="Bot service unvailable")

@app.get("/api/prompts/list")
async def list_prompts():
    """Получить список всех файлов промптов"""
    try:
        prompts = []
        if PROMPTS_STORAGE_ROOT.exists():
            for file in PROMPTS_STORAGE_ROOT.iterdir():
                if file.is_file() and file.suffix == ".md":
                    stat = file.stat()
                    prompts.append({
                        "name": file.name,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "is_current": file.name == "agent_prompt.md",
                        "is_backup": file.name.startswith("agent_prompt_backup_")
                    })
        
        # Сортировка: текущий первый, потом бэкапы, потом остальные
        prompts.sort(key=lambda x: (
            not x["is_current"],  # текущий первым
            not x["is_backup"],   # потом не бэкапы
            x["modified"]         # по дате
        ))
        
        return {
            "current": "agent_prompt.md",
            "files": prompts
        }
    except Exception as e:
        logger.error(f"Error listing prompts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/prompts/current")
async def get_current_prompt():
    """Получить текущий промпт (agent_prompt.md)"""
    try:
        prompt_file = PROMPTS_STORAGE_ROOT / "agent_prompt.md"
        if not prompt_file.exists():
            raise HTTPException(status_code=404, detail="Current prompt not found")
        
        async with aiofiles.open(prompt_file, "r", encoding="utf-8") as f:
            content = await f.read()
        
        stat = prompt_file.stat()
        return {
            "name": "agent_prompt.md",
            "content": content,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        }
    except Exception as e:
        logger.error(f"Error reading current prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/prompts/file/{filename}")
async def get_prompt_file(filename: str):
    """Получить содержимое конкретного файла промпта"""
    try:
        # Защита от path traversal
        if ".." in filename or filename.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        prompt_file = PROMPTS_STORAGE_ROOT / filename
        if not prompt_file.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        async with aiofiles.open(prompt_file, "r", encoding="utf-8") as f:
            content = await f.read()
        
        stat = prompt_file.stat()
        return {
            "name": filename,
            "content": content,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        }
    except Exception as e:
        logger.error(f"Error reading prompt file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/prompts/backup")
async def create_prompt_backup():
    """Создать бэкап текущего промпта"""
    try:
        prompt_file = PROMPTS_STORAGE_ROOT / "agent_prompt.md"
        if not prompt_file.exists():
            raise HTTPException(status_code=404, detail="Current prompt not found")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"agent_prompt_backup_{timestamp}.md"
        backup_file = PROMPTS_STORAGE_ROOT / backup_name
        
        shutil.copy2(prompt_file, backup_file)
        logger.info(f"Created prompt backup: {backup_name}")
        
        return {
            "success": True,
            "backup_name": backup_name,
            "created_at": timestamp
        }
    except Exception as e:
        logger.error(f"Error creating backup: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/prompts/save")
async def save_prompt(data: dict):
    """Сохранить новый промпт с созданием бэкапа"""
    try:
        content = data.get("content")
        if not content:
            raise HTTPException(status_code=400, detail="Content is required")
        
        prompt_file = PROMPTS_STORAGE_ROOT / "agent_prompt.md"
        
        # 1. Создать бэкап если файл существует
        if prompt_file.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"agent_prompt_backup_{timestamp}.md"
            backup_file = PROMPTS_STORAGE_ROOT / backup_name
            shutil.copy2(prompt_file, backup_file)
            logger.info(f"Created backup before save: {backup_name}")
        
        # 2. Записать новый промпт
        async with aiofiles.open(prompt_file, "w", encoding="utf-8") as f:
            await f.write(content)
        
        logger.info("Prompt saved successfully")
        
        # 3. Уведомить adk-agent о перезагрузке (опционально)
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{ADK_AGENT_URL}/api/prompts/reload",
                    timeout=5
                )
                logger.info("Notified adk-agent to reload prompts")
        except Exception as e:
            logger.warning(f"Could not notify adk-agent: {e}")
        
        return {
            "success": True,
            "message": "Prompt saved successfully",
            "backup_created": True
        }
    except Exception as e:
        logger.error(f"Error saving prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/prompts/restore/{filename}")
async def restore_prompt(filename: str):
    """Восстановить промпт из бэкапа"""
    try:
        if ".." in filename or filename.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        backup_file = PROMPTS_STORAGE_ROOT / filename
        if not backup_file.exists():
            raise HTTPException(status_code=404, detail="Backup file not found")
        
        prompt_file = PROMPTS_STORAGE_ROOT / "agent_prompt.md"
        shutil.copy2(backup_file, prompt_file)
        
        logger.info(f"Restored prompt from backup: {filename}")
        
        # Уведомить adk-agent
        try:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{ADK_AGENT_URL}/api/prompts/reload",
                    timeout=5
                )
        except Exception as e:
            logger.warning(f"Could not notify adk-agent: {e}")
        
        return {
            "success": True,
            "message": f"Restored from {filename}",
            "restored_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error restoring prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/prompts/file/{filename}")
async def delete_prompt_file(filename: str):
    """Удалить файл бэкапа"""
    try:
        if ".." in filename or filename.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        if filename == "agent_prompt.md":
            raise HTTPException(status_code=400, detail="Cannot delete current prompt")
        
        backup_file = PROMPTS_STORAGE_ROOT / filename
        if not backup_file.exists():
            raise HTTPException(status_code=404, detail="File not found")
        
        backup_file.unlink()
        logger.info(f"Deleted prompt file: {filename}")
        
        return {
            "success": True,
            "message": f"Deleted {filename}"
        }
    except Exception as e:
        logger.error(f"Error deleting file: {e}")
        raise HTTPException(status_code=500, detail=str(e))

#  endpoint to reload agent
@app.post("/api/prompts/reload-agent")
async def reload_agent_prompt():
    """Отправить команду перезагрузки промпта в adk-agent"""
    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(
                f"{ADK_AGENT_URL}/api/prompts/reload",
                timeout=10
            )
            r.raise_for_status()
            return r.json()
    except httpx.HTTPError as e:
        logger.error(f"Failed to reload agent prompt: {e}")
        raise HTTPException(status_code=502, detail="Agent service unavailable")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)

