from fastapi import FastAPI, UploadFile, File, HTTPException, Form, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, PlainTextResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware
from typing import List, Optional
from dotenv import load_dotenv
from app.services.qdrant_service import QdrantService, CollectionType
from app.models import (
    DocumentInfo, SearchRequest, SearchResult, SwitchCollectionRequest, 
    DeleteCollectionRequest, DeleteKBRequest, SwitchAliasRequest, SyncInterval
    )
from app.utils.preprocessors.document_loader import DocumentLoader as DocumentLoaderFAQ
import hashlib, os, uuid, shutil, asyncio, aiofiles
from contextlib import asynccontextmanager
from app.utils.logger import setup_logger
from app.services.file_storage_service import FileStorageService
from pathlib import Path
import httpx, mimetypes
from urllib.parse import unquote, quote
from datetime import datetime, timedelta, timezone
# Auth dependencies
from jose import JWTError, jwt
from fastapi import Depends
import asyncpg
from passlib.context import CryptContext
import pandas as pd
# простая токенизация
from collections import Counter
import re
import pymorphy3
import csv
import io

load_dotenv()

# Используем современный Lifespan вместо @app.on_event("startup")
@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize Qdrant collection on startup"""
    global http_client
    app.state.db_pool = await asyncpg.create_pool(
        dsn=DATABASE_URL,
        min_size=5,
        max_size=20
    )
    # инициализация базы данных для пользователей UI
    await init_db(app.state.db_pool)
    # создаем глобальный http клиент для всех запросов, чтобы не создавать новый каждый раз
    http_client = httpx.AsyncClient(timeout=60)
    # создаем коллекцию в qdrant 
    qdrant_service.ensure_collection()
    # создаем все необходимые коллекции, чтобы не было проблем с переключением между ними
    qdrant_service.ensure_collections()
    storage = get_current_storage()
    # строим дерево кэш изначально
    storage.build_tree()
    # создаем фоновые задачи
    # делаем синхронизацию при старте
    sync_task = asyncio.create_task(run_sync_all_safe())
    # запускаем расписание переиндексации
    scheduler_task = asyncio.create_task(start_scheduler())
    yield
    sync_task.cancel()
    scheduler_task.cancel()
    await http_client.aclose()
    await app.state.db_pool.close()

app = FastAPI(
    title="UI Manager for Anastasia",
    lifespan=lifespan
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
# обертка для проверки разрешений по ролям
@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    public_paths = [
        "/api/login",
        "/api/refresh",
        "/static",
        "/"
    ]

    path = request.url.path

    # публичные
    if any(path.startswith(p) for p in public_paths):
        return await call_next(request)

    access_token = request.cookies.get("access_token")
    payload = decode_token(access_token) if access_token else None
    # 1. если access_token валиден
    if payload and payload.get("type") == "access":
        role = payload.get("role")

        if not is_allowed(path, role):
            return JSONResponse(status_code=403, content={"detail": "Forbidden"})

        return await call_next(request)
    # 2. если access умер → пробуем refresh
    refresh_token = request.cookies.get("refresh_token")
    if refresh_token:
        refresh_payload = decode_token(refresh_token)

        if refresh_payload and refresh_payload.get("type") == "refresh":
            username = refresh_payload.get("sub")

            # берём роль из БД
            user = await get_user_from_db(username, request.app.state.db_pool)

            if user:
                new_access = create_access_token({
                    "sub": username,
                    "role": user["role"]
                })
                response = await call_next(request)

                # обновляем access_token
                response.set_cookie(
                    key="access_token",
                    value=new_access,
                    httponly=True,
                    samesite="lax"
                )

                return response
        
    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})

# AUTH config
SECRET_KEY = os.getenv("SECRET_KEY", "super-secret-key-change-this")
ALGORITHM = "HS256"
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://aszh-bot:aszh-bot@postgres:5432/aszh-bot")
ACCESS_TOKEN_EXPIRE_MINUTES = 60
REFRESH_TOKEN_EXPIRE_HOURS = 5
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
# доступные эндпоинты для ролей
ROLE_PERMISSIONS = {
    "admin": ["*"],

    "manager": [
        "/api/documents",
        "/api/search",
        "/api/knowledge-bases",
        "/api/filesystem",
        "/api/news",
        "/api/user-groups"
    ]
}
# Инициализация пользователей, паролей и ролей для UI
TELEGRAM_BOT_API = os.getenv("BOT_TELEGRAM_API", "http://bot:8001")
MAX_BOT_API = os.getenv("BOT_MAX_API", "http://bot-max:8002")
PROMPTS_STORAGE_ROOT = Path(os.getenv("PROMPTS_STORAGE_ROOT", "/app/data/prompts"))
PROMPTS_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
# Путь к файлу стартового сообщения бота
BOT_START_MESSAGE_FILE = Path("/app/data/bot/settings/bot_start_message.md")
# папка загрузки файлов
BOT_UPLOAD_DIR = Path("/app/data/bot/upload")

logger = setup_logger(name="Test", service_dir="App")

# Initialize services
KB_STORAGE_ROOT= Path(os.getenv("KB_STORAGE_ROOT", "/data/kb_documents"))
KB_STORAGE_ROOT = KB_STORAGE_ROOT.resolve()
KB_STORAGE_ROOT.mkdir(parents=True, exist_ok=True)
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
# storage services для разных индексов, для kb, faq и других
COLLECTIONS_CFG = {
    os.getenv("ACTIVE_DOCUMENTS_COLLECTION", "kb_collection"): {
        "dir": os.getenv("ACTIVE_DOCUMENTS_PATH","kb"),
        "type": CollectionType.DOCUMENTS,
        "ext": SUPPORTED_KB_EXTENSIONS,
        "sync_type": "kb"
    },
    os.getenv("ARCHIVE_DOCUMENTS_COLLECTION", "archive_kb_collection"): {
        "dir": os.getenv("ARCHIVE_DOCUMENTS_PATH","archive_kb"),
        "type": CollectionType.DOCUMENTS,
        "ext": SUPPORTED_KB_EXTENSIONS,
        "sync_type": "kb"
    },
    os.getenv("KB_DOCUMENTS_COLLECTION", "knowledge_base_collection"): {
        "dir": os.getenv("KB_DOCUMENTS_PATH","knowledge_base"),
        "type": CollectionType.DOCUMENTS,
        "ext": SUPPORTED_KB_EXTENSIONS,
        "sync_type": "kb"
    },
    os.getenv("FAQ_COLLECTION_NAME", "faq_collection"): {
        "dir": os.getenv("FAQ_DOCUMENTS_PATH","faq"),
        "type": CollectionType.FAQ, 
        "ext": SUPPORTED_FAQ_EXTENSIONS,
        "sync_type": "faq"
    }
}
file_storages = {}
collections_config_for_qdrant = {}
# Инициализация путей и подготовка конфига для Qdrant в одном цикле
for name, cfg in COLLECTIONS_CFG.items():
    root_path = KB_STORAGE_ROOT / cfg["dir"]
    root_path.mkdir(parents=True, exist_ok=True)
    # Сохраняем абсолютный путь прямо в конфиг
    cfg["root_path"] = root_path
    collections_config_for_qdrant[name] = cfg["type"]

# Инициализация QdrantService
qdrant_service = QdrantService(
    collection_name=collection_name, # Дефолтная коллекция
    embedding_api_base=embedding_api_base,
    embedding_api_key=embedding_api_key,
    embedding_model=embedding_model,
    embedding_dimensions=embedding_dimensions,
    chunk_size=chunk_size,
    chunk_overlap=chunk_overlap,
    qdrant_host=QDRANT_HOST,
    qdrant_port=QDRANT_PORT,
    collections_config=collections_config_for_qdrant
)

# Инициализация всех FileStorageService без дублирования
for name, cfg in COLLECTIONS_CFG.items():
    file_storages[name] = FileStorageService(
        root_path=cfg["root_path"],
        qdrant_service=qdrant_service,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        service_dir=Path("app"),
        ext_allowed=cfg["ext"]
    )

sync_lock = asyncio.Lock()
sync_update_event = asyncio.Event()
sync_settings = {
    "interval_hours": 3,
    "interval_seconds": None,
    "last_sync": None,
    "next_sync":None,
    "running": False
}
# очередь событий
subscribers = []
# глобальный http клиент для всех запросов, чтобы не создавать новый каждый раз
http_client: httpx.AsyncClient = None
# создаем анализатора морфем
morph = pymorphy3.MorphAnalyzer() 

# Mount static files
static_path = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=str(static_path)), name="static")
# функция для получения текущего сервиса хранилища
def get_current_storage() -> FileStorageService:
    """Возвращает сервис хранилища для текущей активной коллекции Qdrant"""
    # current_name = qdrant_service.collection_name
    current_name = collection_name
    
    if current_name not in file_storages:
        raise ValueError(f"Storage for collection '{current_name}' not initialized!")
        
    return file_storages[current_name]

def get_interval_delta():
    """функция вычисления интервала между синхронизациями"""
    if sync_settings.get("interval_seconds"):
        return timedelta(seconds=sync_settings["interval_seconds"])
    return timedelta(hours=sync_settings["interval_hours"])
# создаем функцию очереди событий чтобы отслеживать автоматические обновления
async def event_generator():
    queue = asyncio.Queue()
    subscribers.append(queue)
    try:
        while True:
            data = await queue.get()
            yield f"data: {data}\n\n"
    finally:
        subscribers.remove(queue)

async def save_upload_to_tmp(file: UploadFile) -> Path:
    """Сохранение во временные файлы"""
    upload_id = uuid.uuid4().hex
    tmp_dir = Path("/tmp/uploads") / upload_id 
    tmp_dir.mkdir(parents=True, exist_ok=True)    
    tmp_file = tmp_dir / (file.filename or "unknown")
    # Читаем и пишем асинхронно, не блокируя основной поток
    async with aiofiles.open(tmp_file, 'wb') as out_file:
        while content := await file.read(1024 * 1024): # Читаем чанками по 1МБ
            await out_file.write(content)
    return tmp_file

def validate_extensions(ext: str, collection_type: str):
    """Проверка поддерживания расширения для индексации"""
    allowed = SUPPORTED_FAQ_EXTENSIONS if collection_type=="faq" else SUPPORTED_KB_EXTENSIONS
    if ext not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported {collection_type.upper()} format: {ext}"
            f"\n Supported formats are: {', '.join(allowed)}"
        )
##################################
# Работа с синхронихацией
##################################
@app.get("/api/filesystem/sync_events")
async def sync_events():
    return StreamingResponse(event_generator(), media_type="text/event-stream")

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
        # проходим по всем папкам переданного storager для построения индекса
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
    """безопасная синхронизация, чтобы нельзя было несколько вызвать одновременно"""
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
            logger.info("[SYNC] finished")
            storage = get_current_storage()
            storage.build_tree()
            for q in subscribers:
                await q.put("sync_completed")

    return {"status": "completed"}

async def run_sync_all_once():
    """
    функция для правильного вызова синхронизации от типа, 
    в дальнейшем можно добавить другие типы коллекций если надо
    """
    
    original_collection = qdrant_service.collection_name
    original_type = qdrant_service.collection_type

    for collection_name, cfg in COLLECTIONS_CFG.items():
        logger.info(f"[SYNC] Processing {collection_name}")
        root = cfg["root_path"]
        storager = file_storages[collection_name]

        logger.info(f"[SYNC] {collection_name} from {root}")
        # 1. переключаемся на коллекцию
        qdrant_service.switch_collection(
            collection_name,
            cfg["type"]
        )

        # 2. удаление отсутствующих KB
        disk_kb_ids = {
            folder.name for folder in root.iterdir() if folder.is_dir()
        }

        qdrant_kbs = qdrant_service.list_knowledge_bases()
        qdrant_kb_ids = {kb["kb_id"] for kb in qdrant_kbs}

        deleted_kbs = qdrant_kb_ids - disk_kb_ids

        for kb_id in deleted_kbs:
            logger.info(f"[SYNC] KB DELETED: {kb_id}")
            try:
                qdrant_service.delete_kb(
                    kb_id=kb_id,
                    collection_name=collection_name
                )
            except Exception as e:
                logger.error(f"[SYNC] delete error {kb_id}: {e}")

        # 3. синхронизация существующих папок
        await sync_function(root, storager, cfg["sync_type"])

    # вернуть состояние
    qdrant_service.switch_collection(original_collection, original_type)

    return {"status": "success", "message": "SYNC completed"}          

async def start_scheduler():
    # запускаем расписание автоматической синхроонизации
    await asyncio.sleep(10)
    await auto_sync()

async def auto_sync():
    """Функция автоматической синхронизации по времени"""
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
    """получить текущие настройки синхронизации"""
    return sync_settings

@app.post("/api/sync/settings")
async def set_sync_settings(data: SyncInterval):
    """Установить новые настройки синхронизации"""
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

@app.post("/api/filesystem/sync")
async def filesystem_sync(
    kb_id: str = Form("01_Маркетинговые материалы"),
    collection_name: str = Form("kb_collection")
):
    """синхронизация для kb отдельно, чтобы не делать для всех"""
    logger.info(f"[SYNC ONE] kb_id={kb_id}, collection={collection_name}")
    if collection_name not in COLLECTIONS_CFG:
        return {"status": "error", "message": f"Collection '{collection_name}' not found"}
    cfg = COLLECTIONS_CFG[collection_name]
    storager = file_storages.get(collection_name)
    if not storager:
        return {"status": "error", "message": f"Storage for {collection_name} is not initialized"}
    try: 
        # Обязательно переключаем Qdrant на нужную коллекцию перед синхронизацией!
        # Это критично, так как storager внутри себя обращается к qdrant_service
        qdrant_service.switch_collection(collection_name, cfg["type"])
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(
            None,
            lambda: storager.sync(
                kb_id=kb_id,
                # Передаем "kb" или "faq" из конфига, чтобы storager понимал логику парсинга
                collection_type=cfg["sync_type"]
                )
            )

    except Exception as e:
        logger.error(f"[SYNC KB] Error: {e}")
        return {"status": "error", "message": str(e)}

    return {"status": "sync_completed"}
##################################
# DATABASE & AUTH utils
##################################
def get_users_from_env() -> list[tuple[str, str, str]]:
    """
    Парсит переменную окружения UI_USERS_DATA.
    Возвращает список кортежей (username, password, role).
    """
    raw_data = os.getenv("UI_USERS_DATA", "")
    
    if not raw_data:
        logger.warning("UI_USERS_DATA is empty. Using default admin.")
        return [('admin', 'admin123', 'admin')]

    users = []
    # Разбиваем строку по запятой на отдельных юзеров
    for entry in raw_data.split(","):
        parts = entry.strip().split(":")
        
        if len(parts) == 3:
            users.append(tuple(parts))
        else:
            logger.error(f"Invalid user format in ENV: {entry}. Expected user:pass:role")
            
    return users

def hash_password(password: str) -> str:
    """Хеширование пароля"""
    return pwd_context.hash(password)

def verify_password(password: str, hashed: str) -> bool:
    """Проверка пароля с нормализацией"""
    return pwd_context.verify(password, hashed)

async def init_db(pool: asyncpg.Pool):
    """Инициализация базы данных"""
    logger.info("Initializing database...")
    # Получаем список пользователей динамически
    users_to_init = get_users_from_env()
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS ui_users (
                id SERIAL PRIMARY KEY,
                username TEXT UNIQUE NOT NULL,
                password TEXT NOT NULL,
                role TEXT NOT NULL
            );
        """)
        for username, password, role in users_to_init:
            hashed = hash_password(password)
            await conn.execute("""
                INSERT INTO ui_users (username, password, role)
                VALUES ($1, $2, $3)
                ON CONFLICT (username) DO NOTHING;
            """, username, hashed, role)
            
    logger.info(f"Database initialized with {len(users_to_init)} users.")

async def get_user_from_db(username: str, pool: asyncpg.Pool):
    """Получение пользователя из базы данных по имени"""
    async with pool.acquire() as conn:

        user = await conn.fetchrow(
            "SELECT username, password, role FROM ui_users WHERE username=$1",
            username
        )

    return dict(user) if user else None

def _generate_jwt(data: dict, expires_delta: timedelta, token_type: str) -> str:
    """Внутренняя база для создания любых токенов"""
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + expires_delta
    to_encode.update({"exp": expire, "type": token_type})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def create_access_token(data: dict):
    """Создание JWT access токена с типом и временем жизни"""
    return _generate_jwt(data, timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES), "access")

def create_refresh_token(data: dict):
    """Создание JWT refresh токена с типом и временем жизни"""
    return _generate_jwt(data, timedelta(hours=REFRESH_TOKEN_EXPIRE_HOURS), "refresh")

def decode_token(token: str):
    """Декодирование JWT токена и проверка его типа"""
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        return payload
    except JWTError:
        return None

def get_current_user(request: Request):
    """Получение текущего пользователя из access токена в cookies"""
    token = request.cookies.get("access_token")

    if not token:
        raise HTTPException(status_code=401, detail="Unauthorized")

    payload = decode_token(token)

    if not payload or payload.get("type") != "access":
        raise HTTPException(status_code=401, detail="Unauthorized")

    return {
        "username": payload.get("sub"),
        "role": payload.get("role")
    }

def is_allowed(path: str, role: str) -> bool:
    """Проверка разрешений по роли для доступа к пути"""
    allowed_paths = ROLE_PERMISSIONS.get(role, [])

    # admin — всё можно
    if "*" in allowed_paths:
        return True

    # проверяем prefix match
    return any(path.startswith(p) for p in allowed_paths)

##################################
# Авторизация и главная
##################################
@app.post("/api/login")
async def login(request: Request, username: str = Form(...), password: str = Form(...)):
    """Эндпоинт для логина. Принимает username и password, проверяет их и возвращает JWT токены в cookies"""
    user = await get_user_from_db(username, request.app.state.db_pool)

    if not user or not verify_password(password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")

    access_token = create_access_token({
        "sub": username,
        "role": user["role"]
    })

    refresh_token = create_refresh_token({
        "sub": username
    })

    response = JSONResponse({"success": True})
    for k, v in [("access_token", access_token), ("refresh_token", refresh_token)]:
        response.set_cookie(key=k, value=v, httponly=True, samesite="lax")

    return response

@app.post("/api/refresh")
async def refresh(request: Request):
    """Эндпоинт для обновления access токена с помощью refresh токена"""
    refresh_token = request.cookies.get("refresh_token")

    if not refresh_token:
        raise HTTPException(status_code=401)

    payload = decode_token(refresh_token)

    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=401)

    username = payload.get("sub")

    user = await get_user_from_db(username, request.app.state.db_pool)

    if not user:
        raise HTTPException(status_code=401)

    new_access = create_access_token({
        "sub": username,
        "role": user["role"]
    })

    response = JSONResponse({"success": True})

    response.set_cookie(
        key="access_token",
        value=new_access, 
        httponly=True,
        samesite="lax"
    )
    return response

@app.post("/api/logout")
async def logout():
    """Эндпоинт для логаута. Удаляет токены из cookies"""
    response = JSONResponse({"success": True})
    response.delete_cookie("access_token")
    response.delete_cookie("refresh_token")

    return response
@app.get("/api/me")
async def me(user=Depends(get_current_user)):
    """Эндпоинт для получения информации о текущем пользователе"""
    return user

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main UI"""
    with open(static_path / "index.html") as f:
        return f.read()

@app.get("/api/health")
async def health():
    """Health check endpoint"""
    try:
        qdrant_service.qdrant_client.get_collections()
        is_alive = True 
        return {
            "status": "healthy" if is_alive else "unhealthy",
            "qdrant_url": qdrant_url,
            "collection": collection_name
        }
    except Exception as e:
         raise HTTPException(status_code=503, detail=f"Qdrant connection failed: {e}")
    
##################################
# Работа с документами
##################################

@app.get("/api/documents", response_model=List[DocumentInfo])
async def list_documents():
    """List all documents in the collection"""
    try:
        documents = qdrant_service.list_documents()
        return documents
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/documents/upload")
async def upload_document(
    file: UploadFile = File(...),
    kb_id: str = Form("default"),
    user_id: str = Form("anonymous"),
    upload_mode: str = Form("check"),  # check, replace, keep-both, force
    collection_type: str = Form("faq"),  # faq or kb
    collection_name: str = Form("kb_collection")
):
    """Upload and process a document
    
    upload_mode:
    - check: Check for duplicates and conflicts (default)
    - replace: Replace existing file with same name
    - keep-both: Keep both versions with incremented version number
    - force: Skip all checks and upload anyway
    """
    # Валидация коллекции
    if collection_name not in COLLECTIONS_CFG:
        raise HTTPException(400, f"Collection '{collection_name}' not found")
    
    # get type of collection:
    cfg = COLLECTIONS_CFG[collection_name]
    filename = file.filename or "unknown"
    ext = Path(filename).suffix.lower()
    validate_extensions(ext, collection_type)    
    tmp_file = None
    # Переключаем Qdrant на нужную коллекцию (КРИТИЧЕСКИ ВАЖНО)
    # Делаем это в самом начале, чтобы все последующие запросы к Qdrant шли в правильный индекс
    qdrant_service.switch_collection(collection_name, cfg["type"])
    try:
        tmp_file = await save_upload_to_tmp(file)
        logger.info(f"collection_type : {collection_type}")
        kb_dir = cfg["root_path"] / kb_id
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
    """удаление kb из qdrant"""
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

@app.get("/api/documents/download/{document_id}")
async def download_document(document_id: str):
    """скачивание документа по id"""
    qdrant_service.switch_collection(collection_name, CollectionType.DOCUMENTS)
    chunks = qdrant_service.get_document_chunks(document_id)
    if not chunks:
        raise HTTPException(404, "Document not found")

    raw_path = chunks[0]["metadata"].get("file_path")

    if not raw_path:
        raise HTTPException(404, "No file_path in metadata")

    file_path = Path(raw_path.strip())
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

##################################
# Работа с коллекциями
##################################

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

@app.get("/api/collections")
def get_collections():
    """получение коллекций"""
    try: 
        info =qdrant_service.list_collections()
        return info
    except Exception as e:
        raise HTTPException(
            status_code=500, 
            detail=f"Failed to fetch collections: {str(e)}")

@app.post("/api/collections/switch")
def switch_collection(req: SwitchCollectionRequest):
    """переключение между коллекциями"""
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
    """Удаление коллекции"""
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
    """endpoint для создания коллекций"""
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
    """получение активных коллекций"""
    return qdrant_service.get_active_collections()

@app.post("/api/collections/switch-alias")
def switch_collection_alias(req: SwitchAliasRequest):
    """переключение алиаса между коллекциями"""
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
    """получение коллекций по типам"""
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
        elif c.name.startswith("archive_kb_"):
            result["kb"].append(c.name)
        elif c.name.startswith("knowledge_base_"):
            result["kb"].append(c.name)
    return result

##################################
# Работа с файловой системой
##################################

@app.get("/api/filesystem/folders")
async def get_folders():
    """Строит дерево файлов, для бота Анастасии"""
    # meanwhile show only kb_tree 
    storage = get_current_storage()
    return storage.build_tree()

@app.get("/api/filesystem/node")
async def get_node(path: str = "", collection_name: str="kb_collection"):
    """строим узлы дерева чтобы ускорить отработку"""
    # проверяем, есть ли такая коллекция в конфиге
    if collection_name not in COLLECTIONS_CFG:
        return {"error": f"Collection '{collection_name}' not found"}
        
    # Достаем сохраненный при инициализации root_path
    base = COLLECTIONS_CFG[collection_name]["root_path"]
    target = (base / path).resolve()

    # защита
    if not str(target).startswith(str(base.resolve())):
        return {"error": "invalid path"}

    result = {
        "folders": [],
        "files": []
    }

    for item in target.iterdir():
        if item.is_dir():
            result["folders"].append(item.name)
        else:
            result["files"].append(item.name)

    return result

@app.get("/api/filesystem/download")
async def download_filesystem_file(path: str):
    """Скачивает файл из нашего источника, пока только для kb коллекции документов"""
    path = unquote(path)
    # Получаем имя текущей активной коллекции
    # current_collection = qdrant_service.collection_name
    current_collection = collection_name
    if current_collection not in COLLECTIONS_CFG:
        return {"error": f"Collection '{current_collection}' not found in config"}
    root_path = COLLECTIONS_CFG[current_collection]["root_path"]
    file_path = (root_path / path).resolve()

    # защита от выхода из root
    if not str(file_path).startswith(str(root_path)):
        raise HTTPException(403, "Invalid path")

    if not file_path.exists():
        raise HTTPException(404, "File not found")

    return FileResponse(
        path=str(file_path),
        filename=file_path.name,
        media_type="application/octet-stream"
    )

##################################
# Работа с новостями
##################################

@app.get("/api/news")
async def get_news():
    """Получить все новости из бота"""
    resp = await http_client.get(f"{TELEGRAM_BOT_API}/api/news")
    resp.raise_for_status()
    return resp.json()

@app.get("/api/local-file-news")
async def get_news_file(name: str):
    """Отправка файла"""
    try:
        filename = Path(name).name
        file_path = BOT_UPLOAD_DIR / filename
        # собираем реальный путь
        logger.info(f"Путь который вышел: {file_path}")
        if not file_path.exists():
            raise HTTPException(404, f"File not found: {file_path}")

        content_type, _ = mimetypes.guess_type(file_path)
        text_extensions = {'.md', '.txt', '.json', '.csv', '.xml', '.html', '.htm'}
        is_text_file = file_path.suffix.lower() in text_extensions
        if is_text_file or (content_type and content_type.startswith("text")):
            try:
                async with aiofiles.open(file_path, mode='r', encoding='utf-8') as f:
                    content = await f.read()
            except UnicodeDecodeError:
                # Пробуем другие кодировки для кириллицы
                for enc in ["cp1251", "utf-8-sig", "koi8-r"]:
                    try:
                        async with aiofiles.open(file_path, mode='r', encoding=enc) as f:
                            content = await f.read()
                        logger.info(f"File read with encoding: {enc}")
                        break
                    except UnicodeDecodeError:
                        continue
                else:
                    content = file_path.read_text(encoding="utf-8", errors="replace")

            # НЕТ Content-Disposition - браузер покажет текст, а не скачает
            return PlainTextResponse(
                content, 
                media_type="text/plain; charset=utf-8"
            )

        # Для PDF и изображений - FileResponse с inline (показывает в модалке)
        if content_type and (
            content_type.startswith("image") or 
            content_type == "application/pdf"
        ):
            return FileResponse(
                path=str(file_path),
                media_type=content_type,
                headers={
                    "Content-Disposition": f"inline; filename*=UTF-8''{quote(filename)}"
                }
            )
        
        return FileResponse(
            path=str(file_path),
            media_type=content_type or "application/octet-stream",
            filename=filename.encode('utf-8').decode('latin-1'),
            headers={
                "Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"
            }
        )

    except Exception as e:
        logger.error(f"Проблема говорит: {e}")
        raise HTTPException(500, str(e))

@app.get("/api/news/{news_id}")
async def get_news_by_id(news_id: int):
    """Получить новость по ID из бота"""
    resp = await http_client.get(f"{TELEGRAM_BOT_API}/api/news/{news_id}")
    resp.raise_for_status()
    return resp.json()

@app.delete("/api/news/{news_id}")
async def delete_news(news_id: int):
    """удалить новость из истории бд"""
    resp = await http_client.delete(f"{TELEGRAM_BOT_API}/api/news/{news_id}")
    resp.raise_for_status()
    return resp.json()

@app.post("/api/news/send")
async def send_news(
    html: str = Form(...),
    files: List[UploadFile] = File(default=[]),
    schedule_time: Optional[str] = Form(None),
    reuse_file_path: Optional[str] = Form(None), 
    target_group: str = Form("all")
):
    """Отправить новость одновременно на Telegram и MAX ботов.
    Возвращает комбинированный результат."""
    try:
        multipart_data = []
        # откладываем время 
        if schedule_time:
            multipart_data.append(("schedule_time", (None, schedule_time)))
            logger.info(f"Отложено на: {schedule_time}")
        else: 
            logger.info("Без отложенной отправки")
        multipart_data.append(("html", (None, html)))
        logger.info(f"HTML length: {len(html)}")
        # группа получателей
        multipart_data.append(("target_group", (None, target_group)))
        logger.info(f"Группа получателей: {target_group}")
        # переиспользование старого файла
        if reuse_file_path:
            logger.info(f"Reusing file from path: {reuse_file_path}")
            multipart_data.append(("reuse_file_path", (None, reuse_file_path)))
        
        # файлы
        # Читаем файлы в память один раз, чтобы переиспользовать для обоих запросов
        file_cache = {}
        if not reuse_file_path:
            for f in files:
                content = await f.read()
                multipart_data.append(
                    ("files", (f.filename, content, f.content_type))
                )
                file_cache[f.filename] = content  # кэшируем для повторного использования
        else:
            logger.info("Using reused file, skipping new upload")
        
        # 2. Внутренняя функция для отправки в конкретного бота
        async def send_to_bot(name: str, url: str, news_id=None):
            try:
                # Создаём новый multipart для каждого запроса, 
                # чтобы избежать проблем с повторным чтением стримов
                request_data = []
                for item in multipart_data:
                    if item[0] == "files":
                        # Восстанавливаем content из кэша
                        filename = item[1][0]
                        content = file_cache.get(filename) or await files[0].read()
                        request_data.append(
                            ("files", (filename, content, item[1][2]))
                        )
                    else:
                        request_data.append(item)
                # передаём news_id
                if news_id:
                    request_data.append(("news_id", (None, str(news_id))))

                resp = await http_client.post(
                    f"{url}/broadcast",
                    files=request_data
                )
                resp.raise_for_status()
                return name, resp.json()
            except Exception as e:
                logger.error(f"{name.upper()} send error: {e}")
                return name, {"status": "error", "error": str(e)}

        # Telegram создаёт новость
        tg_name, tg_result = await send_to_bot("telegram", TELEGRAM_BOT_API)

        if tg_result.get("status") != "ok":
            raise Exception("Telegram failed — не создаём новость")

        news_id = tg_result.get("news_id")

        # MAX использует уже созданную
        max_name, max_result = await send_to_bot("max", MAX_BOT_API, news_id=news_id)
        # 4. Сбор и форматирование результата
        final_response = {
            "status": "ok",
            "results": {},
            "sent": 0,
            "timestamp": datetime.now(timezone.utc).isoformat()
        }
        results_map = {
            tg_name: tg_result,
            max_name: max_result
        }
        
        for bot_name, bot_result in results_map.items():
            if isinstance(bot_result, Exception):
                # Обработка исключений из gather
                logger.error(f"⚠️ Exception in gather: {bot_result}")
                continue
            final_response["results"][bot_name] = bot_result
            
            if isinstance(bot_result, dict) and bot_result.get("status") == "ok":
                final_response["sent"] += bot_result.get("sent", 0)
                logger.info(f"✅ {bot_name.upper()}: {bot_result.get('sent', 0)} пользователей")
            else:
                logger.warning(f"⚠️ {bot_name.upper()}: {bot_result}")

        # 5. Логирование итогового результата
        logger.info(f"📊 Итого отправлено: {final_response['sent']} пользователей")
        return final_response
    except Exception as e:
        logger.error(f"News send error: {e}")
        raise HTTPException(500, str(e))

###############################
# Работа с промптами ADK Agent 
###############################

@app.get("/api/prompts/list")
async def list_prompts(agent: str):
    """Получить список всех файлов промптов"""
    try: 
        agent_path = PROMPTS_STORAGE_ROOT/agent
        if not agent_path.exists():
            raise HTTPException(status_code=404, detail="Agent not found")
        prompts = []
        for file in agent_path.iterdir():
            if file.is_file() and file.suffix == ".md":
                stat = file.stat()
                prompts.append({
                    "name": file.name,
                    "size": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "is_current": file.name == f"{agent}_agent_prompt.md",
                    "is_backup": file.name.startswith(f"{agent}_agent_prompt_backup_")
                })
        prompts.sort(key=lambda x: (
            not x["is_current"],
            not x["is_backup"],
            x["modified"]
        ))

        return {
            "current": f"{agent}_agent_prompt.md",
            "files": prompts
        }
    except Exception as e:
        logger.error(f"Error listing prompts: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/prompts/current")
async def get_current_prompt(agent: str):
    """Получить текущий промпт (f'{agent}_agent_prompt.md')"""
    try:
        prompt_file = PROMPTS_STORAGE_ROOT / agent / f"{agent}_agent_prompt.md"
        if not prompt_file.exists():
            raise HTTPException(status_code=404, detail="Current prompt not found")
        
        async with aiofiles.open(prompt_file, "r", encoding="utf-8") as f:
            content = await f.read()
        
        stat = prompt_file.stat()
        return {
            "name": f"{agent}_agent_prompt.md",
            "content": content,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        }
    except Exception as e:
        logger.error(f"Error reading current prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/prompts/file/{filename}")
async def get_prompt_file(filename: str, agent: str):
    """Получить содержимое конкретного файла промпта"""
    try:
        # Защита от path traversal
        if ".." in filename or filename.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        prompt_file = PROMPTS_STORAGE_ROOT / agent / filename
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
async def create_prompt_backup(agent: str):
    """Создать бэкап текущего промпта"""
    try:
        prompt_file = PROMPTS_STORAGE_ROOT/ agent / f"{agent}_agent_prompt.md"
        if not prompt_file.exists():
            raise HTTPException(status_code=404, detail="Current prompt not found")
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{agent}_agent_prompt_backup_{timestamp}.md"
        backup_file = PROMPTS_STORAGE_ROOT / agent / backup_name
        
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
        agent = data.get("agent")
        if not content:
            raise HTTPException(status_code=400, detail="Content is required")
        
        prompt_file = PROMPTS_STORAGE_ROOT / agent / f"{agent}_agent_prompt.md"
        
        # 1. Создать бэкап если файл существует
        if prompt_file.exists():
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup_name = f"{agent}_agent_prompt_backup_{timestamp}.md"
            backup_file = PROMPTS_STORAGE_ROOT / agent / backup_name
            shutil.copy2(prompt_file, backup_file)
            logger.info(f"Created backup before save: {backup_name}")
        
        # 2. Записать новый промпт
        async with aiofiles.open(prompt_file, "w", encoding="utf-8") as f:
            await f.write(content)
        
        logger.info("Prompt saved successfully")
        
        return {
            "success": True,
            "message": "Prompt saved successfully",
            "backup_created": True
        }
    except Exception as e:
        logger.error(f"Error saving prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/prompts/restore/{filename}")
async def restore_prompt(filename: str, agent: str):
    """Восстановить промпт из бэкапа"""
    try:
        if ".." in filename or filename.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        backup_file = PROMPTS_STORAGE_ROOT / agent / filename
        if not backup_file.exists():
            raise HTTPException(status_code=404, detail="Backup file not found")
        
        prompt_file = PROMPTS_STORAGE_ROOT / agent / f"{agent}_agent_prompt.md"
        shutil.copy2(backup_file, prompt_file)
        
        logger.info(f"Restored prompt from backup: {filename}")
        
        return {
            "success": True,
            "message": f"Restored from {filename}",
            "restored_at": datetime.now().isoformat()
        }
    except Exception as e:
        logger.error(f"Error restoring prompt: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.delete("/api/prompts/file/{filename}")
async def delete_prompt_file(filename: str, agent: str):
    """Удалить файл бэкапа"""
    try:
        if ".." in filename or filename.startswith("/"):
            raise HTTPException(status_code=400, detail="Invalid filename")
        
        if filename == f"{agent}_agent_prompt.md":
            raise HTTPException(status_code=400, detail="Cannot delete current prompt")
        
        backup_file = PROMPTS_STORAGE_ROOT / agent / filename
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

@app.get("/api/prompts/agents")
async def get_agents():
    """Получаем агентов какие у нас есть"""
    try:
        agents = [
            f.name for f in PROMPTS_STORAGE_ROOT.iterdir()
            if f.is_dir()
        ]
        return agents
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

############################
# Работа с настройками бота 
############################
@app.get("/api/prompts/bot-start")
async def get_bot_start_message():
    """Получить текущее стартовое сообщение бота"""
    try:
        if not BOT_START_MESSAGE_FILE.exists():
            # Создаём файл с дефолтным сообщением если не существует
            default_message = "👋 Привет! Я ваш помощник.\n\nЧем могу помочь?"
            async with aiofiles.open(BOT_START_MESSAGE_FILE, "w", encoding="utf-8") as f:
                await f.write(default_message)
            return {
                "success": True,
                "content": default_message,
                "exists": False,
                "size": len(default_message),
                "modified": datetime.now().isoformat()
            }
        async with aiofiles.open(BOT_START_MESSAGE_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
            stat = BOT_START_MESSAGE_FILE.stat()
        
        return {
            "success": True,
            "content": content,
            "exists": True,
            "size": stat.st_size,
            "modified": datetime.fromtimestamp(stat.st_mtime).isoformat()
        }
    except Exception as e:
        logger.error(f"Error reading bot start message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/prompts/bot-start")
async def save_bot_start_message(data: dict):
    """Сохранить стартовое сообщение бота"""
    try:
        content = data.get("content")
        if not content:
            raise HTTPException(status_code=400, detail="Content is required")
        
        # Сохраняем в файл
        async with aiofiles.open(BOT_START_MESSAGE_FILE, "w", encoding="utf-8") as f:
                await f.write(content)
        
        logger.info(f"Bot start message saved ({len(content)} symbols)")
        bot_reload_success = False
        try:
            r = await http_client.post(f"{TELEGRAM_BOT_API}/api/reload-start-message", timeout=5)
            if r.status_code == 200:
                bot_reload_success = True
                logger.info("Bot notified to reload start message")
        except Exception as e:
            logger.warning(f"Could not notify bot: {e}")

        return {
            "success": True,
            "message": "Bot start message saved successfully",
            "length": len(content),
            "bot_notified": bot_reload_success
        }
    except Exception as e:
        logger.error(f"Error saving bot start message: {e}")
        raise HTTPException(status_code=500, detail=str(e))

##################################
# Работа с группами пользователей
##################################

@app.get("/api/subscribers")
async def get_subscribers():
    """Получить всех подписчиков с информацией о группах"""
    try:
        resp = await http_client.get(f"{TELEGRAM_BOT_API}/api/subscribers")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Error getting subscribers: {e}")
        raise HTTPException(500, f"Failed to get subscribers: {str(e)}")
    
@app.post("/api/subscribers/group")
async def update_subscriber_group(data: dict):
    """Обновить группу пользователя"""
    try:
        user_id = int(data.get("user_id"))
        group = data.get("group")
        value = bool(data.get("value"))
        
        if group not in ("manager_group", "coach_group"):
            raise HTTPException(400, "Invalid group. Must be 'manager_group' or 'coach_group'")
        
        resp = await http_client.post(
            f"{TELEGRAM_BOT_API}/api/subscribers/group",
            json={
                "user_id": user_id,
                "group": group,
                "value": value
            }
        )
        resp.raise_for_status()
        return resp.json()
            
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error updating subscriber group: {e}")
        raise HTTPException(500, f"Failed to update group: {str(e)}")

@app.get("/api/subscribers/export")
async def export_subscribers(
    search: str = None,
    group: str = None
):
    """
    Экспорт пользователей с фильтрами:
    - search: строка поиска
    - group: manager | coach | both | none | all
    """

    try:
        resp = await http_client.get(f"{TELEGRAM_BOT_API}/api/subscribers")
        resp.raise_for_status()
        users = resp.json()
    except Exception as e:
        logger.error(f"Error fetching subscribers: {e}")
        raise HTTPException(500, "Failed to fetch subscribers")

    # 🔍 фильтрация
    def matches(u):
        # поиск
        if search:
            q = search.lower()
            if not (
                str(u.get("user_id", "")).lower().find(q) != -1 or
                (u.get("username") or "").lower().find(q) != -1 or
                (u.get("first_name") or "").lower().find(q) != -1 or
                (u.get("last_name") or "").lower().find(q) != -1
            ):
                return False
        # фильтр группы
        if group == "manager":
            return u.get("manager_group")
        elif group == "coach":
            return u.get("coach_group")
        elif group == "both":
            return u.get("manager_group") and u.get("coach_group")
        elif group == "none":
            return not u.get("manager_group") and not u.get("coach_group")

        return True

    filtered = [u for u in users if matches(u)]

    # 📄 формируем CSV
    output = io.StringIO()
    writer = csv.writer(output)

    writer.writerow([
        "User ID",
        "Username",
        "First Name",
        "Last Name",
        "Manager",
        "Coach",
        "Last Seen"
    ])
    for u in filtered:
        writer.writerow([
            u.get("user_id"),
            u.get("username"),
            u.get("first_name"),
            u.get("last_name"),
            "Yes" if u.get("manager_group") else "No",
            "Yes" if u.get("coach_group") else "No",
            u.get("last_seen")
        ])

    return Response(
        content=output.getvalue(),
        media_type="text/csv",
        headers={
            "Content-Disposition": "attachment; filename=subscribers.csv"
        }
    )
            
##################################
# Работа с логами для мониторинга
##################################
# логирование событий в ui для аналитики
@app.get("/api/events")
async def get_events(request: Request):
    limit = int(request.query_params.get("limit", 100))
    offset = int(request.query_params.get("offset", 0))
    # фильтры для поиска по логам
    filters = {
        "user_id": request.query_params.get("user_id"),
        "user_name": request.query_params.get("user_name"),
        "event_type": request.query_params.get("event_type"),
        "channel": request.query_params.get("channel"),
        "created_at": request.query_params.get("created_at"),
        "payload": request.query_params.get("payload"),
    }

    conditions = []
    values = []
    i = 1

    for key, value in filters.items():
        if value:
            if key == "payload":
                conditions.append(f"payload::text ILIKE ${i}")
                values.append(f"%{value}%")
            elif key == "created_at":
                # Пытаемся распарсить дату
                try:
                    dt = datetime.fromisoformat(value)

                    # обрезаем секунды
                    dt_from = dt.replace(second=0, microsecond=0)
                    dt_to = dt_from + timedelta(minutes=1)
                    # фильтр по дню (±1 день)
                    conditions.append(f"created_at >= ${i} AND created_at < ${i+1}")
                    values.append(dt_from)
                    values.append(dt_to)
                    i += 1  # +1 дополнительный параметр

                except:
                    # fallback — если не смогли распарсить
                    conditions.append(f"created_at::text ILIKE ${i}")
                    values.append(f"%{value}%")    
            else:
                conditions.append(f"{key} ILIKE ${i}")
                values.append(f"%{value}%")
            i += 1

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    query = f"""
        SELECT user_id, user_name, event_type, channel, payload, created_at
        FROM events
        {where_clause}
        ORDER BY created_at DESC
        LIMIT ${i} OFFSET ${i+1}
    """

    values.extend([limit, offset])

    async with request.app.state.db_pool.acquire() as conn:
        rows = await conn.fetch(query, *values)

    return [
        {
            "user_id": r["user_id"],
            "user_name": r["user_name"],
            "event_type": r["event_type"],
            "channel": r["channel"],
            "payload": r["payload"],
            "created_at": r["created_at"].isoformat()
        }
        for r in rows
    ]
# экспорт событий для аналитики за период
@app.get("/api/analytics/export")
async def export(request: Request, from_ts: Optional[str]=None, to_ts: Optional[str]=None):
    """Экспорт событий за период в excel для аналитики"""
    if from_ts:
        from_dt = datetime.fromisoformat(from_ts)
    else:
        from_dt = datetime(1970, 1, 1, tzinfo=timezone.utc)

    if to_ts:
        to_dt = datetime.fromisoformat(to_ts)
    else:
        to_dt = datetime.now(timezone.utc)
    pool = request.app.state.db_pool

    query = """
    SELECT *
    FROM events
    WHERE created_at BETWEEN $1 AND $2
    LIMIT 10000
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_dt, to_dt)
    # формируем dataframe
    df = pd.DataFrame([dict(r) for r in rows])
    #  фиксим проблему со временем в pandas убирая timezone
    for col in df.columns:
        if str(df[col].dtype).startswith("datetime64[ns,"):
            df[col] = df[col].dt.tz_convert(None)
    file_path = "/tmp/analytics_logs.xlsx"
    df.to_excel(file_path, index=False)
    # сохраняем обязательно с media_type для корректной выгрузки файла
    return FileResponse(
        file_path,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename="analytics_logs.xlsx"
    )

####################
# Работа аналитики
####################
# пользователи которые взаимодействовали с документом
@app.get("/api/analytics/document-users")
async def document_users(filename: str, from_ts: str, to_ts: str, request: Request):
    """получаем пользователей скачавших документ"""
    pool = request.app.state.db_pool
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)
    query = """
    SELECT
        user_id,
        MAX(user_name) as user_name,
        payload->>'source' as source,
        COUNT(*) as downloads
    FROM events
    WHERE event_type IN ('document_download', 'document_download_menu')

    AND COALESCE(
            payload->>'filename',
            split_part(payload->>'file_path', '/', array_length(string_to_array(payload->>'file_path', '/'), 1))
        ) = $1

    AND user_id IS NOT NULL
    AND created_at BETWEEN $2 AND $3

    GROUP BY user_id, source
    ORDER BY downloads DESC
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, filename, from_ts, to_ts)

    return [dict(r) for r in rows]
# аналитика активности самых активных часов и дней
@app.get("/api/analytics/activity")
async def activity(from_ts: str, to_ts: str, request: Request):
    """Получить активность по часам и дням недели для выявления пиковых периодов использования"""
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)
    pool = request.app.state.db_pool

    query = """
    SELECT
        EXTRACT(HOUR FROM created_at) as hour,
        EXTRACT(DOW FROM created_at) as day,
        COUNT(*) as messages
    FROM events
    WHERE event_type = 'message_received'
      AND created_at BETWEEN $1 AND $2
    GROUP BY hour, day
    ORDER BY day, hour
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_ts, to_ts)

    return [dict(r) for r in rows]
# облако популярных слов
@app.get("/api/analytics/top-words")
async def top_words(from_ts: str, to_ts: str, request: Request):
    """строим облако наиболее частых слов в запросах"""
    pool = request.app.state.db_pool
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)
    query = """
    SELECT payload->>'text' as text
    FROM events
    WHERE event_type = 'message_received'
      AND payload ? 'text'
      AND created_at BETWEEN $1 AND $2
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_ts, to_ts)
    #  извлекаем текст
    texts = [r["text"] for r in rows if r["text"]]
    # извлекаем слова из текста
    words = []
    for t in texts:
        words += re.findall(r'\b\w+\b', t.lower())

    # убираем мусор
    stopwords = {"и", "в", "на", "что", "как", "а", "с", "по", 
                 "это", "файл", "документ", "скачать"}
    clean_words = []
    for w in words:
        if len(w) < 3 or w in stopwords:
            continue
        # производим лематизацию
        lemma = morph.parse(w)[0].normal_form
        clean_words.append(lemma)
    # считаем слова
    counter = Counter(clean_words)

    top = counter.most_common(50)

    return [{"text": w, "value": c} for w, c in top]
# аналитика фраз топ
@app.get("/api/analytics/top-phrases")
async def top_phrases(from_ts: str, to_ts: str, request: Request):
    pool = request.app.state.db_pool

    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)

    query = """
    SELECT payload->>'text' as text
    FROM events
    WHERE event_type = 'message_received'
      AND payload ? 'text'
      AND created_at BETWEEN $1 AND $2
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_ts, to_ts)
    # извлекаем текст
    texts = [r["text"] for r in rows if r["text"]]
    # прописываем стоп слова
    stopwords = {
        "и", "в", "на", "что", "как", "а", "с", "по",
        "это", "файл", "документ", "скачать"
    }
    # извлекаем фразы 
    all_phrases = []
    for t in texts:
        # находим слова
        words = re.findall(r'\b\w+\b', t.lower())

        # чистка + лемматизация
        clean = []
        for w in words:
            if len(w) < 3 or w in stopwords:
                continue
            lemma = morph.parse(w)[0].normal_form
            clean.append(lemma)

        # ===== биграммы =====
        for i in range(len(clean) - 1):
            phrase = f"{clean[i]} {clean[i+1]}"
            all_phrases.append(phrase)

        # ===== триграммы =====
        for i in range(len(clean) - 2):
            phrase = f"{clean[i]} {clean[i+1]} {clean[i+2]}"
            all_phrases.append(phrase)
    # считаем число повторений
    counter = Counter(all_phrases)

    top = counter.most_common(50)

    return [{"text": p, "value": c} for p, c in top]
# статистика по пользователям
@app.get("/api/analytics/stats")
async def get_stats(from_ts: str, to_ts: str, request: Request):
    """Получить общую статистику по пользователям, сообщениям и времени ответа за период"""
    pool = request.app.state.db_pool
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)
    
    query = """
    WITH user_messages AS (
        SELECT user_id, COUNT(*) as msg_count
        FROM events
        WHERE event_type = 'message_received'
        AND user_id IS NOT NULL
        AND created_at BETWEEN $1 AND $2
        GROUP BY user_id
    ),

    response_times AS (
        SELECT (payload->>'response_time_ms')::int as rt
        FROM events
        WHERE event_type = 'response'
        AND payload ? 'response_time_ms'
        AND created_at BETWEEN $1 AND $2
    ),

    totals AS (
        SELECT
            COUNT(DISTINCT user_id) as unique_users,
            COUNT(*) as total_messages
        FROM events
        WHERE event_type IN ('message_received', 'response')
        AND user_id IS NOT NULL
        AND created_at BETWEEN $1 AND $2
    )

    SELECT
        t.unique_users,
        t.total_messages,

        -- сообщения
        (SELECT AVG(msg_count) FROM user_messages) as avg_messages_per_user,
        (SELECT MAX(msg_count) FROM user_messages) as max_messages_per_user,

        -- response time
        (SELECT AVG(rt) FROM response_times) as avg_response_time,
        (SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY rt) FROM response_times) as median_response_time

    FROM totals t
    """

    async with pool.acquire() as conn:
        row = await conn.fetchrow(query, from_ts, to_ts)

    return dict(row)
# аналитика по самым активным каналам
@app.get("/api/analytics/channels")
async def channels(from_ts: str, to_ts: str, request: Request):
    """получение информации о самых активных каналах"""
    pool = request.app.state.db_pool
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)
    
    query = """
    SELECT
        channel,
        COUNT(*) as messages
    FROM events
    WHERE event_type = 'message_received'
      AND channel IS NOT NULL
      AND created_at BETWEEN $1 AND $2
    GROUP BY channel
    ORDER BY messages DESC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_ts, to_ts)

    return [dict(r) for r in rows]

# аналитика по топу пользователей
@app.get("/api/analytics/top-users")
async def top_users(from_ts: str, to_ts: str, request: Request):
    """получаем наиболее активных пользователей с их статистикой"""
    pool = request.app.state.db_pool
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)
    query = """
    WITH period_messages AS (
        SELECT user_id, MAX(user_name) as user_name,
          COUNT(*) as messages
        FROM events
        WHERE event_type = 'message_received'
          AND user_id IS NOT NULL
          AND created_at BETWEEN $1 AND $2
        GROUP BY user_id
    ),

    weekly_messages AS (
        SELECT user_id, COUNT(*) as weekly_messages
        FROM events
        WHERE event_type = 'message_received'
          AND user_id IS NOT NULL
          AND created_at > NOW() - INTERVAL '7 days'
        GROUP BY user_id
    )

    SELECT
        p.user_id,
        p.user_name,
        p.messages,
        COALESCE(w.weekly_messages / 7.0, 0) as avg_weekly_messages
    FROM period_messages p
    LEFT JOIN weekly_messages w ON p.user_id = w.user_id
    ORDER BY p.messages DESC
    LIMIT 10
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_ts, to_ts)

    return [dict(r) for r in rows]

# получение самых востребованных документов
@app.get("/api/analytics/top-documents")
async def top_documents(from_ts: str, to_ts: str, request: Request):
    """функция отображения самых популярных документов"""
    pool = request.app.state.db_pool
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)
    query = """
    SELECT
        payload->>'doc_id' as doc_id,

        COALESCE(
            payload->>'filename',
            split_part(payload->>'file_path', '/', array_length(string_to_array(payload->>'file_path', '/'), 1))
        ) as file_name,

        -- берем ЛЮБОЙ file_path (например MAX)
        MAX(payload->>'file_path') as file_path,

        COUNT(*) as total_downloads,

        COUNT(*) FILTER (WHERE payload->>'source' = 'search') as search_downloads,
        COUNT(*) FILTER (WHERE payload->>'source' = 'menu') as menu_downloads

    FROM events
    WHERE event_type IN ('document_download', 'document_download_menu')
    AND created_at BETWEEN $1 AND $2

    GROUP BY doc_id, file_name
    ORDER BY total_downloads DESC
    LIMIT 20
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_ts, to_ts)

    return [dict(r) for r in rows]

# получаем наиболее востребованные способы работы с документами (поиск или меню)
@app.get("/api/analytics/documents-sources")
async def document_sources(from_ts: str, to_ts: str, request: Request):
    """Получаем источники поиска (меню или поиск)"""
    pool = request.app.state.db_pool
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)

    query = """
    SELECT
        payload->>'source' as source,
        COUNT(*) as downloads
    FROM events
    WHERE event_type IN ('document_download', 'document_download_menu')
      AND created_at BETWEEN $1 AND $2
    GROUP BY source
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_ts, to_ts)

    return [dict(r) for r in rows]

####################
# Работа с диалогами
####################
# выгрузка диалогов
@app.get("/api/analytics/export-dialogs")
async def export_dialogs(from_ts: str, to_ts: str, request: Request):
    """Экспорт диалогов за период в excel для аналитики"""
    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)
    pool = request.app.state.db_pool

    query = """
    WITH messages AS (
        SELECT
            payload->>'turn_id' as turn_id,
            session_id,
            user_name,
            channel,
            created_at as message_time,
            payload->>'text' as message
        FROM events
        WHERE event_type = 'message_received'
        AND created_at BETWEEN $1 AND $2
    ),

    responses AS (
        SELECT DISTINCT ON (payload->>'turn_id')
            payload->>'turn_id' as turn_id,
            payload->>'text' as response,
            (payload->>'response_time_ms')::int as response_time_ms
        FROM events
        WHERE event_type = 'response'
        ORDER BY payload->>'turn_id', created_at DESC
    ),

    downloads_turn AS (
        -- файлы внутри конкретного запроса
        SELECT
            payload->>'turn_id' as turn_id,
            STRING_AGG(payload->>'file_path', ' | ' ORDER BY created_at) as files
        FROM events
        WHERE event_type IN ('document_download', 'document_download_menu')
          AND payload->>'file_path' IS NOT NULL
        GROUP BY payload->>'turn_id'
    ),

    downloads_session AS (
        -- ВСЕ файлы пользователя (как в старом SQL, но без дублей)
        SELECT
            session_id,
            STRING_AGG(
                DISTINCT payload->>'file_path',
                ', ' ORDER BY payload->>'file_path'
            ) as all_files
        FROM events
        WHERE event_type IN ('document_download', 'document_download_menu')
          AND created_at BETWEEN $1 AND $2
          AND payload->>'file_path' IS NOT NULL
        GROUP BY session_id
    ),

    msg_counts AS (
        SELECT session_id, COUNT(*) as msg_count
        FROM events
        WHERE event_type = 'message_received'
        AND created_at BETWEEN $1 AND $2
        GROUP BY session_id
    )

    SELECT
        m.session_id,
        m.user_name,
        (m.message_time + INTERVAL '3 hour') as message_time,
        m.message,

        CASE
            WHEN r.response IS NOT NULL THEN r.response
            WHEN dt.files IS NOT NULL THEN dt.files
            ELSE ''
        END as response,

        r.response_time_ms,

        mc.msg_count,
        m.channel,

        ds.all_files as downloaded_files

    FROM messages m

    LEFT JOIN responses r ON r.turn_id = m.turn_id
    LEFT JOIN downloads_turn dt ON dt.turn_id = m.turn_id
    LEFT JOIN downloads_session ds ON ds.session_id = m.session_id
    LEFT JOIN msg_counts mc ON mc.session_id = m.session_id

    ORDER BY m.message_time
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, from_ts, to_ts)

    df = pd.DataFrame([dict(r) for r in rows])
    # убираем timezone из datetime колонок
    for col in df.columns:
        if str(df[col].dtype).startswith("datetime64[ns,"):
            df[col] = df[col].dt.tz_localize(None)
    # форматируем время ответа в понятный вид измерений
    if "response_time_ms" in df.columns:
        # Заполнить нулями Nan
        df["response_time_sec"] = (df["response_time_ms"].fillna(0) / 1000).round(2)
        df.drop(columns=["response_time_ms"], inplace=True)
    
    # оптимизируем названия в выгрузке
    def clean_file_names(files):
        if not files:
            return ""
        return " | ".join(f.split("/")[-1] for f in files.split(" | "))

    def clean_files_pipe(files):
        """для response (| разделитель)"""
        if not files:
            return ""
        return " | ".join(f.split("/")[-1] for f in files.split(" | "))

    def clean_files_csv(files):
        """для downloaded_files (, разделитель)"""
        if not files:
            return ""
        return ", ".join(f.split("/")[-1] for f in files.split(", "))

    # response (может быть текст или файлы)
    df["response"] = df["response"].fillna("").apply(
        lambda x: clean_files_pipe(x) if "|" in x else clean_file_names(x)
    )

    # все скачанные файлы
    df["downloaded_files"] = df["downloaded_files"].fillna("").apply(clean_files_csv)
    file_path = "/tmp/dialogs.xlsx"
    df.to_excel(file_path, index=False)
    # сохраняем обязательно с media_type для корректной выгрузки файла
    return FileResponse(
            file_path,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            filename="dialogs.xlsx"
    )

# просмотр диалогов по всем пользователям
@app.get("/api/analytics/dialogs")
async def get_dialogs(
    request: Request,
    from_ts: str,
    to_ts: str,
    user: str = None,
    text: str = None
):
    pool = request.app.state.db_pool

    from_dt = datetime.fromisoformat(from_ts)
    to_dt = datetime.fromisoformat(to_ts)

    query = """
    WITH turns AS (
        SELECT
            payload->>'turn_id' as turn_id,
            user_id,
            MAX(user_name) as user_name,
            MIN(created_at) as message_time
        FROM events
        WHERE payload->>'turn_id' IS NOT NULL
        AND created_at BETWEEN $1 AND $2
        GROUP BY payload->>'turn_id', user_id
    ),

    messages AS (
        SELECT DISTINCT ON (payload->>'turn_id')
            payload->>'turn_id' as turn_id,
            payload->>'text' as message
        FROM events
        WHERE event_type = 'message_received'
        ORDER BY payload->>'turn_id', created_at ASC
    ),

    responses AS (
        SELECT DISTINCT ON (payload->>'turn_id')
            payload->>'turn_id' as turn_id,
            payload->>'text' as response,
            (payload->>'response_time_ms')::int as response_time
        FROM events
        WHERE event_type = 'response'
        ORDER BY payload->>'turn_id', created_at DESC
    ),

    docs AS (
        SELECT
            payload->>'turn_id' as turn_id,
            STRING_AGG(payload->>'file_path', '||') as file_paths
        FROM events
        WHERE event_type IN ('document_download', 'document_download_menu')
        GROUP BY payload->>'turn_id'
    )

    SELECT
        t.user_id,
        COALESCE(t.user_name, 'Аноним') as user_name,
        t.message_time,
        m.message,
        r.response,
        r.response_time,
        d.file_paths

    FROM turns t
    LEFT JOIN messages m ON m.turn_id = t.turn_id
    LEFT JOIN responses r ON r.turn_id = t.turn_id
    LEFT JOIN docs d ON d.turn_id = t.turn_id

    WHERE t.message_time BETWEEN $1 AND $2
    """

    params = [from_dt, to_dt]
    # фильтр по пользователям
    if user:
        # Поиск по ID или имени
        query += f" AND (CAST(t.user_id AS TEXT) ILIKE ${len(params)+1} OR t.user_name ILIKE ${len(params)+1})"
        params.append(f"%{user}%")
    # фильтр по тексту
    if text:
        query += f" AND m.message ILIKE ${len(params)+1}"
        params.append(f"%{text}%")

    query += " ORDER BY t.message_time DESC LIMIT 500"

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    return [dict(r) for r in rows]
# просмотр диалогов по каждому пользователю
@app.get("/api/analytics/user-dialogs")
async def user_dialogs(user_id: str, from_ts: str, to_ts: str, request: Request):
    pool = request.app.state.db_pool

    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)

    query = """
    SELECT
        m.created_at as message_time,
        m.payload->>'text' as message,

        r.payload->>'text' as response,
        (r.payload->>'response_time_ms')::int as response_time,

        d.payload->>'file_path' as file_path

    FROM events m

    LEFT JOIN events r
      ON m.payload->>'turn_id' = r.payload->>'turn_id'
     AND r.event_type = 'response'

    LEFT JOIN events d
      ON m.session_id = d.session_id
     AND d.event_type IN ('document_download', 'document_download_menu')
     AND d.created_at >= m.created_at
     AND d.created_at <= m.created_at + INTERVAL '10 seconds'

    WHERE m.event_type = 'message_received'
      AND m.user_id = $1
      AND m.created_at BETWEEN $2 AND $3

    ORDER BY m.created_at
    LIMIT 500
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, user_id, from_ts, to_ts)

    return [dict(r) for r in rows]

# выгрузка диалога по пользователю
@app.get("/api/analytics/export-user-dialogs")
async def export_user_dialogs(user_id: str, from_ts: str, to_ts: str, request: Request):
    pool = request.app.state.db_pool

    from_ts = datetime.fromisoformat(from_ts)
    to_ts = datetime.fromisoformat(to_ts)

    query = """
    SELECT
        m.created_at,
        m.payload->>'text' as message,
        r.payload->>'text' as response
    FROM events m
    LEFT JOIN events r
      ON m.payload->>'turn_id' = r.payload->>'turn_id'
     AND r.event_type = 'response'
    WHERE m.event_type = 'message_received'
      AND m.user_id = $1
      AND m.created_at BETWEEN $2 AND $3
    ORDER BY m.created_at
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, user_id, from_ts, to_ts)
    # экспорт диалогов делаем просто в виде текста для одного диалога
    lines = []
    for r in rows:
        lines.append(f"[{r['created_at']}] USER: {r['message']}")
        lines.append(f"[{r['created_at']}] BOT: {r['response']}")
        lines.append("")

    content = "\n".join(lines)

    return Response(
        content,
        media_type="text/plain",
        headers={"Content-Disposition": f"attachment; filename=dialogs_{user_id}.txt"}
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)

