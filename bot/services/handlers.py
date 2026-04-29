import os
import aiohttp
import time
from urllib.parse import quote
from aiogram.types import ( FSInputFile,
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

from bot.services.utils import (
    markdown_to_safe_html,
    render_results,
    handle_show_more,
    handle_show_all,
    handle_download_by_ranks,
    get_document_id,
    get_kb_tree,
    build_universal_menu
)
import uuid
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from maxapi.types import MessageCreated, InputMedia, RequestContactButton
import re

logger = setup_logger('handlers', 'handlers.log')
# инициализируем логер событий
eventlogger = EventLogger()
# переменные для сохранения дерева папок в кэше
TREE_CACHE = None
TREE_TS = 0
# Клавиатура для запроса телефона (показывается только если телефона нет)
PHONE_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text="📱 Поделиться номером телефона", request_contact=True)]
    ],
    resize_keyboard=True,
    one_time_keyboard=True
)
# синхронизация пользователей с адк 
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
    
async def get_authenticated_user(event, subscriber_store, platform: str = "telegram") -> dict | None:
    """
    Универсальная проверка регистрации.
    platform: "telegram" или "max"
    Проверяет регистрацию и наличие телефона.
    Если телефона нет — отправляет запрос и возвращает None.
    Если всё ок — возвращает словарь с готовыми данными.
    """
    # 1. Извлечение данных пользователя (Адаптер)
    if platform == "telegram":
        user_obj = event.from_user
        user_id = int(user_obj.id)
        # В телеграме метод ответа вызывается у самого сообщения
        answer_func = event.answer 
        kb_param = "reply_markup"
    else:
        # Для Max API логика 
        user_obj = event.callback.user if hasattr(event, 'callback') else event.from_user
        user_id = int(user_obj.user_id)
        # В Max API ответ идет через event.message
        answer_func = event.message.answer
        kb_param = "attachments"

    username = user_obj.username or "unknown"
    first_name = user_obj.first_name or "Гость"
    last_name = getattr(user_obj, 'last_name', None)
    
    # 2. Работа с БД (общая логика)
    existing_phone = await subscriber_store.get_phone(user_id)
    
    await subscriber_store.add(
        user_id=user_id,
        username=username,
        first_name=first_name,
        last_name=last_name,
        last_seen=datetime.now(),
        phone_number=None, 
        platform=platform
    )
    
    user_data = await subscriber_store.get_user_data(user_id)

    # 3. Если телефона нет — запрашиваем
    if not existing_phone:
        logger.info(f"Запрос телефона [{platform}] user_id={user_id} (@{username})")
        
        text = (
            f"👋 Привет, {first_name}!\n\n"
            f"Для связи с вами нам нужен ваш номер телефона.\n\n"
            f"Пожалуйста, нажмите кнопку ниже, чтобы поделиться номером.\n"
            f"Это нужно только для уведомлений о важных обновлениях."
        )
        
        keyboard = get_phone_keyboard(platform)
        
        # Динамически передаем клавиатуру в нужный именованный аргумент
        await answer_func(text, **{kb_param: [keyboard] if platform == "max" else keyboard})
        
        return None

    return user_data

# Вспомогательная функция для получения ID пользователя
def get_uid(event):
    user = getattr(event, 'from_user', None) or getattr(event, 'callback', event).user
    return getattr(user, 'id', getattr(user, 'user_id', None))
######################################
# обработчики сообщений и команд бота
######################################
def register_handlers(dp, store, subscriber_store, adk, doc_handler, get_start_message, platform="telegram") -> None:
    """Регистрация всех обработчиков сообщений универсальная для разных платформ"""
    # 1. Адаптация декораторов и фильтров под платформу
    if platform == "telegram":
        from aiogram.filters import Command
        from aiogram import F
        from aiogram.types import ReplyKeyboardRemove
        message_decorator = dp.message
        callback_decorator = dp.callback_query
        home_filter = (F.data == "home")
        dir_filter = F.data.startswith("d:")
        file_filter = F.data.startswith("f:")
    else:
        from maxapi import F
        from maxapi.types import Command
        from maxapi.enums import TextFormat
        from maxapi.types.attachments import Contact
        message_decorator = dp.message_created
        callback_decorator = dp.message_callback
        home_filter = (F.callback.payload == "home")
        dir_filter = F.callback.payload.startswith("d:")
        file_filter = F.callback.payload.startswith("f:")

    # Обработчик команды /start
    @message_decorator(Command("start"))
    async def start(event):
        """Обработчик /start"""
        user = await get_authenticated_user(event, subscriber_store, platform)
        if not user:
            return 
            
        user_id = user["user_id"]
        logger.info(f"Команда /start [{platform}] от user_id={user_id} (@{user['username']})")
        
        await eventlogger.log_event(
            event_type="command_start",
            user_id=str(user_id),
            user_name=user.get("username"),
            session_id=str(user_id),
            channel=platform
        )
        # На /start не вызываем ADK.
        # Только обновляем пользователя в БД через get_authenticated_user()
        # и показываем стартовое меню.
        tree = await get_tree_cached()
        menu = build_universal_menu(tree, [], platform)
        text = get_start_message()
        
        if platform == "telegram":
            await event.answer(text, reply_markup=menu)
        else:
            await event.message.answer(text=text, attachments=[menu])

    # обработчик команды /version для получения версии
    @message_decorator(Command("version"))
    async def version_info(event):
        """Команда для получения версии платформы/бота"""
        # Унифицируем получение ID (в TG это .id, в Max это .user_id)
        user_obj = event.from_user
        user_id = getattr(user_obj, 'id', getattr(user_obj, 'user_id', None))
        
        logger.info(f"Команда /version [{platform}] от user_id={user_id}")
        await eventlogger.log_event(
            event_type="command_version",
            user_id=str(user_id),
            session_id=str(user_id),
            channel=platform
        )
        
        msg_text = f"Текущая версия бота: {Settings.PLATFORM_VERSION}"
        if platform == "telegram":
            await event.answer(msg_text)
        else:
            await event.message.answer(msg_text)
    
    # домашняя страница
    @callback_decorator(home_filter)
    async def go_home(event):
        """Обработчик перехода на главную страницу"""
        if platform == "telegram":
            await event.answer() # Подтверждаем callback в TG

        tree = await get_tree_cached()
        menu = build_universal_menu(tree, [], platform)
        text = get_start_message()
        
        if platform == "telegram":
            await event.message.edit_text(text, reply_markup=menu)
        else:
            await event.message.edit(text=text, attachments=[menu])

    # обработчик команды /reset для сброса истории и сессии
    @message_decorator(Command("reset"))
    async def reset(event):
        user_id = get_uid(event)
        username = event.from_user.username or "unknown"
        session_id = str(user_id)

        logger.info(f"Команда /reset [{platform}] от user_id={user_id} (@{username})")
        await eventlogger.log_event(
            event_type="command_reset",
            user_id=str(user_id),
            session_id=session_id,
            channel=platform
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
            text = "✅ История диалога и сессия сброшены"
            if platform == "telegram":
                await event.answer(text)
            else:
                await event.message.answer(text)
            logger.info(f"История и сессия сброшены для user_id={user_id}")

        except Exception as e:
            logger.error(f"Ошибка при сбросе: {e}", exc_info=True)
            await eventlogger.log_event(
                event_type="error",
                user_id=str(user_id),
                session_id=session_id,
                channel=platform,
                payload={
                    "error": str(e)
                }
            )
            err_text = "❌ Ошибка при сбросе истории"
            if platform == "telegram":
                await event.answer(err_text)
            else:
                await event.message.answer(err_text)
        return
    
    # обработчик команды /help для отображения справки
    @message_decorator(Command("help"))
    async def help_cmd(event):
        user_id = get_uid(event)
        logger.info(f"Команда /help от user_id={user_id}")
        await eventlogger.log_event(event_type="command_help", user_id=str(user_id), channel=platform)
        
        help_text = (
            "ℹ️ Я помогу найти информацию в базе знаний.\n\n"
            "Просто напиши свой вопрос, и я постараюсь найти ответ!\n\n"
            "Команды:\n"
            "/start — начать работу\n"
            "/reset — сбросить историю\n"
            "/help — эта справка"
        )
        if platform == "telegram":
            await event.answer(help_text)
        else:
            await event.message.answer(help_text)

    # обработчик открытия папки в меню бота
    @callback_decorator(dir_filter)
    async def open_dir(event):
        """Команда обработчик открытия папки"""
        if platform == "telegram":
            await event.answer()
            payload = event.data
        else:
            payload = event.callback.payload

        pid = payload.split(":", 1)[1]
        path = Settings.CALLBACK_MAP.get(pid)

        if path is None:
            msg = "Кнопка устарела"
            if platform == "telegram":
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

        Settings.CALLBACK_MAP.move_to_end(pid)
        path_list = path.split("/") if path else []
        tree = await get_tree_cached()
        menu = build_universal_menu(tree, path_list, platform)
        title = "📁 /".join(path_list) or get_start_message()

        if platform == "telegram":
            await event.message.edit_text(title, reply_markup=menu)
        else:
            await event.message.edit(text=title, attachments=[menu])

    # обработчик открытия файла в меню бота
    @callback_decorator(file_filter)
    async def send_file(event):
        """Обработчик отправки файлов через меню бота"""
        if platform == "telegram":
            await event.answer()
            payload = event.data
            user_info = event.from_user
        else:
            payload = event.callback.payload
            user_info = event.callback.user

        pid = payload.split(":", 1)[1]
        path = Settings.CALLBACK_MAP.get(pid)
        
        if not path:
            msg = "Файл не найден"
            if platform == "telegram":
                await event.answer(msg, show_alert=True)
            else:
                await event.answer(msg)
            return

        Settings.CALLBACK_MAP.move_to_end(pid)
        doc_id = await get_document_id(path)
        
        # Формируем URL
        if not doc_id:
            url = f"{Settings.KB_MANAGER_URL}/api/filesystem/download/?path={quote(path)}"
        else:
            url = f"{Settings.KB_MANAGER_URL}/api/documents/download/{doc_id}"
            
        filename = path.split("/")[-1]
        user_id = get_uid(event)

        logger.info(f"Запрос на скачивание файла через меню: {filename} (doc_id={doc_id}) от user_id={user_id}")
        await eventlogger.log_event(
            event_type="document_download_menu",
            user_id=str(user_id),
            session_id=str(user_id),
            user_name=user_info.username,
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
            if platform == "telegram":
                await event.message.answer_document(
                    document=FSInputFile(tmp_name, filename=filename)
                )
            else:
                await event.message.answer(
                    attachments=[InputMedia(path=tmp_name)]
                )
            logger.info(f"✅ Файл отправлен: {filename}")

        except Exception as e:
            logger.error(f"❌ Ошибка отправки файла: {e}", exc_info=True)
            if platform == "telegram":
                await event.answer("Ошибка загрузки файла", show_alert=True)
            else:
                await event.message.answer("❌ Ошибка при загрузке файла")
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.remove(tmp_name)

    # --- Вспомогательная функция для отправки ответов
    async def send_answer(event, text, menu=None, is_html=True):
        if platform == "telegram":
            return await event.answer(
                text, 
                reply_markup=menu or (ReplyKeyboardRemove() if "Спасибо" in text else None), 
                parse_mode="HTML" if is_html else None
            )
        else:
            attachments = [menu] if menu else []
            return await event.message.answer(
                text=text, 
                attachments=attachments, 
                format=TextFormat.HTML if is_html else None
            ) 
    # логика обработки контакта
    async def save_contact_and_welcome(event, user_id, phone):
        # Сохраняем телефон в БД
        await subscriber_store.update_phone(user_id, phone)
        logger.info(f"✅ Телефон получен [{platform}]: {phone}")
        
        await eventlogger.log_event(
            event_type="get_contact", user_id=str(user_id), 
            session_id=str(user_id), channel=platform
        )

        tree = await get_tree_cached()
        menu = build_universal_menu(tree, [], platform)
        
        await send_answer(event, "✅ Спасибо! Теперь вы можете пользоваться ботом.")
        await send_answer(event, get_start_message(), menu=menu)

    # хендлер контакта для телеграмма:
    if platform == "telegram":
        @dp.message(F.contact)
        async def handle_contact_tg(m):
            if m.contact.user_id != m.from_user.id:
                return await m.answer("⚠️ Пожалуйста, отправьте свой номер телефона")
            await save_contact_and_welcome(m, m.from_user.id, m.contact.phone_number)
    else: 
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
                menu = build_universal_menu(tree, [], "max")
                
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
    # хендлер текста
    @message_decorator()
    async def on_text(event):
        # Извлечение данных пользователя
        user_obj = event.from_user
        if platform == "telegram":
            user_id = user_obj.id
            user_text = (event.text or "").strip()
        else:
            user_id = user_obj.user_id
            user_text = (event.message.body.text or "").strip()

        #  Проверка контакта для Max (он шлет его внутри обычного сообщения)
        if platform == "max" and event.message.body and event.message.body.attachments:
            for att in event.message.body.attachments:
                if hasattr(att, 'type') and att.type == 'contact':
                    logger.info("Контакт обнаружен, обрабатываем...")
                    await handle_contact_received(event, att)
                    return

        # Если это не контакт — проверяем авторизацию как обычно
        user = await get_authenticated_user(event, subscriber_store, platform)
        if not user or not user_text:
            return

        session_id = str(user_id)
        turn_id = str(uuid.uuid4())
        start_time = time.time()

        # логируем скорость ответа
        logger.info(f"📨 Сообщение [{platform}] от user_id={user_id} (@{user['username']}): {user_text[:100]}")
        await eventlogger.log_event(
            event_type="message_received", user_id=str(user_id), 
            user_name=user.get("username"), session_id=session_id, 
            channel=platform, payload={"text": user_text, "turn_id": turn_id, "start_time": start_time}
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
                ok = await handle_show_more(event=event, store=store, user_id=user_id, session_id=session_id, turn_id=turn_id, start_time=start_time, platform=platform)
                if not ok:
                    # Сообщение для пользователя, если списка нет
                    response_time = int((time.time() - start_time) * 1000)
                    answer = "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                    # await send_answer(event, answer)
                    if platform == "telegram":
                        await event.answer(answer)
                    else:
                        await event.message.answer(answer)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(user_id),
                        session_id=session_id,
                        channel=platform,
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
                ok = await handle_show_all(event, store, user_id, session_id, turn_id, start_time, platform)
                if not ok:
                    response_time = int((time.time() - start_time) * 1000)
                    answer = "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                    # await send_answer(event, answer)
                    if platform == "telegram":
                        await event.answer(answer)
                    else:
                        await event.message.answer(answer)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(user_id),
                        session_id=session_id,
                        channel=platform,
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
                await handle_download_by_ranks(event, store, doc_handler, user_id, session_id, dl_ranks, turn_id, start_time, platform)
                await store.append(user_id, "user", user_text)
                await store.append(user_id, "model", "Запрошена отправка файлов по номерам из списка.")
                return

            # Синхронизируем профиль пользователя в ADK перед run()
            await sync_user_profile_to_adk(adk, subscriber_store, int(user_id), session_id)
            
            # --- Получаем информацию о последнем поиске перед текущим запросом (для контроля смены поиска) ---
            meta_before = await store.get_last_search_meta(user_id, session_id)
            search_id_before = meta_before["search_id"] if meta_before else None

            # --- Общий запрос к ADK: поиск и формирование ответа для пользователя ---
            answer, _ = await adk.run(user_id=str(user_id), session_id=session_id, text=user_text)
            response_time = int((time.time() - start_time) * 1000)
            logger.info(f"📤 Ответ для user_id={user_id}: {answer[:100]}")
            # сохраняем в логах событие ответа и его латентность
            await eventlogger.log_event(
                event_type="response",
                user_id=str(user_id),
                session_id=session_id,
                channel=platform,
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
            
            if search_id_after and search_id_after != search_id_before and meta_after:
                items = await store.get_last_search_results(user_id, session_id)
                if items:
                    shown = min(max(int(meta_after.get("shown_count", 5)), 0), len(items))
                    text_list = render_results(items[:shown], total=len(items), offset=0)
                    
                    await send_answer(event, text_list) # Используем наш хелпер!
                    response_time = int((time.time() - start_time) * 1000)
                    await eventlogger.log_event(
                        event_type="response",
                        user_id=str(user_id),
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
                await send_answer(event, final_text)
                
                await eventlogger.log_event(
                    event_type="response", user_id=str(user_id),
                    session_id=session_id, channel=platform,
                    payload={"turn_id": turn_id, "text": final_text, "response_time_ms":response_time}
                )
        except Exception as e:
            logger.error(f" ❌ Ошибка обработки сообщения на платформе: [{platform}] от user_id={user_id}: {e}", exc_info=True)
            await eventlogger.log_event(
                event_type="error",
                user_id=str(user_id),
                session_id=session_id,
                channel=platform,
                payload={
                    "error": str(e)
                }
            )
            # await send_answer(event, "😔 Произошла ошибка при обработке запроса.\n Попробуйте позже или используйте /reset для сброса диалога.")
            if platform == "telegram":
                await event.answer("😔 Произошла ошибка при обработке запроса.\n Попробуйте позже или используйте /reset для сброса диалога.")
            else:
                await event.message.answer("😔 Произошла ошибка при обработке запроса.\n Попробуйте позже или используйте /reset для сброса диалога.")