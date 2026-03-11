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
    DeleteCollectionRequest, DeleteKBRequest, SwitchAliasRequest
    )
from app.utils.preprocessors.document_loader import DocumentLoader as DocumentLoaderFAQ
import hashlib, os, uuid, shutil, asyncio
from app.utils.logger import setup_logger
from app.services.file_storage_service import FileStorageService
from pathlib import Path
import httpx
from urllib.parse import unquote

BOT_API = "http://bot:8001/broadcast"

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
    service_dir=Path("app")
)

# Mount static files
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")


@app.on_event("startup")
async def startup_event():
    """Initialize Qdrant collection on startup"""
    qdrant_service.ensure_collection()
    asyncio.create_task(auto_sync())


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

async def auto_sync():
    while True:
        await run_sync_all_once()
        await asyncio.sleep(86400)

@app.post("/api/filesystem/sync_all")
async def manual_sync_all():
    """
    Эндпоинт для ручной синхронизации по кнопке.
    Вызывает ту же логику, но один раз и сразу возвращает ответ.
    """
    result = await run_sync_all_once()
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
            "documents_count": docs_count,
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
        # raise HTTPException(status_code=500, detail=str(e))


@app.get("/api/collections/info")
async def collection_info():
    """Get collection information"""
    try:
        info = qdrant_service.get_collection_info()
        return info
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

    async with httpx.AsyncClient() as client:

        r = await client.post(
            BOT_API,
            json={"text": text},
            timeout=20
        )

    return r.json()

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)

