import asyncio
from dotenv import load_dotenv
from typing import Optional
# Загружаем переменные окружения ДО импорта setup_logger
load_dotenv(override=True)
from utils import setup_logger
from bot.services.utils import run_http_server
from maxapi import Bot, Dispatcher
from maxapi.types import BotCommand
import os
from utils.event_logger import EventLogger
from utils.document_handler import DocumentHandler
#  импортируем вынесенные классы для работы с базами данных
from bot.services.database import PostgresChatStore, SubscriberStore, NewsStore, AdkApiClient

from bot.services.config import Settings
#  импортируем функции вспомогательные для бота
from bot.services.broadcast import create_broadcast_app, news_scheduler
from bot.services.handlers import register_handlers

from bot.services.user_resolver import UserResolver

# Настройка логирования
logger = setup_logger('bot', 'bot.log')
TITLE_START = """
👋 Привет! Я интерактивный чат-бот базы знаний компании.

📁 Выбери интересующий тебя раздел или напиши что тебя интересует сообщением.
"""
TITLE_HELP = """
ℹ️ Я помогу найти информацию в базе знаний.\n\n
Просто напиши свой вопрос, и я постараюсь найти ответ!\n\n
Команды:\n
/start — начать работу\n
/reset — сбросить историю\n
/help — эта справка
"""

class BotHolder:
    def __init__(self):
        self.instance: Optional[Bot] = None
# инициализация команд бота только для макса
async def setup_bot_commands(bot):
    """Регистрация команд в меню бота"""
    try:
        await bot.set_my_commands(
            BotCommand(name="start", description="Показать файлы"),
            BotCommand(name="help", description="помощь | справка"),
            BotCommand(name="reset", description="сбросить диалог"),
        )
        logger.info("Команды бота зарегистрированы")
    except Exception as e:
        logger.warning(f"Ошибка регистрации команд: {e}")

def load_bot_start_message():
    """Load start message from file"""
    global TITLE_START
    try:
        if Settings.BOT_START_MESSAGE_FILE.exists():
            TITLE_START = Settings.BOT_START_MESSAGE_FILE.read_text(encoding="utf-8")
            logger.info(f"Start message loaded from file: {len(TITLE_START)} symbols")
        else:
            logger.warning(f"Start file not found using standard")
    except Exception as e:
        logger.error(f"Error loading starting message: {e}")

def load_bot_help_message():
    """Load help message from file"""
    global TITLE_HELP
    try:
        if Settings.BOT_HELP_MESSAGE_FILE.exists():
            TITLE_HELP = Settings.BOT_HELP_MESSAGE_FILE.read_text(encoding="utf-8")
            logger.info(f"Help message loaded from file: {len(TITLE_HELP)} symbols")
        else:
            logger.warning(f"Help file not found using standard")
    except Exception as e:
        logger.error(f"Error loading help message: {e}")

# загрузка стартового сообщения
load_bot_start_message()
load_bot_help_message()

def get_start_message():
    """Получение стартового сообщения"""
    return TITLE_START

def get_help_message():
    """Получение справочного сообщения"""
    return TITLE_HELP
# =========================
# RUN
# =========================
async def main():
    logger.info("=" * 60)
    logger.info("Запуск max бота")
    logger.info("=" * 60)
    # logger событий
    eventlogger = EventLogger()
    await eventlogger.init()
    max_token = os.getenv("MAX_BOT_TOKEN", "enter_your_token").strip()
    if not max_token:
        logger.error("MAX_BOT_TOKEN отсутствует в .env")
        raise RuntimeError("MAX_BOT_TOKEN is missing in .env")
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
    # обработчик пользователя для унификации по номеру телефона
    user_resolver = UserResolver(store.pool)
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
    #  регистрируем диспетчер для обработки сообщений бота
    dp = Dispatcher()
    # регистрация обработчиков сообщений и команд бота
    register_handlers(
        dp=dp,
        store=store,
        subscriber_store=subscriber_store,
        user_resolver=user_resolver,
        adk=adk,
        doc_handler=doc_handler,
        get_start_message=get_start_message,
        get_help_message=get_help_message,
        platform="max"
    )
    logger.info("Все компоненты инициализированы")
    bot_holder = BotHolder()
    broadcast_app = create_broadcast_app(
        news_store=news_store,
        subscriber_store=subscriber_store,
        load_bot_start_message=load_bot_start_message,
        load_bot_help_message=load_bot_help_message,
        get_start_message=get_start_message,
        get_help_message=get_help_message,
        bot_holder=bot_holder,
        source="max"
    )
    http_task = asyncio.create_task(run_http_server(broadcast_app, 8002))
    scheduler_task = asyncio.create_task(news_scheduler(news_store, subscriber_store, bot_holder, source="max"))
    logger.info("🚀 HTTP сервер и scheduler запущены")
    try:
        await eventlogger.log_event(
            event_type="system_start",
            channel="max",
            payload={
                "status": "bot_started"
            }
        )
        bot_instance = Bot(token=max_token)
        me = await bot_instance.get_me()
        logger.info(f"✅ Успешное подключение к Max API. Бот: @{me.username}")
        # Инициализация компонентов
        bot_holder.instance = bot_instance
        # команды инициализируем
        await setup_bot_commands(bot_instance)
        logger.info("✓ Диспетчер инициализирован")
        logger.info(f"🚀 Бот запущен и готов к работе (версия {Settings.PLATFORM_VERSION})")
        await dp.start_polling(bot_instance)
        
    except KeyboardInterrupt:
        logger.info("Получен сигнал прерывания (Ctrl+C)")

    finally:
        logger.info("Остановка фоновых задач...")
        for task, name in [(http_task, "HTTP сервер"), (scheduler_task, "Scheduler")]:
            if not task.done():
                task.cancel()
                try:
                    await asyncio.wait_for(task, timeout=5.0)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    logger.info(f"✓ {name} остановлен")
        try:
            await adk.close()
            logger.info("✅ ADK клиент закрыт")
        except Exception as e:
            logger.error(f"Ошибка при закрытии ADK: {e}")
        try:
            await store.close()
            logger.info("✅ Подключение к базе закрыто")
        except Exception as e:
            logger.error(f"Ошибка при закрытии БД: {e}")
        try:
            if bot_holder.instance and bot_holder.instance.session:
                await bot_holder.instance.session.close()
                logger.info("✅ Сессия бота закрыта")
        except Exception as e:
            logger.error(f"Ошибка при закрытии сессии бота: {e}")
        logger.info("Бот остановлен")

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise