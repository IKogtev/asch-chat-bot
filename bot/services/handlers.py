import os
import aiohttp
import time
from urllib.parse import quote
from aiogram.types import ( FSInputFile, ReplyKeyboardRemove,
    ReplyKeyboardMarkup, KeyboardButton
    ) 
import tempfile
from datetime import datetime
from utils import setup_logger
# логер событий
from utils.event_logger import EventLogger
# импортируем конфиг
from bot.services.config import Settings
#  импортируем функции вспомогательные для бота
from utils.doc_search_format import parse_download_ranks
from bot.services.adk_events import extract_bot_action
from bot.services.product_kits import get_product_kit

from bot.services.utils import (
    markdown_to_safe_html,
    render_results,
    handle_show_more,
    handle_show_all,
    handle_download_by_ranks,
    get_document_id,
    get_kb_tree,
    build_universal_menu,
    normalize_phone
)
import uuid
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from maxapi.types import InputMedia, RequestContactButton
import re
from maxapi.enums import TextFormat
from maxapi.enums.sender_action import SenderAction
from maxapi.types.attachments import Contact
import contextlib
import asyncio

logger = setup_logger('handlers', 'handlers.log')
# инициализируем логер событий
eventlogger = EventLogger()
# переменные для сохранения дерева папок в кэше
TREE_CACHE = None
TREE_TS = 0
#  переменные для отсечения старых сообщений бота
BOT_START_TIME = time.time()
RECENT_MESSAGES = {}
STARTUP_GRACE_PERIOD = 5  # сек
OLD_MESSAGE_THRESHOLD = 15  # сек


# Отправка комплекта документов продукта по структурному действию от ADK.
async def handle_product_kit_action(
    bot_res,
    bot_action: dict,
    user_id: int | str,
    session_id: str,
    turn_id: str,
    start_time: float,
    platform: str,
) -> bool:
    """Находит папку продукта по ID, отправляет файлы комплекта и пишет события в лог."""
    product_code = str(bot_action.get("product_code") or "").strip()
    product_name = str(bot_action.get("product_name") or "").strip()
    folder_kit = str(bot_action.get("folder_kit") or "").strip()
    result = get_product_kit(
        product_code=product_code,
        product_name=product_name,
        folder_kit=folder_kit,
    )

    if result["status"] != "ok":
        response_time = int((time.time() - start_time) * 1000)
        await bot_res.send(result["message"])
        await eventlogger.log_event(
            event_type="product_kit_status",
            user_id=str(user_id),
            session_id=session_id,
            channel=platform,
            payload={
                "turn_id": turn_id,
                "product_code": product_code,
                "product_name": product_name,
                "folder_kit": folder_kit,
                "status": result["status"],
                "text": result["message"],
                "response_time_ms": response_time,
            },
        )
        return False

    sent = 0
    for file_info in result["files"]:
        await bot_res.send(
            "",
            is_doc={"path": file_info["path"], "name": file_info["name"]},
        )
        sent += 1
        await eventlogger.log_event(
            event_type="document_download",
            user_id=str(user_id),
            session_id=session_id,
            channel=platform,
            payload={
                "file_path": file_info["path"],
                "text": file_info["name"],
                "doc_id": None,
                "rank": None,
                "source": "product_kit",
                "turn_id": turn_id,
                "product_code": product_code,
                "product_name": product_name,
                "folder_kit": folder_kit,
            },
        )

    response_time = int((time.time() - start_time) * 1000)
    await eventlogger.log_event(
        event_type="product_kit_sent",
        user_id=str(user_id),
        session_id=session_id,
        channel=platform,
        payload={
            "turn_id": turn_id,
            "product_code": product_code,
            "product_name": product_name,
            "folder_kit": folder_kit,
            "files_sent": sent,
            "skipped_files": result.get("skipped_files", []),
            "response_time_ms": response_time,
        },
    )
    return sent > 0


# синхронизация пользователей с адк 
async def sync_user_profile_to_adk(adk, subscriber_store, user_id: int, session_id: str, global_user_id=None) -> None:
    """
    Загружает профиль пользователя из БД и подготавливает его для передачи в ADK.
    """
    user_data = await subscriber_store.get_user_data(user_id)
    if not user_data and not global_user_id:
        return

    # Лишние персональные данные в ADK не передаем
    user_data.pop("phone_number", None)
    adk_user_id = str(global_user_id)

    # Убеждаемся, что имя не пустое (иначе агент не обратится по имени)
    if not user_data.get("first_name"):
        logger.warning(f"first_name пуст для user_id={user_id}")
    if not user_data.get("last_name"):
        logger.warning(f"last_name пуст для user_id={user_id}")

    await adk.set_user_state(
        user_id=adk_user_id,
        session_id=adk_user_id,
        user_data=user_data,
    )

# кэширование полученных путей 
async def get_tree_cached():
    global TREE_CACHE, TREE_TS
    # кэшируем дерево чтобы постоянно не обращаться к api 15 sec 
    if TREE_CACHE and time.time() - TREE_TS < Settings.TIME_SET_WAIT:
        return TREE_CACHE

    TREE_CACHE = await get_kb_tree()
    TREE_TS = time.time()

    return TREE_CACHE

# получаем клавиатуру 
def get_phone_keyboard(platform: str="telegram"):
    """Клавиатура с кнопкой контакта"""
    if platform == 'telegram':
        return ReplyKeyboardMarkup(
            keyboard=[
                [KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]
            ],
            resize_keyboard=True,
            one_time_keyboard=True
        )
    else: #Max API
        builder = InlineKeyboardBuilder()
        builder.row(
            RequestContactButton(text='📱 Поделиться номером телефона')
        )
        return builder.as_markup()

######################################
# обработчики сообщений и команд бота
######################################
def register_handlers(dp, store, subscriber_store, user_resolver, adk, doc_handler, get_start_message, get_help_message, platform="telegram") -> None:
    """Регистрация всех обработчиков сообщений универсальная для разных платформ"""
    is_tg = (platform == "telegram")
    # 1. Адаптация декораторов и фильтров под платформу
    if is_tg:
        from aiogram.filters import Command
        from aiogram import F
        message_decorator = dp.message
        callback_decorator = dp.callback_query
        home_filter = (F.data == "home")
        dir_filter = F.data.startswith("d:")
        file_filter = F.data.startswith("f:")
    else:
        from maxapi import F
        from maxapi.types import Command
        message_decorator = dp.message_created
        callback_decorator = dp.message_callback
        home_filter = (F.callback.payload == "home")
        dir_filter = F.callback.payload.startswith("d:")
        file_filter = F.callback.payload.startswith("f:")

    def universal_handler(func):
        async def wrapper(event, *args, **kwargs):
            # АНТИ-БЭКЛОГ ЗАЩИТА 
            is_callback = hasattr(event, "data") or hasattr(event, "callback")

            # применяем ТОЛЬКО к обычным сообщениям
            if not is_callback:

                # 1. защита при старте
                if time.time() - BOT_START_TIME < STARTUP_GRACE_PERIOD:
                    logger.warning("⏳ Skip message during startup")
                    return

                # 2. защита от старых сообщений
                if is_old_message(event, platform):
                    logger.warning("⏳ Skip old message (backlog)")
                    return
            # Вызываем общую авторизацию
            ud, bot_res = await unified_auth(event)
            if ud is None:
                return # Прерываем, если телефон не получен
            
            # Передаем управление в основную функцию, добавляя ud и bot_res
            return await func(event, ud, bot_res, *args, **kwargs)
        return wrapper
    
    #  отсечение по времени: 
    def is_old_message(event, platform: str) -> bool:
        """Отсекаем старые сообщения (backlog)"""
        try:
            now = time.time()

            if platform == "telegram":
                msg_time = getattr(event, "date", None)
                if not msg_time:
                    return False
                msg_ts = msg_time.timestamp()

            else:  # max
                msg = getattr(event, "message", None)
                if not msg:
                    return False

                msg_time = getattr(msg, "timestamp", None) or getattr(msg, "created_at", None)

                if isinstance(msg_time, (int, float)):
                    msg_ts = msg_time
                elif hasattr(msg_time, "timestamp"):
                    msg_ts = msg_time.timestamp()
                else:
                    return False

            return (now - msg_ts) > OLD_MESSAGE_THRESHOLD

        except Exception:
            return False

    # общая авторизация
    async def unified_auth(event):
        """Проверка регистрации и получение данных"""
        bot_res = BotResponse(event, platform)
        user_obj = event.from_user if is_tg else (event.callback.user if hasattr(event, 'callback') else event.from_user)
        user_id = int(getattr(user_obj, 'id' if is_tg else 'user_id'))
        # Проверка, не является ли текущее сообщение контактом
        is_incoming_contact = False
        contact_obj = None
        if platform == "telegram" and hasattr(event, 'contact') and event.contact:
            is_incoming_contact = True
            contact_obj = event.contact
        elif platform == "max":
            # Проверяем вложения в BotResponse (мы его уже создали выше)
            for att in bot_res.attachments:
                if getattr(att, 'type', None) == 'contact':
                    is_incoming_contact = True
                    contact_obj = att
                    break

        username = user_obj.username or "unknown"
        first_name = user_obj.first_name or "Гость"
        last_name = getattr(user_obj, 'last_name', None)
        # Телефон (ВАЖНО: сначала из event, потом из БД) ---
        phone = None

        if is_incoming_contact and contact_obj:
            # берем из события, а не из БД
            raw_phone = getattr(contact_obj, "phone_number", None)
            phone = normalize_phone(raw_phone)

            # контакт может содержать более точные данные
            first_name = getattr(contact_obj, "first_name", None) or first_name
            last_name = getattr(contact_obj, "last_name", None) or last_name

        else:
            # fallback в БД
            phone = await subscriber_store.get_phone(user_id)
        #  резолвим global user
        global_user_id = None
        if phone:
            try:
                global_user_id = await user_resolver.resolve_user(
                    platform=platform,
                    platform_user_id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name,
                    phone=phone
                )
            except Exception as e:
                logger.error(f"UserResolver error: {e}", exc_info=True)

        # всегда обновляем подписчика
        await subscriber_store.add(
            user_id=user_id, username=username,
            first_name=first_name, last_name=last_name,
            last_seen=datetime.now(), phone_number=phone, platform=platform
        )
        # если это контакт — обновим телефон явно
        if phone:
            await subscriber_store.update_phone(user_id, phone)
        # обработка блокировки пользователя
        user_data = await subscriber_store.get_user_data(user_id) or {}
        if user_data.get("is_blocked"):
            await bot_res.send(
                "🚫 Доступ для вашей учетной записи ограничен.\n\n"
                "Пожалуйста, обратитесь к администратору сервиса."
                )
            return None, None
        
        user_data["global_user_id"] = str(global_user_id) if global_user_id else None
        logger.info(
            f"[USER_RESOLVE] platform={platform} "
            f"platform_user_id={user_id} "
            f"global_user_id={user_data['global_user_id']} "
            f"phone={'***' if phone else None}"
        )
        # если телефона нет - запрашиваем его
        if not phone and not is_incoming_contact:
            logger.info(f"Запрос телефона [{platform}] user_id={user_id} (@{username})")
            keyboard = get_phone_keyboard(platform)
            text = (
                f"👋 Привет, {user_obj.first_name}!\n\n"
                f"Для связи с вами нам нужен ваш номер телефона.\n\n"
                f"Пожалуйста, нажмите кнопку ниже, чтобы поделиться номером.\n"
                f"Это нужно только для уведомлений о важных обновлениях."
            )
            await bot_res.send(text, menu=keyboard)
            return None, None
        
        return user_data, bot_res

    # Обработчик команды /start
    @message_decorator(Command("start"))
    @universal_handler
    async def start(event, ud, bot_res, **kwargs):
        """Обработчик /start"""
        global_user_id = ud.get("global_user_id")
        logger.info(f"Команда /start [{platform}] от user_id={global_user_id} (@{ud['username']})")
        
        await eventlogger.log_event(
            event_type="command_start",
            user_id=str(global_user_id),
            user_name=ud.get("username"),
            session_id=str(global_user_id),
            channel=platform
        )
        # На /start не вызываем ADK.
        # Только обновляем пользователя в БД 
        # и показываем стартовое меню.
        tree = await get_tree_cached()
        menu = build_universal_menu(tree, [], platform)
        text = get_start_message()
        await bot_res.send(text, menu=menu)

    # обработчик команды /version для получения версии
    @message_decorator(Command("version"))
    @universal_handler
    async def version_info(event, ud, bot_res, **kwargs):
        """Команда для получения версии платформы/бота"""
        global_user_id = ud.get("global_user_id")
        logger.info(f"Команда /version [{platform}] от user_id={global_user_id}")
        await eventlogger.log_event(
            event_type="command_version",
            user_id=str(global_user_id),
            user_name=ud.get("username"),
            session_id=str(global_user_id),
            channel=platform
        )
        
        msg_text = f"Текущая версия бота: {Settings.PLATFORM_VERSION}"
        await bot_res.send(msg_text)
    
    # домашняя страница
    @callback_decorator(home_filter)
    @universal_handler
    async def go_home(event, ud, bot_res, **kwargs):
        """Обработчик перехода на главную страницу"""
        tree = await get_tree_cached()
        menu = build_universal_menu(tree, [], platform)
        text = get_start_message()
        await bot_res.edit(text, menu=menu)

    # обработчик команды /reset для сброса истории и сессии
    @message_decorator(Command("reset"))
    @universal_handler
    async def reset(event, ud, bot_res, **kwargs):
        user_id = ud["user_id"]
        global_user_id = ud.get("global_user_id")
        username = ud["username"]
        session_id = str(global_user_id) if global_user_id else str(user_id)

        logger.info(f"Команда /reset [{platform}] от user_id={global_user_id} (@{username})")
        await eventlogger.log_event(
            event_type="command_reset",
            user_id=str(global_user_id),
            session_id=session_id,
            channel=platform
        )
        try:
            # Удаляем сессию в ADK (актуальная + legacy "default" от старых версий бота)
            await adk.delete_session(user_id=str(global_user_id), session_id=session_id)
            # Очищаем историю в БД
            await store.reset(user_id, global_user_id)
            # Удаляем состояние результатов поиска
            await store.reset_search_state(global_user_id, session_id)

            # После /reset не создаем новую ADK-сессию
            # и не вызываем set_user_state.
            # Новая session и state будут созданы при первом обычном сообщении.
            text = "✅ История диалога и сессия сброшены"
            await bot_res.send(text)
            logger.info(f"История и сессия сброшены для user_id={user_id}")

        except Exception as e:
            logger.error(f"Ошибка при сбросе: {e}", exc_info=True)
            await eventlogger.log_event(
                event_type="error",
                user_id=str(global_user_id),
                session_id=session_id,
                channel=platform,
                payload={
                    "error": str(e)
                }
            )
            err_text = "❌ Ошибка при сбросе истории"
            await bot_res.send(err_text)
        return
    
    # обработчик команды /help для отображения справки
    @message_decorator(Command("help"))
    @universal_handler
    async def help_cmd(event, ud, bot_res, **kwargs):
        global_user_id = ud.get("global_user_id")
        logger.info(f"Команда /help от user_id={global_user_id}")
        await eventlogger.log_event(event_type="command_help", user_id=str(global_user_id), channel=platform)
        help_text = get_help_message()
        await bot_res.send(help_text)

    # обработчик открытия папки в меню бота
    @callback_decorator(dir_filter)
    @universal_handler
    async def open_dir(event, ud, bot_res, **kwargs):
        """Команда обработчик открытия папки"""
        payload = bot_res.payload

        pid = payload.split(":", 1)[1]
        path = Settings.CALLBACK_MAP.get(pid)

        if not path:
            await bot_res.answer_callback("⚠️ Кнопка устарела. Возвращаю в начало...", show_alert=True)
            # Вместо edit делаем send, чтобы меню появилось внизу чата
            tree = await get_tree_cached()
            menu = build_universal_menu(tree, [], platform)
            text = get_start_message()
            return await bot_res.send(text, menu=menu)

        # Обновление кэш путей
        Settings.CALLBACK_MAP.move_to_end(pid)
        path_list = path.split("/") if path else []
        tree = await get_tree_cached()
        menu = build_universal_menu(tree, path_list, platform)
        title = "📁 /".join(path_list) or get_start_message()

        await bot_res.edit(title, menu=menu)

    # обработчик открытия файла в меню бота
    @callback_decorator(file_filter)
    @universal_handler
    async def send_file(event, ud, bot_res, **kwargs):
        """Обработчик отправки файлов через меню бота"""
        payload = bot_res.payload
        pid = payload.split(":", 1)[1]
        path = Settings.CALLBACK_MAP.get(pid)

        if not path:
            return await bot_res.answer_callback("Файл не найден")
        
        Settings.CALLBACK_MAP.move_to_end(pid)
        doc_id = await get_document_id(path)
        
        # Формируем URL
        if not doc_id:
            url = f"{Settings.KB_MANAGER_URL}/api/filesystem/download/?path={quote(path)}"
        else:
            url = f"{Settings.KB_MANAGER_URL}/api/documents/download/{doc_id}"
            
        filename = path.split("/")[-1]
        global_user_id = ud.get("global_user_id")

        logger.info(f"Запрос на скачивание файла через меню: {filename} (doc_id={doc_id}) от user_id={global_user_id}")
        await eventlogger.log_event(
            event_type="document_download_menu",
            user_id=str(global_user_id),
            session_id=str(global_user_id),
            user_name=ud.get("username"),
            channel=platform,
            payload={"filename": filename, "file_path": path, "doc_id": doc_id, "source": "menu"}
        )

        tmp_name = os.path.join(tempfile.gettempdir(), filename)
        try:
            # Скачиваем файл во временный буфер
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    with open(tmp_name, "wb") as f:
                        f.write(await resp.read())

            # Отправка файла
            await bot_res.send(
                text=f"📄 {filename}", 
                is_doc={'path': tmp_name, 'name': filename}
            )
            logger.info(f"✅ Файл отправлен: {filename}")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки файла: {e}", exc_info=True)
            await bot_res.send("Ошибка при загрузке файла")
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.remove(tmp_name)

    async def process_contact_logic(
        bot_res: BotResponse, 
        phone: str, 
        user_id: str, 
        username: str | None = None,
        first_name: str = "", 
        last_name: str | None = ""
    ):
        """Единая точка входа для сохранения контакта и приветствия"""
        
        # Сохраняем в БД
        await subscriber_store.update_phone(user_id, phone)
        # нормализуем телефон
        phone = normalize_phone(phone)
        logger.info(f"✅ Телефон получен [{platform}]: user_id={user_id}, {'***' if phone else None}")
        # обновляем global user
        global_user_id = None
        try:
            global_user_id = await user_resolver.resolve_user(
                platform=platform,
                platform_user_id=int(user_id),
                username=username,
                first_name=first_name,
                last_name=last_name,
                phone=phone
            )
        except Exception as e:
            logger.error(f"UserResolver contact update error: {e}", exc_info=True)

        logger.info(f"[CONTACT_RESOLVED] user_id={user_id} -> global_user_id={global_user_id}")

        await eventlogger.log_event(
            event_type="get_contact", 
            user_id=str(user_id),
            session_id=str(global_user_id) if global_user_id else str(user_id), 
            channel=platform
        )

        tree = await get_tree_cached()
        menu = build_universal_menu(tree, [], platform)
        
        await bot_res.send("✅ Спасибо! Теперь вы можете пользоваться ботом.")
        await bot_res.send(get_start_message(), menu=menu)

    # хендлер контакта для телеграмма:
    if is_tg:
        @message_decorator(F.contact)
        @universal_handler
        async def handle_contact_tg(m, ud, bot_res, **kwargs):
            if m.contact.user_id != m.from_user.id:
                return await m.answer("⚠️ Пожалуйста, отправьте свой номер телефона")
            await process_contact_logic(
                bot_res=bot_res,
                phone=m.contact.phone_number, 
                user_id=m.from_user.id,
                username=m.from_user.username,
                first_name=m.from_user.first_name or "",
                last_name=m.from_user.last_name or ""
            )
    else: 
        # обработчик получения контакта (номера телефона)
        async def handle_contact_max(bot_res: BotResponse, ud, contact: Contact):
            """Обработчик полученного контакта — извлекает телефон из vCard"""
            user_id = ud["user_id"]
            # Достаем имена из user_data (ud)
            username = ud.get("username")
            first_name = ud.get("first_name", "")
            last_name = ud.get("last_name", "")
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
                    await bot_res.answer_callback("⚠️ Не удалось получить номер. Попробуйте ещё раз.")
                    return
                phone = f"+{phone}"
                logger.info(f"✅ Телефон извлечён: {'***' if phone else None}")
                await process_contact_logic(
                    bot_res=bot_res, 
                    phone=phone, 
                    user_id=user_id,
                    username=username,
                    first_name=first_name,
                    last_name=last_name
                )
            except Exception as e:
                logger.error(f"❌ Ошибка обработки контакта: {e}", exc_info=True)
                await bot_res.answer_callback("⚠️ Произошла ошибка. Попробуйте ещё раз.")
                await eventlogger.log_event(
                    event_type="error",
                    user_id=str(user_id),
                    session_id=str(user_id),
                    channel=platform,
                    payload={
                        "error": str(e)
                    }
                )
    # хендлер текста
    @message_decorator()
    @universal_handler
    async def on_text(event, ud, bot_res, **kwargs):
        # Извлечение данных пользователя
        user_id = ud["user_id"]
        global_user_id = ud.get("global_user_id")
        user_text = bot_res.text
        #  Проверка контакта для Max (он шлет его внутри обычного сообщения)
        if platform == "max" and bot_res.attachments:
            for att in bot_res.attachments:
                if getattr(att, 'type', None) == 'contact':
                    logger.info("Контакт обнаружен, обрабатываем...")
                    return await handle_contact_max(bot_res, ud, att)

        # проверка наличия global_user_id
        if not global_user_id:
            logger.warning(f"Skip message — no global_user_id for user_id={user_id}")
            return
        if platform == "max":

            # пустые/service сообщения
            if not user_text.strip():
                logger.info("Skip MAX empty/service event")
                return

            # сообщения самого бота/system
            sender = getattr(event.message, "sender", None)

            if sender:

                sender_type = getattr(sender, "type", None)

                logger.info(f"MAX sender_type={sender_type}")

                if sender_type in ("bot", "system"):
                    logger.info(f"Skip MAX internal sender_type={sender_type}")
                    return
        # Если это не контакт — проверяем авторизацию как обычно
        
        session_id = str(global_user_id)
        turn_id = str(uuid.uuid4())
        start_time = time.time()

        # логируем скорость ответа
        logger.info(f"📨 Сообщение [{platform}] от user_id={global_user_id} (@{ud['username']}): {user_text[:100]}")
        await eventlogger.log_event(
            event_type="message_received", user_id=str(global_user_id), 
            user_name=ud.get("username"), session_id=session_id, 
            channel=platform, payload={"text": user_text, "turn_id": turn_id, "start_time": start_time}
        )

        try:
            adk_user_id = str(global_user_id) if global_user_id else str(user_id)
            await adk.ensure_session(user_id=adk_user_id, session_id=adk_user_id)

            # Пагинация и скачивание по номеру — из БД, без вызова ADK
            """
            Основной блок обработки пользовательских текстовых сообщений.
            Здесь происходит разбор запросов на постраничный просмотр/загрузку файлов,
            поиск по базе знаний (через ADK), а также отправка найденных документов.

            Весь блок обрабатывается внутри try, чтобы корректно залогировать и обработать любые ошибки.
            """
            # --- Пагинация: показать следующую порцию сохранённого списка документов ---
            if Settings.SHOW_MORE_RE.match(user_text):
                ok = await handle_show_more(event=event, store=store, user_id=global_user_id, session_id=session_id, turn_id=turn_id, start_time=start_time, platform=platform)
                if not ok:
                    # Сообщение для пользователя, если списка нет
                    response_time = int((time.time() - start_time) * 1000)
                    answer = "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                    # await send_answer(event, answer)
                    await bot_res.send(answer)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(global_user_id),
                        session_id=session_id,
                        channel=platform,
                        payload={
                            "turn_id": turn_id,
                            "text": answer, 
                            "response_time_ms": response_time
                        }    
                    )
                # Логируем пользовательский запрос и результат в историю
                await store.append(user_id, "user", user_text, global_user_id)
                await store.append(
                    user_id,
                    "model",
                    "Показана следующая порция списка документов."
                    if ok
                    else "Список документов не найден.",
                    global_user_id
                )
                return
            # --- Пагинация: показать полный список сохранённых документов ---
            if Settings.SHOW_ALL_RE.match(user_text) and not Settings.SHOW_MORE_RE.match(user_text):
                # Только если это не "показать еще"
                ok = await handle_show_all(event, store, global_user_id, session_id, turn_id, start_time, platform)
                if not ok:
                    response_time = int((time.time() - start_time) * 1000)
                    answer = "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                    await bot_res.send(answer)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(global_user_id),
                        session_id=session_id,
                        channel=platform,
                        payload={
                            "turn_id": turn_id,
                            "text": answer, 
                            "response_time_ms": response_time
                        }    
                    )
                await store.append(user_id, "user", user_text, global_user_id)
                await store.append(
                    user_id,
                    "model",
                    "Показан полный список документов."
                    if ok
                    else "Список документов не найден.",
                    global_user_id
                )
                return
            
            # --- Обработка запроса на скачивание файлов по номерам из списка ---
            dl_ranks = parse_download_ranks(user_text)
            if dl_ranks:
                """
                Если пользователь ввёл запрос, похожий на "скачать документы под номерами ...",
                вызываем обработчик отправки файлов.
                """
                await handle_download_by_ranks(event, store, doc_handler, global_user_id, session_id, dl_ranks, turn_id, start_time, platform)
                await store.append(user_id, "user", user_text, global_user_id)
                await store.append(user_id, "model", "Запрошена отправка файлов по номерам из списка.", global_user_id)
                return

            # Синхронизируем профиль пользователя в ADK перед run()
            await sync_user_profile_to_adk(adk, subscriber_store, int(user_id), session_id, global_user_id)
            
            # --- Получаем информацию о последнем поиске перед текущим запросом (для контроля смены поиска) ---
            meta_before = await store.get_last_search_meta(global_user_id, session_id)
            search_id_before = meta_before["search_id"] if meta_before else None
            # инициализация статуса "печатает..." для пользователя
            await bot_res.start_typing()
            # --- Общий запрос к ADK: поиск и формирование ответа для пользователя ---
            answer, events = await adk.run(user_id=adk_user_id, session_id=adk_user_id, text=user_text)
            response_time = int((time.time() - start_time) * 1000)
            work = answer or ""

            # сохраняем историю диалога
            await store.append(user_id, "user", user_text, global_user_id)
            await store.append(user_id, "model", answer, global_user_id)

            bot_action = extract_bot_action(events)
            if isinstance(bot_action, dict) and bot_action.get("type") == "send_product_kit":
                if work.strip():
                    is_already_html = "<b>" in work or work.lstrip().startswith("<")
                    final_text = work if is_already_html else markdown_to_safe_html(work)
                    await bot_res.send(final_text)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(global_user_id),
                        session_id=session_id,
                        channel=platform,
                        payload={
                            "turn_id": turn_id,
                            "text": final_text,
                            "response_time_ms": response_time,
                        },
                    )

                await handle_product_kit_action(
                    bot_res=bot_res,
                    bot_action=bot_action,
                    user_id=global_user_id,
                    session_id=session_id,
                    turn_id=turn_id,
                    start_time=start_time,
                    platform=platform,
                )
                return

            # Новый поиск документов: список в БД — признак смены search_id, первая порция рендерится здесь
            meta_after = await store.get_last_search_meta(global_user_id, session_id)
            search_id_after = meta_after["search_id"] if meta_after else None
            
            if search_id_after and search_id_after != search_id_before and meta_after:
                items = await store.get_last_search_results(global_user_id, session_id)
                if items:
                    shown = min(max(int(meta_after.get("shown_count", 5)), 0), len(items))
                    text_list = render_results(items[:shown], total=len(items), offset=0)
                    
                    await bot_res.send(text_list) # Используем наш хелпер!
                    response_time = int((time.time() - start_time) * 1000)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(global_user_id),
                        session_id=session_id,
                        channel=platform,
                        payload={
                            "turn_id": turn_id,
                            "text": text_list,
                            "response_time_ms": response_time
                        }    
                    )
                    return

            # 2. Если это просто текстовый ответ от нейронки
            if work and work.strip():
                # Проверяем, нужно ли конвертировать Markdown в HTML
                is_already_html = "<b>" in work or work.lstrip().startswith("<")
                final_text = work if is_already_html else markdown_to_safe_html(work)
                response_time = int((time.time() - start_time) * 1000)
                # Отправляем одной командой для любой платформы!
                await bot_res.send(final_text)
                
                await eventlogger.log_event(
                    event_type="response", user_id=str(global_user_id),
                    session_id=session_id, channel=platform,
                    payload={"turn_id": turn_id, "text": final_text, "response_time_ms":response_time}
                )
        except Exception as e:
            logger.error(f" ❌ Ошибка обработки сообщения на платформе: [{platform}] от user_id={global_user_id}: {e}", exc_info=True)
            await eventlogger.log_event(
                event_type="error",
                user_id=str(global_user_id),
                session_id=session_id,
                channel=platform,
                payload={
                    "error": str(e)
                }
            )
            await bot_res.send("😔 Произошла ошибка при обработке запроса.\n Попробуйте позже или используйте /reset для сброса диалога.")
        finally:
            await bot_res.stop_typing()

# --- Универсальный Адаптер Ответов ---
class BotResponse:
    """Адаптер для унификации ответов на разных платформах"""
    def __init__(self, event, platform):
        self.event = event
        self.platform = platform
        self.is_tg = platform == "telegram"
        self._typing_task=None

    @property
    def payload(self):
        """Универсальный способ получить данные callback-кнопки"""
        if self.is_tg:
            return getattr(self.event, 'data', None)
        else:
            # Для Max API данные лежат в callback.payload
            return getattr(self.event.callback, 'payload', None)
    
    @property
    def text(self):
        if self.is_tg:
            return (self.event.text or "").strip()
        return (self.event.message.body.text or "").strip()

    @property
    def attachments(self):
        """Возвращает вложения (только для Max)"""
        if self.is_tg:
            return []
        return getattr(self.event.message.body, 'attachments', [])

    async def send(self, text, menu=None, is_html=True, is_doc=None):
        
        # Перед отправкой ответа выключаем typing
        # await self.stop_typing()
        
        if is_doc:
            return await self._send_document(is_doc['path'], is_doc['name'])
        
        # Обработка клавиатуры для TG (удаление, если нужно)
        reply_markup = menu
        if self.is_tg and not menu and "Спасибо" in text:
            reply_markup = ReplyKeyboardRemove()

        if self.is_tg:
            return await self.event.answer(
                text, 
                reply_markup=reply_markup, 
                parse_mode="HTML" if is_html else None
            )
        else:
            return await self.event.message.answer(
                text=text,
                attachments=[menu] if menu else [],
                format=TextFormat.HTML if is_html else None
            )

    async def edit(self, text, menu=None, is_html=True):
        """Редактирует сообщение и подтверждает callback (для TG)"""
        try:
            if self.is_tg:
                # В TG нужно подтверждать callback, если это кнопка
                if hasattr(self.event, 'answer'):
                    await self.event.answer()
                return await self.event.message.edit_text(
                    text, reply_markup=menu, parse_mode="HTML" if is_html else None
                )
            else:
                return await self.event.message.edit(
                    text=text, attachments=[menu] if menu else []
                )
        except Exception as e:
            logger.warning(f"Could not edit message, sending new one: {e}")
            # Если редактирование не прошло (сообщение старое или удалено), просто шлем новое
            return await self.send(text, menu=menu, is_html=is_html)

    def _get_message(self):
        if self.is_tg:
            if hasattr(self.event, "message"):
                return self.event.message
            return self.event
        return self.event.message

    async def _send_document(self, path, filename):
        msg = self._get_message()

        if self.is_tg:
            return await msg.answer_document(
                FSInputFile(path, filename=filename)
            )

        return await msg.answer(
            attachments=[InputMedia(path=path)]
        )
        # if self.is_tg:
        #     return await self.event.message.answer_document(FSInputFile(path, filename=filename))
        # else:
        #     return await self.event.message.answer(attachments=[InputMedia(path=path)])

    async def answer_callback(self, text=None, show_alert=False):
        """Для всплывающих окон в TG или уведомлений в Max"""
        if self.is_tg and hasattr(self.event, 'answer'):
            await self.event.answer(text, show_alert=show_alert)
        elif not self.is_tg:
            if text:
                # Если передан флаг alert, добавляем эмодзи для заметности
                msg = f"⚠️ {text.upper()}" if show_alert else text
                await self.event.answer(msg)

    async def _send_typing_once(self):
        try:
            if self.is_tg:
                chat = self.event.chat
                await chat.do("typing")

            else:
                # MAX API
                chat_id = self.event.message.recipient.chat_id

                await self.event.bot.send_action(
                    chat_id=chat_id,
                    action=SenderAction.TYPING_ON
                )
                logger.info(f"MAX typing sent to chat_id={chat_id}")

        except Exception as e:
            logger.debug(f"Typing status error: {e}")

    async def _typing_loop(self):
        """Фоновый loop отправки typing status"""

        while True:
            await self._send_typing_once()
            # Telegram typing живет ~5 секунд
            # поэтому обновляем каждые 4
            await asyncio.sleep(4)

    async def start_typing(self):
        """Запуск typing-индикатора"""
        # Telegram требует постоянного обновления typing
        if self.is_tg:

            if self._typing_task is None or self._typing_task.done():

                self._typing_task = asyncio.create_task(
                    self._typing_loop()
                )

        else:
            # MAX достаточно одного action
            await self._send_typing_once()
        
    async def stop_typing(self):
        """Остановка typing-индикатора"""

        if self._typing_task:
            self._typing_task.cancel()

            with contextlib.suppress(asyncio.CancelledError):
                await self._typing_task

            self._typing_task = None
