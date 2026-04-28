import asyncio
import aiohttp
from dotenv import load_dotenv
from typing import Optional
import uvicorn
# Загружаем переменные окружения ДО импорта setup_logger
load_dotenv(override=True)
from utils import setup_logger
from bot.services.utils import get_kb_tree, register_callback_path, get_document_id, render_results, get_filename, markdown_to_safe_html
from maxapi import Bot, Dispatcher
from maxapi.types import MessageCreated, Command, MessageCallback, InputMedia, BotCommand
import os
from maxapi.types.attachments.attachment import ButtonsPayload
from maxapi.types.attachments.buttons import (
    ClipboardButton,
    LinkButton,
    CallbackButton,
)
from utils.event_logger import EventLogger
from utils.document_handler import DocumentHandler
#  импортируем вынесенные классы для работы с базами данных
from bot.services.database import PostgresChatStore, SubscriberStore, NewsStore, AdkApiClient

from bot.services.config import Settings
from maxapi import F
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from urllib.parse import quote
from aiogram.types import (
    Message, FSInputFile, CallbackQuery, ReplyKeyboardRemove, 
    ReplyKeyboardMarkup, KeyboardButton
    ) 
import tempfile
from datetime import datetime
import time 
from maxapi.types import RequestContactButton
from maxapi.types.attachments import Contact
import re, uuid
#  импортируем функции вспомогательные для бота
from utils.doc_search_format import parse_download_ranks
from maxapi.enums import TextFormat
from bot.services.broadcast import create_broadcast_app, news_scheduler

# Настройка логирования
logger = setup_logger('bot', 'bot.log')
TREE_CACHE = None
TREE_TS = 0
TITLE_START = """
👋 Привет! Я интерактивный чат-бот базы знаний компании.

📁 Выбери интересующий тебя раздел или напиши что тебя интересует сообщением.
"""
eventlogger = EventLogger()
class BotHolder:
    def __init__(self):
        self.instance: Optional[Bot] = None
# Инициализация бота и диспетчера
# Токен можно задать через переменную окружения MAX_BOT_TOKEN
# или передать напрямую: Bot(token='ваш_токен')
async def setup_bot_commands(bot):
    """Регистрация команд в меню бота"""

    try:
        await bot.set_my_commands(
            BotCommand(name="start", description="Начать работу"),
            BotCommand(name="help", description="Показать справку"),
            BotCommand(name="version", description="Версия системы"),
            BotCommand(name="reset", description="сбросить историю"),
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

def get_start_message():
    """Получение стартового сообщения"""
    return TITLE_START

def build_menu_from_tree(tree: dict, path: list[str]):
    """Функция для строительства деревовидной структуры папок"""
    node = tree

    for p in path:
        node = node.get(p, {})
    builder = InlineKeyboardBuilder()
    # папки
    for key in node.keys():
        if key == "files":
            continue
        full_path = "/".join(path + [key])
        pid = register_callback_path(full_path)
        builder.row(
            CallbackButton(
                text=f"📁 {key}",
                payload=f"d:{pid}"
            )
        )

    # файлы
    for f in node.get("files", []):
        full_path = "/".join(path + [f])
        pid = register_callback_path(full_path)
        builder.row(
            CallbackButton(
                text=f"📄 {f}",
                payload=f"f:{pid}"
            )
        )

    # навигация
    if path:
        parent = "/".join(path[:-1])
        pid = register_callback_path(parent)
        builder.row(
            CallbackButton(
                text=f"⬅ Назад",
                payload=f"d:{pid}"
            ),
            CallbackButton(
                text="🏠 на главную",
                payload="home"
            )
        )

    return builder.as_markup()

######################################
# обработчики сообщений и команд бота
######################################
def register_handlers(dp: Dispatcher, store, subscriber_store, adk, doc_handler, get_start_message) -> None:
    """Регистрация всех обработчиков сообщений"""
    # Обработчик команды /start
    @dp.message_created(Command("start"))
    async def start_handler(event: MessageCreated):
        """Обработчик /start"""
        user = await get_authenticated_user(event, subscriber_store)
        if not user:
            return 
            
        user_id = user["user_id"]
        logger.info(f"Команда /start от user_id={user_id} (@{user['username']})")
        await eventlogger.log_event(
            event_type="command_start",
            user_id=str(user_id),
            user_name=user.get("username"),
            session_id=str(user_id),
            channel="max"
        )
        # На /start не вызываем ADK.
        # Только обновляем пользователя в БД через get_authenticated_user()
        # и показываем стартовое меню.
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])
        
        await event.message.answer(
            text=get_start_message(),
            attachments=[menu]
        )
    # обработчик команды /version для получения версии
    @dp.message_created(Command("version"))
    async def version_info(event: MessageCreated):
        """Команда для получения версии платформы/бота"""
        user_id = event.from_user.user_id
        logger.info(f"Команда /version от user_id={user_id}")
        await eventlogger.log_event(
            event_type="command_version",
            user_id=str(user_id),
            session_id=str(user_id),
            channel="max"
        )
        await event.message.answer(f"Текущая версия бота: {Settings.PLATFORM_VERSION}")
    # домашняя страница
    @dp.message_callback(F.callback.payload == "home")
    async def go_home(event: MessageCallback):
        """Обработчик перехода на главную страницу"""
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])
        
        await event.message.edit(
            text=get_start_message(),
            attachments=[menu]
        )
    # обработчик команды /reset для сброса истории и сессии
    @dp.message_created(Command("reset"))
    async def reset(event: MessageCreated) -> None:
        user_id = event.from_user.user_id
        username = event.from_user.username or "unknown"
        session_id = str(user_id)

        logger.info(f"Команда /reset от user_id={user_id} (@{username})")
        await eventlogger.log_event(
            event_type="command_reset",
            user_id=str(user_id),
            session_id=session_id,
            channel="max"
        )
        try:
            # Удаляем сессию в ADK (актуальная + legacy "default" от старых версий бота)
            await adk.delete_session(user_id=str(user_id), session_id=session_id)

            # Очищаем историю в БД
            await store.reset(user_id)

            # Удаляем состояние результатов поиска
            await store.reset_search_state(user_id, session_id)

            # После /reset не создаем новую ADK-сессию
            # и не вызываем set_user_state.
            # Новая session и state будут созданы при первом обычном сообщении.
            await event.message.answer("✅ История диалога и сессия сброшены")
            logger.info(f"История и сессия сброшены для user_id={user_id}")

        except Exception as e:
            await eventlogger.log_event(
                event_type="error",
                user_id=str(user_id),
                session_id=session_id,
                channel="max",
                payload={
                    "error": str(e)
                }
            )
            logger.error(f"Ошибка при сбросе: {e}", exc_info=True)
            await event.message.answer("❌ Ошибка при сбросе истории")

        return

    @dp.message_created(Command("help"))
    async def help_cmd(event: MessageCreated) -> None:
        user_id = event.from_user.user_id
        logger.info(f"Команда /help от user_id={user_id}")
        await eventlogger.log_event(
            event_type="command_help",
            user_id=str(user_id),
            channel="max"
        )
        await event.message.answer(
            "ℹ️ Я помогу найти информацию в базе знаний.\n\n"
            "Просто напиши свой вопрос, и я постараюсь найти ответ!\n\n"
            "Команды:\n"
            "/start — начать работу\n"
            "/reset — сбросить историю\n"
            "/help — эта справка"
        )

    # обработчик открытия папки в меню бота
    @dp.message_callback(F.callback.payload.startswith("d:"))
    async def open_dir(event: MessageCallback):
        """Команда обработчик открытия папки"""
        payload = event.callback.payload
        if not payload:
            await event.answer("Ошибка данных")
            return
        
        pid = payload.split(":", 1)[1]
        path = Settings.CALLBACK_MAP.get(pid)

        if path is None:
            await event.answer("Кнопка устарела")
            return

        Settings.CALLBACK_MAP.move_to_end(pid)
        path_list = path.split("/") if path else []
        tree = await get_tree_cached()

        menu = build_menu_from_tree(tree, path_list)
        title = "📁 /".join(path_list) or get_start_message()
        await event.message.edit(
            text=title,
            attachments=[menu]
        )
    # обработчик открытия файла в меню бота
    @dp.message_callback(F.callback.payload.startswith("f:"))
    async def send_file(event: MessageCallback):
        """Обработчик отправки файлов через меню бота"""
        payload = event.callback.payload
        if not payload:
            await event.answer("❌ Ошибка данных")
            return
        pid = payload.split(":", 1)[1]
        path = Settings.CALLBACK_MAP.get(pid)
        if not path:
            await event.answer("Файл не найден")
            return
        Settings.CALLBACK_MAP.move_to_end(pid)
        doc_id = await get_document_id(path)
        if not doc_id:
            url = f"{Settings.KB_MANAGER_URL}/api/filesystem/download/?path={quote(path)}"
        else:
            url = f"{Settings.KB_MANAGER_URL}/api/documents/download/{doc_id}"
        filename = path.split("/")[-1]
        user_id = event.callback.user.user_id
        logger.info(f"Запрос на скачивание файла через меню: {filename} (doc_id={doc_id}) от user_id={user_id}")
        await eventlogger.log_event(
            event_type="document_download_menu",
            user_id=str(user_id),
            session_id=str(user_id),
            user_name=event.callback.user.username,
            channel="max",
            payload={
                "filename": filename,
                "file_path": path,
                "doc_id": doc_id,
                "source": "menu"
            }
        )
        tmp_name = os.path.join(tempfile.gettempdir(), filename)
        try:
            # Скачиваем файл во временный буфер
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    with open(tmp_name, "wb") as f:
                        f.write(await resp.read())


            # Отправляем файл через InputMedia
            await event.message.answer(
                attachments=[InputMedia(path=tmp_name)]
            )
            logger.info(f"✅ Файл отправлен: {filename}")
                        
        except Exception as e:
            logger.error(f"❌ Ошибка отправки файла: {e}", exc_info=True)
        finally:
            # Очищаем временный файл
            if tmp_name and os.path.exists(tmp_name):
                os.remove(tmp_name)

    # обработчик всех текстовых сообщений (основной диалог)
    @dp.message_created()
    async def handle_message(event: MessageCreated):
        # проверяем не пришел ли контакт
        if event.message.body and event.message.body.attachments:
            for att in event.message.body.attachments:
                # Контакт приходит как dict с type='contact'
                if hasattr(att, 'type') and att.type == 'contact':
                    logger.info("Контакт обнаружен, обрабатываем...")
                    await handle_contact_received(event, att)
                    return  # контакт обработан, выходим
        # Если это не контакт — проверяем авторизацию как обычно
        user = await get_authenticated_user(event, subscriber_store)
        if not user:
            return
        
        user_id = user["user_id"]
        session_id = str(user_id)
        user_text = (event.message.body.text or "").strip()
        if not user_text:
            return
        turn_id = str(uuid.uuid4())

        # логируем скорость ответа
        start_time = time.time()
        logger.info(f"📨 Сообщение от user_id={user_id} (@{user['username']}): {user_text[:100]}")
        await eventlogger.log_event(
            event_type="message_received",
            user_id=str(user_id),
            user_name=user.get("username"),
            session_id=session_id,
            channel="max",
            payload={
                "text": user_text,
                "turn_id": turn_id,
                "start_time": start_time
            }
        )
        try:
            await adk.ensure_session(user_id=str(user_id), session_id=session_id)

            # Пагинация и скачивание по номеру — из БД, без вызова ADK
            """
            Основной блок обработки пользовательских текстовых сообщений.
            Здесь происходит разбор запросов на постраничный просмотр/загрузку файлов,
            поиск по базе знаний (через ADK), а также отправка найденных документов.

            Весь блок обрабатывается внутри try, чтобы корректно залогировать и обработать любые ошибки.
            """
            # --- Пагинация: показать следующую порцию сохранённого списка документов ---
            if Settings.SHOW_MORE_RE.match(user_text):
                # Если пользователь запросил "показать еще", возвращаем следующую порцию из сохраненного поиска.
                ok = await handle_show_more(event, store, user_id, session_id, turn_id, start_time)
                if not ok:
                    # Сообщение для пользователя, если списка нет
                    response_time = int((time.time() - start_time) * 1000)
                    answer = "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                    await event.message.answer(answer)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(user_id),
                        session_id=session_id,
                        channel="max",
                        payload={
                            "turn_id": turn_id,
                            "text": answer, 
                            "response_time_ms": response_time
                        }    
                    )
                # Логируем пользовательский запрос и результат в историю
                await store.append(user_id, "user", user_text)
                await store.append(
                    user_id,
                    "model",
                    "Показана следующая порция списка документов."
                    if ok
                    else "Список документов не найден.",
                )
                return
            # --- Пагинация: показать полный список сохранённых документов ---
            if Settings.SHOW_ALL_RE.match(user_text) and not Settings.SHOW_MORE_RE.match(user_text):
                # Только если это не "показать еще"
                ok = await handle_show_all(event, store, user_id, session_id, turn_id, start_time)
                if not ok:
                    response_time = int((time.time() - start_time) * 1000)
                    answer = "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                    await event.message.answer(answer)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(user_id),
                        session_id=session_id,
                        channel="max",
                        payload={
                            "turn_id": turn_id,
                            "text": answer, 
                            "response_time_ms": response_time
                        }    
                    )
                await store.append(user_id, "user", user_text)
                await store.append(
                    user_id,
                    "model",
                    "Показан полный список документов."
                    if ok
                    else "Список документов не найден.",
                )
                return
            
            # --- Обработка запроса на скачивание файлов по номерам из списка ---
            dl_ranks = parse_download_ranks(user_text)
            if dl_ranks:
                """
                Если пользователь ввёл запрос, похожий на "скачать документы под номерами ...",
                вызываем обработчик отправки файлов.
                """
                await handle_download_by_ranks(
                    event, store, doc_handler, user_id, session_id, dl_ranks, turn_id, start_time
                )
                await store.append(user_id, "user", user_text)
                await store.append(user_id, "model", "Запрошена отправка файлов по номерам из списка.")
                return

            # Синхронизируем профиль пользователя в ADK перед run()
            await sync_user_profile_to_adk(
                adk=adk,
                subscriber_store=subscriber_store,
                user_id=int(user_id),
                session_id=session_id,
            )

            # --- Получаем информацию о последнем поиске перед текущим запросом (для контроля смены поиска) ---
            meta_before = await store.get_last_search_meta(user_id, session_id)
            search_id_before = meta_before["search_id"] if meta_before else None

            # --- Общий запрос к ADK: поиск и формирование ответа для пользователя ---
            answer, _ = await adk.run(
                user_id=str(user_id),
                session_id=session_id,
                text=user_text
            )
            response_time = int((time.time() - start_time) * 1000)
            logger.info(f"📤 Ответ для user_id={user_id}: {answer[:100]}")
            # сохраняем в логах событие ответа и его латентность
            await eventlogger.log_event(
                event_type="response",
                user_id=str(user_id),
                session_id=session_id,
                channel="max",
                payload={
                    "turn_id": turn_id,
                    "text": answer[:500],  # не логируем слишком длинные
                    "response_time_ms": response_time
                }
            )

            work = answer or ""

            # сохраняем историю диалога
            await store.append(user_id, "user", user_text)
            await store.append(user_id, "model", answer)

            # Новый поиск документов: список в БД — признак смены search_id, первая порция рендерится здесь
            meta_after = await store.get_last_search_meta(user_id, session_id)
            search_id_after = meta_after["search_id"] if meta_after else None
            if (
                search_id_after
                and search_id_after != search_id_before
                and meta_after
            ):
                items = await store.get_last_search_results(user_id, session_id)
                if items:
                    shown = int(meta_after["shown_count"])
                    shown = min(max(shown, 0), len(items))
                    chunk = items[:shown]
                    text = render_results(chunk, total=len(items), offset=0)
                    await event.message.answer(text, format=TextFormat.HTML)
                    response_time = int((time.time() - start_time) * 1000)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(user_id),
                        session_id=session_id,
                        channel="max",
                        payload={
                            "turn_id": turn_id,
                            "text": text,
                            "response_time_ms": response_time
                        }    
                    )
                    return
                
            # ответ пользователю (kb_answer и прочее)
            if work.strip():
                if "<b>" in work or work.lstrip().startswith("<"):
                    await event.message.answer(work, format=TextFormat.HTML)
                    response_time = int((time.time() - start_time) * 1000)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(user_id),
                        session_id=session_id,
                        channel="max",
                        payload={
                            "turn_id": turn_id,
                            "text": work,
                            "response_time_ms": response_time
                        }    
                    )
                else:
                    html_answer = markdown_to_safe_html(work)
                    await event.message.answer(html_answer, format=TextFormat.HTML)
                    response_time = int((time.time() - start_time) * 1000)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(user_id),
                        session_id=session_id,
                        channel="max",
                        payload={
                            "turn_id": turn_id,
                            "text": html_answer,
                            "response_time_ms": response_time
                        }    
                    )

        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения от user_id={user_id}: {e}", exc_info=True)
            await eventlogger.log_event(
                event_type="error",
                user_id=str(user_id),
                session_id=session_id,
                channel="max",
                payload={
                    "error": str(e)
                }
            )
            await event.message.answer(
                "😔 Произошла ошибка при обработке запроса.\n"
                "Попробуйте позже или используйте /reset для сброса диалога."
            )
        # logger.info(f" Что храниться внутри {event}")
    
    # обработчик получения контакта (номера телефона)
    async def handle_contact_received(event: MessageCreated, contact: Contact):
        """Обработчик полученного контакта — извлекает телефон из vCard"""
        user_id = event.from_user.user_id
        try:
            # Контакт приходит как объект с полем payload
            payload = contact.payload  # ContactAttachmentPayload
            
            # Телефон может быть в vcf_info (vCard формат)
            vcf_info = getattr(payload, 'vcf_info', None)
            phone = None
            
            if vcf_info:
                # Парсим vCard: ищем строку TEL:...
                # Пример: TEL;TYPE=cell:79647322754
                tel_match = re.search(r'TEL[^:]*:([+\d\s\-\(\)]+)', vcf_info)
                if tel_match:
                    phone = tel_match.group(1).strip()
                    # Очищаем номер: убираем пробелы, тире, скобки
                    phone = re.sub(r'[\s\-\(\)]', '', phone)
            
            # Альтернатива: если есть прямое поле phone
            if not phone and hasattr(payload, 'phone'):
                phone = getattr(payload, 'phone', None)
                
            if not phone:
                logger.warning(f"❌ Не удалось извлечь телефон из vcf_info: {vcf_info[:100] if vcf_info else 'None'}")
                await event.message.answer("⚠️ Не удалось получить номер. Попробуйте ещё раз.")
                return
            phone = f"+{phone}"
            logger.info(f"✅ Телефон извлечён: {phone}")
        
            # Сохраняем телефон в БД
            await subscriber_store.update_phone(user_id, phone)
            
            logger.info(f"✅ Телефон получен: user_id={user_id}, phone={phone}")
            await eventlogger.log_event(
                event_type="get_contact",
                user_id=str(user_id),
                session_id=str(user_id),
                channel="max"
            )
            
            # Показываем меню
            tree = await get_tree_cached()
            menu = build_menu_from_tree(tree, [])
            
            await event.message.answer(
                text="✅ Спасибо! Теперь вы можете пользоваться ботом."
            )
            await event.message.answer(
                text=get_start_message(),
                attachments=[menu]
            )
        except Exception as e:
            logger.error(f"❌ Ошибка обработки контакта: {e}", exc_info=True)
            await event.message.answer("⚠️ Произошла ошибка. Попробуйте ещё раз.")
            await eventlogger.log_event(
                event_type="error",
                user_id=str(user_id),
                session_id=str(user_id),
                channel="max",
                payload={
                    "error": str(e)
                }
            )

# обработка команды скачивания по рангам
async def handle_download_by_ranks(
        event: MessageCreated,
        store: PostgresChatStore,
        doc_handler: DocumentHandler,
        user_id: int,
        session_id: str,
        ranks: list[int],
        turn_id: str,
        start_time
    ) -> bool:
    if not ranks:
        return False
    sent_any = False
    for rank in ranks:
        item = await store.get_result_by_rank(user_id, session_id, rank)
        if not item:
            answer = f"Не нашёл документ №{rank} в последнем списке."
            await event.message.answer(answer)
            response_time = int((time.time() - start_time) * 1000)
            await eventlogger.log_event(
                event_type="response",
                user_id=str(user_id),
                session_id=session_id,
                channel="max",
                payload={
                    "turn_id": turn_id,
                    "text": answer,
                    "response_time_ms": response_time
                }    
            )
            continue

        doc_id = item.get("document_id")
        if not doc_id:
            answer = f"Не удалось определить document_id для документа №{rank}."
            await event.message.answer(answer)
            response_time = int((time.time() - start_time) * 1000)
            await eventlogger.log_event(
                event_type="response",
                user_id=str(user_id),
                session_id=session_id,
                channel="max",
                payload={
                    "turn_id": turn_id,
                    "text": answer,
                    "response_time_ms": response_time
                }    
            )
            continue

        file_path = None
        try:
            file_path = await doc_handler.download_document(doc_id)
            if file_path and file_path.exists():
                await event.message.answer(
                    attachments=[InputMedia(path=str(file_path))]
                )
                await eventlogger.log_event(
                    event_type="document_download",
                    user_id=str(user_id),
                    user_name=event.from_user.username,
                    session_id=session_id,
                    channel="max",
                    payload={
                        "file_path": str(file_path),
                        "text": get_filename(str(file_path)),
                        "doc_id": doc_id,
                        "rank": rank,
                        "source": "search",
                        "turn_id": turn_id
                    }
                )
                sent_any = True
            else:
                await event.message.answer(f"Не удалось загрузить документ №{rank}.")
                await eventlogger.log_event(
                    event_type="document_download_failed",
                    user_id=str(user_id),
                    user_name=event.from_user.username,
                    session_id=session_id,
                    channel="max",
                    payload={
                        "file_path": str(file_path),
                        "text": f"Не удалось загрузить документ №{rank}.",
                        "doc_id": doc_id,
                        "rank": rank,
                        "source": "search",
                        "turn_id": turn_id
                    }
                )
        except Exception as e:
            logger.error(f"Ошибка отправки документа rank={rank}, doc_id={doc_id}: {e}", exc_info=True)
            answer = f"Ошибка при загрузке документа №{rank}."
            await event.message.answer(answer)
            response_time = int((time.time() - start_time) * 1000)
            await eventlogger.log_event(
                event_type="response",
                user_id=str(user_id),
                session_id=session_id,
                channel="max",
                payload={
                    "turn_id": turn_id,
                    "text": answer,
                    "response_time_ms": response_time
                }    
            )
            
        finally:
            try:
                if file_path and file_path.exists():
                    file_path.unlink()
            except Exception:
                pass

    return sent_any

# обработка команды показать все результаты
async def handle_show_all(
    event: MessageCreated,
    store: PostgresChatStore,
    user_id: int,
    session_id: str,
    turn_id: str,
    start_time
) -> bool:
    items = await store.get_last_search_results(user_id, session_id)
    if not items:
        return False

    text = render_results(items, total=len(items), offset=0)
    await store.update_shown_count(user_id, session_id, len(items))
    await event.message.answer(text, format=TextFormat.HTML)
    response_time = int((time.time() - start_time) * 1000)
    await eventlogger.log_event(
        event_type="response",
        user_id=str(user_id),
        session_id=session_id,
        channel="max",
        payload={
            "turn_id": turn_id,
            "text": text,
            "response_time_ms": response_time
        }    
    )
    return True

#  обработка команды показать ещё результаты
async def handle_show_more(
    event: MessageCreated,
    store: PostgresChatStore,
    user_id: int,
    session_id: str,
    page_size: int = Settings.SHOW_MAX,
    turn_id: str=None,
    start_time: None=None
) -> bool:
    meta = await store.get_last_search_meta(user_id, session_id)
    items = await store.get_last_search_results(user_id, session_id)

    if not meta or not items:
        return False

    start = meta["shown_count"]
    end = min(start + page_size, len(items))

    if start >= len(items):
        answer = "Это уже все найденные файлы."
        await event.message.answer(answer)
        response_time = int((time.time() - start_time) * 1000)
        await eventlogger.log_event(
            event_type="response",
            user_id=str(user_id),
            session_id=session_id,
            channel="max",
            payload={
                "turn_id": turn_id,
                "text": answer,
                "response_time_ms": response_time
            }    
        )
        
        return True

    chunk = items[start:end]
    text = render_results(chunk, total=len(items), offset=start)
    await store.update_shown_count(user_id, session_id, end)
    await event.message.answer(text, format=TextFormat.HTML)
    await eventlogger.log_event(
            event_type="response",
            user_id=str(user_id),
            session_id=session_id,
            channel="telegram",
            payload={
                "turn_id": turn_id,
                "text": text,
                "response_time_ms": response_time
            }    
        )
    return True

async def sync_user_profile_to_adk(adk, subscriber_store, user_id: int, session_id: str) -> None:
    """
    Загружает профиль пользователя из БД и подготавливает его для передачи в ADK.
    """
    user_data = await subscriber_store.get_user_data(user_id)
    if not user_data:
        return

    # Лишние персональные данные в ADK не передаем
    user_data.pop("phone_number", None)
    
    # Убеждаемся, что имя не пустое (иначе агент не обратится по имени)
    if not user_data.get("first_name"):
        logger.warning(f"first_name пуст для user_id={user_id}")
    if not user_data.get("last_name"):
        logger.warning(f"last_name пуст для user_id={user_id}")

    await adk.set_user_state(
        user_id=str(user_id),
        session_id=session_id,
        user_data=user_data,
    )

async def get_authenticated_user(event: MessageCreated | MessageCallback, subscriber_store) -> dict | None:
    """
    Проверяет регистрацию и наличие телефона в БД.
    Если телефона нет, но он есть в профиле MAX — сохраняет.
    Если телефона нет вообще — возвращает None (нужно запросить авторизацию).
    """
    # Получаем ID и данные пользователя из события
    user_id = int(event.callback.user.user_id if hasattr(event, 'callback') else event.from_user.user_id)
    user_obj = event.callback.user if hasattr(event, 'callback') else event.from_user
    
    username = user_obj.username or "unknown"
    first_name = user_obj.first_name or "Гость"
    last_name = user_obj.last_name
    last_seen = datetime.now()
    
    # Проверяем, есть ли телефон в нашей БД
    existing_phone = await subscriber_store.get_phone(user_id)
    # Добавление/обновление пользователя в базе
    await subscriber_store.add(
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        last_seen=last_seen,
        phone_number=None,
        platform="max" # не затираем телефон если он уже есть
    )
    # Достаем полные данные пользователя
    user_data = await subscriber_store.get_user_data(user_id)
    # Если телефона нет — запрашиваем контакт
    if not existing_phone:
        logger.info(f"🔐 Запрос телефона у user_id={user_id} (@{username})")
        await event.message.answer(
            text=(
                f"👋 Привет, {first_name}!\n\n"
                f"Для связи с вами нам нужен ваш номер телефона.\n\n"
                f"Пожалуйста, нажмите кнопку ниже чтобы поделиться номером.\n"
                f"Это нужно только для уведомлений о важных обновлениях."
            ),
            attachments=[get_phone_keyboard()]
        )
        return None
    return user_data

def get_phone_keyboard():
    """Клавиатура с кнопкой контакта"""
    builder = InlineKeyboardBuilder()
    builder.row(
        RequestContactButton(text='📱 Поделиться номером телефона')
    )
    return builder.as_markup()

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
    max_token = os.getenv("MAX_BOT_TOKEN", "REDACTED_EXAMPLE").strip()
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
    # инициализируем хранилище новостей
    news_store = NewsStore(store.pool)
    adk = AdkApiClient(base_url=adk_base, app_name=adk_app)
    await adk.open()
    broadcast_app = create_broadcast_app(
        news_store=news_store,
        subscriber_store=subscriber_store,
        load_bot_start_message=load_bot_start_message,
        get_start_message=get_start_message,
        source="max"
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
        adk=adk,
        doc_handler=doc_handler,
        get_start_message=get_start_message
    )
    logger.info("Все компоненты инициализированы")
    bot_holder = BotHolder()
    http_task = asyncio.create_task(run_http_server(broadcast_app))
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

# Запуск HTTP сервера в отдельной задаче
async def run_http_server(app):
    """Запуск HTTP сервера"""
    try:
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=8002,
            log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()
    except asyncio.CancelledError:
        logger.info("HTTP сервер остановлен")

# загрузка стартового сообщения
load_bot_start_message()

async def get_tree_cached():
    global TREE_CACHE, TREE_TS
    # кэшируем дерево чтобы постоянно не обращаться к api 15 sec 
    if TREE_CACHE and time.time() - TREE_TS < Settings.TIME_SET_WAIT:
        return TREE_CACHE

    TREE_CACHE = await get_kb_tree()
    TREE_TS = time.time()

    return TREE_CACHE

if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Получен сигнал остановки (Ctrl+C)")
    except Exception as e:
        logger.error(f"Критическая ошибка: {e}", exc_info=True)
        raise