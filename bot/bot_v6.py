import asyncio
import os
from typing import Optional

from aiogram import Bot, Dispatcher
from aiogram.exceptions import TelegramNetworkError
from aiogram.client.session.aiohttp import AiohttpSession

from dotenv import load_dotenv
import aiohttp

# Загружаем переменные окружения ДО импорта setup_logger
load_dotenv(override=True)

from utils import setup_logger
from utils.event_logger import EventLogger
from utils.document_handler import DocumentHandler
#  импортируем вынесенные классы для работы с базами данных
from bot.services.database import PostgresChatStore, SubscriberStore, NewsStore, AdkApiClient
# импортируем конфиг
from bot.services.config import Settings

from bot.services.broadcast import create_broadcast_app, news_scheduler
from bot.services.handlers import register_handlers
from bot.services.utils import run_http_server
from bot.services.user_resolver import UserResolver
##################################
# Глобальные константы и переменные
##################################

# Настройка логгера
logger = setup_logger('bot', 'bot.log')
# # создаем папку для загрузки новостей, если ее нет
# стартовое сообщение
TITLE_START = """
👋 Привет! Я интерактивный чат-бот базы знаний компании.

📁 Выбери интересующий тебя раздел или напиши что тебя интересует сообщением.
"""

#  создаем класс для того чтобы держать запросы бота
class BotHolder:
    def __init__(self):
        self.instance: Optional[Bot] = None
#####################################
# Главная функция и обработчики бота
#####################################
async def main() -> None:
    """Главная функция бота"""
    logger.info("=" * 60)
    logger.info("Запуск Telegram бота")
    logger.info("=" * 60)
    # logger событий
    eventlogger = EventLogger()
    await eventlogger.init()
    # Загрузка конфигурации
    tg_token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if not tg_token:
        logger.error("TELEGRAM_BOT_TOKEN отсутствует в .env")
        raise RuntimeError("TELEGRAM_BOT_TOKEN is missing in .env")

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
    # ссылка на прокси для телеграма (если нужна)
    proxy_url = os.getenv("TELEGRAM_PROXY")
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
    broadcast_app = create_broadcast_app(
        news_store=news_store,
        subscriber_store=subscriber_store,
        load_bot_start_message=load_bot_start_message,
        get_start_message=get_start_message,
        source="telegram"
    )

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
        platform="telegram"
    )

    logger.info("Все компоненты инициализированы")
    bot_holder = BotHolder()
    http_task = asyncio.create_task(run_http_server(broadcast_app, 8001))
    scheduler_task = asyncio.create_task(news_scheduler(news_store, subscriber_store, bot_holder, source="telegram"))
    logger.info("🚀 HTTP сервер и scheduler запущены")
    # Запуск бота
    try:
        logger.info("🚀 Бот запущен и готов к работе")
        await eventlogger.log_event(
            event_type="system_start",
            channel="telegram",
            payload={
                "status": "bot_started"
            }
        )
        while True:
            bot_instance = None
            try:
                logger.info("Попытка подключения к Telegram API...")

                if proxy_url:
                    session = AiohttpSession(proxy=proxy_url)
                    bot_instance = Bot(token=tg_token, session=session)
                    logger.info(f"Используется Telegram proxy: {proxy_url}")
                else:
                    bot_instance = Bot(token=tg_token)

                me = await bot_instance.get_me()
                logger.info(f"✅ Успешное подключение к Telegram API. Бот: @{me.username}")

                # Инициализация компонентов
                bot_holder.instance = bot_instance

                logger.info("✓ Диспетчер инициализирован")

                logger.info(f"🚀 Бот запущен и готов к работе (версия {Settings.PLATFORM_VERSION})")
                await dp.start_polling(bot_instance)
            except TelegramNetworkError as e:
                logger.warning(
                    f"⚠️ Нет доступа к Telegram API: {e}. "
                    f"Следующая попытка через {Settings.RECONNECT_DELAY_SEC} секунд."
                )
                await asyncio.sleep(Settings.RECONNECT_DELAY_SEC)

            except (
                aiohttp.ClientError,
                OSError,
                ConnectionResetError,
                TimeoutError,
            ) as e:
                logger.warning(
                    f"⚠️ Сетевая ошибка при работе с Telegram API: {e}. "
                    f"Следующая попытка через {Settings.RECONNECT_DELAY_SEC} секунд."
                )
                await asyncio.sleep(Settings.RECONNECT_DELAY_SEC)

            except asyncio.CancelledError:
                logger.info("Получен сигнал остановки бота")
                break

            except Exception as e:
                logger.error(f"❌ Ошибка при работе бота: {e}", exc_info=True)
                logger.info(f"Перезапуск через {Settings.RECONNECT_DELAY_SEC} секунд...")
                bot_holder.instance = None
                await asyncio.sleep(Settings.RECONNECT_DELAY_SEC)

            finally:
                if bot_instance and hasattr(bot_instance, "session") and bot_instance.session:
                    try:
                        await bot_instance.session.close()
                    except Exception as e:
                        logger.warning(f"Ошибка при закрытии сессии бота: {e}")

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

##################################
# Вспомогательные функции
##################################
# делаем загрузку стартового сообщения из файла если есть, иначе берем и загружаем стандартное
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
# загрузка стартового сообщения
load_bot_start_message()

def get_start_message():
    """Получение стартового сообщения"""
    return TITLE_START


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise