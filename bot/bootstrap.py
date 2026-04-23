import os
from dotenv import load_dotenv
# Загружаем переменные окружения ДО импорта setup_logger
load_dotenv(override=True)
from utils import setup_logger
from utils.document_handler import DocumentHandler
#  импортируем вынесенные классы для работы с базами данных
from bot.services.database import PostgresChatStore, SubscriberStore, NewsStore, AdkApiClient
# импортируем конфиг
from bot.services.config import Settings

# Настройка логгера
logger = setup_logger('bot', 'bot.log')

async def init_services():
    dsn = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or "").strip()
    if not dsn:
        logger.error("DATABASE_URL отсутствует в .env")
        raise RuntimeError("DATABASE_URL (or POSTGRES_DSN) is missing in .env")

    adk_base = os.getenv("ADK_API_BASE", "http://agent:8000").strip()
    adk_app = os.getenv("ADK_APP_NAME", "agent").strip()

    # Конфигурация для DocumentHandler
    kb_manager_token = os.getenv("KB_MANAGER_TOKEN", "").strip() or None
    downloads_dir = os.getenv("DOWNLOADS_DIR", "./downloads").strip()

    logger.info(f"Конфигурация:")
    logger.info(f"  ADK Base: {adk_base}")
    logger.info(f"  ADK App: {adk_app}")
    logger.info(f"  KB Manager: {Settings.KB_MANAGER_URL}")
    logger.info(f"  Downloads: {downloads_dir}")
    logger.info(f"  Database: {dsn.split('@')[1] if '@' in dsn else 'configured'}")

    store = PostgresChatStore(dsn=dsn, max_turns=30)
    await store.connect()

    # инициализируем хранилище пользователей
    subscriber_store = SubscriberStore(store.pool)
    # инициализируем хранилище новостей
    news_store = NewsStore(store.pool)

    adk = AdkApiClient(base_url=adk_base, app_name=adk_app)
    await adk.open()

    # Инициализация DocumentHandler
    doc_handler = DocumentHandler(
        kb_manager_url=Settings.KB_MANAGER_URL,
        kb_manager_token=kb_manager_token,
        downloads_dir=downloads_dir
    )

    return store, subscriber_store, news_store, adk, doc_handler