import os
import json
import aiohttp
import time
from urllib.parse import quote
from aiogram import Dispatcher, F
from aiogram.filters import Command
from aiogram.types import (
    Message, FSInputFile, CallbackQuery, ReplyKeyboardRemove, 
    ReplyKeyboardMarkup, KeyboardButton
    ) 
import tempfile
from datetime import datetime
from utils import setup_logger
# импортируем конфиг
from bot.services.config import Settings
#  импортируем функции вспомогательные для бота
from bot.services.utils import (
    markdown_to_safe_html, parse_download_ranks, render_results, extract_bot_contract, 
    normalize_contract_results, extract_search_results_from_events, handle_download_by_ranks,
    handle_show_all, handle_show_more, build_menu_from_tree, get_document_id, get_kb_tree)

logger = setup_logger('handlers', 'handlers.log')
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
######################################
# обработчики сообщений и команд бота
######################################
def register_handlers(dp: Dispatcher, store, subscriber_store, adk, doc_handler, TITLE_START) -> None:
    """Регистрация всех обработчиков сообщений"""
    # Обработчик команды /start
    @dp.message(Command("start"))
    async def start(m: Message) -> None:
        user = await get_authenticated_user(m, subscriber_store)
        if not user: 
            return
        user_id = user["user_id"]
        logger.info(f"Команда /start от user_id={user_id} (@{user['username']}) - телефон уже есть.")
        # Если телефон есть - загружаем данные в ADK
        session_id = f"session-{user_id}"
        await adk.ensure_session(user_id=str(user_id), session_id=session_id)
        await adk.set_user_state(str(user_id), session_id, user)
        logger.info(f"📋 Данные пользователя загружены в ADK: {user['phone_number']}")
    
        # строим меню для ответа
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])
        await m.answer(TITLE_START, reply_markup=menu)           

    # обработчик получения контакта (номера телефона)
    @dp.message(F.contact)
    async def handle_contact(m: Message) -> None:
        """Обработка получения номера телефона"""
        if not m.contact:
            return
        
        user_id = m.from_user.id
        # Проверяем, что пользователь отправил свой контакт
        if m.contact.user_id != user_id:
            await m.answer("⚠️ Пожалуйста, отправьте свой номер телефона")
            return
        
        phone = m.contact.phone_number
        
        # Сохраняем телефон в БД
        await subscriber_store.update_phone(user_id, phone)
        
        logger.info(f"✓ Получен телефон от user_id={user_id}.")
        # Создаём сессию и загружаем данные в ADK
        session_id = f"session-{user_id}"
        await adk.ensure_session(user_id=str(user_id), session_id=session_id)
        
        user_data = await subscriber_store.get_user_data(user_id)
        if user_data:
            await adk.set_user_state(str(user_id), session_id, user_data)
        
        # Убираем клавиатуру и показываем меню
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])
        
        await m.answer(
            "✅ Спасибо! Теперь вы можете пользоваться ботом.",
            reply_markup=ReplyKeyboardRemove()
        )
        await m.answer(TITLE_START, reply_markup=menu)

    # обработчик команды /version для получения версии
    @dp.message(Command("version"))
    async def version_info(m: Message) -> None:
        """Команда для получения версии платформы/бота"""
        user_id = m.from_user.id
        logger.info(f"Команда /version от user_id={user_id}")
        await m.answer(
            f"Текущая версия бота: {Settings.PLATFORM_VERSION}"
        )

    #домашняя страница
    @dp.callback_query(lambda c: c.data == "home")
    async def go_home(callback: CallbackQuery):
        # обработчик перехода на главную страницу кнопка home
        await callback.answer()
        # строим меню для ответа
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])

        await callback.message.edit_text(
            TITLE_START,
            reply_markup=menu
        )

    # обработчик команды /reset для сброса истории и сессии
    @dp.message(Command("reset"))
    async def reset(m: Message) -> None:
        user_id = m.from_user.id
        username = m.from_user.username or "unknown"
        session_id = "default"
        
        logger.info(f"Команда /reset от user_id={user_id} (@{username})")
        
        try:
            # Удаляем сессию в ADK
            await adk.delete_session(user_id=str(user_id), session_id=session_id)
            
            # Создаём новую сессию
            await adk.ensure_session(user_id=str(user_id), session_id=session_id)
            
            # Очищаем историю в БД
            await store.reset(user_id)
            
            # Удаляем состояние результатов поиска
            await store.reset_search_state(user_id, session_id)
            
            # после reset сразу вернуть профиль в state
            user_data = await subscriber_store.get_user_data(user_id)
            if user_data:
                await adk.set_user_state(str(user_id), session_id, user_data)
                
            await m.answer("✅ История диалога и сессия сброшены")
            logger.info(f"История и сессия сброшены для user_id={user_id}")
        except Exception as e:
            logger.error(f"Ошибка при сбросе: {e}", exc_info=True)
            await m.answer("❌ Ошибка при сбросе истории")
    
    #  обработчик команды /help для отображения справки
    @dp.message(Command("help"))
    async def help_cmd(m: Message) -> None:
        user_id = m.from_user.id
        logger.info(f"Команда /help от user_id={user_id}")
        
        await m.answer(
            "ℹ️ Я помогу найти информацию в базе знаний.\n\n"
            "Просто напиши свой вопрос, и я постараюсь найти ответ!\n\n"
            "Команды:\n"
            "/start — начать работу\n"
            "/reset — сбросить историю\n"
            "/help — эта справка"
        )
    
    # обработчик всех текстовых сообщений (основной диалог)
    @dp.message(F.text)
    async def on_text(m: Message) -> None:
        user = await get_authenticated_user(m, subscriber_store)
        if not user:
            return
        user_id = user["user_id"]
        session_id = f"session-{user_id}"
        user_text = (m.text or "").strip()

        if not user_text:
            return

        logger.info(f"📨 Сообщение от user_id={user_id} (@{user['username']}): {user_text[:100]}")

        try:
            # 1. follow-up: download by rank
            ranks = parse_download_ranks(user_text)
            if ranks:
                handled = await handle_download_by_ranks(
                    m=m,
                    store=store,
                    doc_handler=doc_handler,
                    user_id=user_id,
                    session_id=session_id,
                    ranks=ranks,
                )
                if handled:
                    return
            
            # 3. follow-up: show more
            if Settings.SHOW_MORE_RE.match(user_text) and Settings.SHOW_BY_PAGE:
                handled = await handle_show_more(
                    m=m,
                    store=store,
                    user_id=user_id,
                    session_id=session_id,
                    page_size=Settings.SHOW_MAX,
                )
                if handled:
                    return

            # 2. follow-up: show all
            if Settings.SHOW_ALL_RE.match(user_text):
                handled = await handle_show_all(
                    m=m,
                    store=store,
                    user_id=user_id,
                    session_id=session_id,
                )
                if handled:
                    return

            # 4. обычный запрос -> ADK
            await adk.ensure_session(user_id=str(user_id), session_id=session_id)

            # Загружаем данные пользователя в состояние ADK (на случай если сессия новая)
            user_data = await subscriber_store.get_user_data(int(user_id))
            if user_data:
                await adk.set_user_state(str(user_id), session_id, user_data)
    
            answer, events = await adk.run(
                user_id=str(user_id),
                session_id=session_id,
                text=user_text
            )

            logger.info(f"📤 Ответ для user_id={user_id}: {answer[:100]}")

            # DEBUG-логирование оставляем
            if os.getenv("LOG_LEVEL", "INFO").upper() == "DEBUG" and events:
                try:
                    for event in events:
                        if not isinstance(event, dict):
                            logger.debug("Event not a dict")
                            continue
                        if "usageMetadata" in event:
                            usage = event["usageMetadata"]
                            logger.debug(
                                f"📊 Использование токенов: "
                                f"prompt={usage.get('promptTokenCount', 0)}, "
                                f"response={usage.get('candidatesTokenCount', 0)}, "
                                f"total={usage.get('totalTokenCount', 0)}, "
                                f"cached={usage.get('cachedContentTokenCount', 0)}"
                            )

                        if "actions" in event and event["actions"]:
                            actions = event["actions"]

                            if actions.get("stateDelta"):
                                logger.debug(f"🔄 State delta: {json.dumps(actions['stateDelta'], indent=2, ensure_ascii=False)}")

                            if actions.get("artifactDelta"):
                                logger.debug(f"📦 Artifact delta: {json.dumps(actions['artifactDelta'], indent=2, ensure_ascii=False)}")

                            if actions.get("requestedToolConfirmations"):
                                logger.debug(f"🔧 Tool confirmations: {json.dumps(actions['requestedToolConfirmations'], indent=2, ensure_ascii=False)}")

                        if "author" in event:
                            logger.debug(f"👤 Author: {event['author']}")

                        if "invocationId" in event:
                            logger.debug(f"🆔 Invocation ID: {event['invocationId']}")

                        if "content" in event and isinstance(event["content"], dict):
                            parts = event["content"].get("parts", [])
                            for part in parts:
                                if not isinstance(part, dict):
                                    continue

                                if "tool_use" in part:
                                    logger.debug(f"🔧 Tool use: {json.dumps(part['tool_use'], indent=2, ensure_ascii=False)}")

                                if "tool_response" in part:
                                    logger.debug(f"📥 Tool response: {json.dumps(part['tool_response'], indent=2, ensure_ascii=False)}")

                                if "function_call" in part:
                                    logger.debug(f"🔧 Function call: {json.dumps(part['function_call'], indent=2, ensure_ascii=False)}")

                                if "function_response" in part:
                                    logger.debug(f"📥 Function response: {json.dumps(part['function_response'], indent=2, ensure_ascii=False)}")

                except Exception as log_err:
                    logger.debug(f"Не удалось извлечь метаданные из events: {log_err}")

            # 5. сохраняем историю диалога
            await store.append(user_id, "user", user_text)
            await store.append(user_id, "model", answer)

            # 6. пробуем сначала вытащить bot_contract из ответа агента
            contract = extract_bot_contract(answer)
            if contract:
                reranked_items = normalize_contract_results(contract)

                await store.save_search_results(
                    user_id=user_id,
                    session_id=session_id,
                    query=user_text,
                    items=reranked_items,
                    shown_count=min(Settings.SHOW_MAX, len(reranked_items)),
                )
                logger.info(f"💾 Сохранён search-state из bot_contract: {len(reranked_items)} документов для user_id={user_id}")

                if reranked_items:
                    top_items = reranked_items[:Settings.SHOW_MAX]
                    text = render_results(top_items, total=len(reranked_items), offset=0)
                    await m.answer(text, parse_mode="HTML")
                else:
                    await m.answer("Не нашёл релевантных файлов по запросу.")
                return
            
            # 7. fallback: старая логика через events
            extracted_items = extract_search_results_from_events(events)
            if extracted_items:
                await store.save_search_results(
                    user_id=user_id,
                    session_id=session_id,
                    query=user_text,
                    items=extracted_items,
                    shown_count=min(8, len(extracted_items)),
                )
                logger.info(f"💾 Сохранён search-state: {len(extracted_items)} документов для user_id={user_id}")
            else:
                logger.info("ℹ️ Из events не удалось извлечь search-state")

            # 8. answer пользователю — старый путь
            clean_answer = doc_handler.remove_document_ids(answer)
            if clean_answer.strip():
                html_answer = markdown_to_safe_html(clean_answer)
                await m.answer(html_answer, parse_mode="HTML") 

        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения от user_id={user_id}: {e}", exc_info=True)
            await m.answer(
                "😔 Произошла ошибка при обработке запроса.\n"
                "Попробуйте позже или используйте /reset для сброса диалога."
            )

    #  обработчик открытия папки в меню бота
    @dp.callback_query(F.data.startswith("d:"))
    async def open_dir(callback: CallbackQuery):
        """Команда обработчик открытия папки"""
        await callback.answer()
        pid = callback.data.split(":")[1]

        path = Settings.CALLBACK_MAP.get(pid)

        if path is None:
            await callback.answer("Кнопка устарела", show_alert=True)
            return
        # делаем обращение к пути снова свежим
        Settings.CALLBACK_MAP.move_to_end(pid)
        path_list = path.split("/") if path else []
        tree = await get_tree_cached()
        # строим дерево папок относительно текущей папки
        menu = build_menu_from_tree(tree, path_list)
        title = "📁 /".join(path_list) or TITLE_START
        await callback.message.edit_text(
            title,
            reply_markup=menu
        )

    # обработчик открытия файла в меню бота
    @dp.callback_query(F.data.startswith("f:"))
    async def send_file(callback: CallbackQuery):
        """Обработчик отправки файлов через меню бота"""
        await callback.answer()

        pid = callback.data.split(":")[1]
        path = Settings.CALLBACK_MAP.get(pid)

        if not path:
            await callback.answer("Файл не найден", show_alert=True)
            return
        # делаем обращение к пути снова свежим
        Settings.CALLBACK_MAP.move_to_end(pid)
        doc_id = await get_document_id(path)
        if not doc_id:
            url = f"{Settings.KB_MANAGER_URL}/api/filesystem/download/?path={quote(path)}"
        else:
            url = f"{Settings.KB_MANAGER_URL}/api/documents/download/{doc_id}"
        filename = path.split("/")[-1]

        # скачиваем файл
        tmp_name = None
        async with aiohttp.ClientSession() as session:
            async with session.get(url) as resp:
                if resp.status != 200:
                    await callback.answer("Ошибка загрузки файла", show_alert=True)
                    return
                try:
                    tmp = tempfile.NamedTemporaryFile(delete=False)
                    tmp_name = tmp.name
                    tmp.write(await resp.read())
                    tmp.close()

                    # отправляем
                    await callback.message.answer_document(
                        document=FSInputFile(tmp_name, filename=filename),
                        # caption=filename
                    )
                finally:
                    if tmp_name and os.path.exists(tmp_name):
                        os.remove(tmp_name)
 
async def get_authenticated_user(m: Message, subscriber_store) -> dict | None:
    """
    Проверяет регистрацию и наличие телефона.
    Если телефона нет — отправляет запрос и возвращает None.
    Если всё ок — возвращает словарь с готовыми данными.
    """
    # 1. Разруливаем фоллбеки ОДИН раз для всех
    user_id = int(m.from_user.id)
    username = m.from_user.username or "unknown"
    first_name = m.from_user.first_name
    last_name = m.from_user.last_name
    last_seen = datetime.now()
    # проверяем есть ли телефон у пользователя в базе
    existing_phone = await subscriber_store.get_phone(int(user_id))
    # Добавление/обновление пользователя в базе
    await subscriber_store.add(
        user_id=int(user_id),
        username=username,
        first_name=first_name,
        last_name=last_name,
        last_seen=last_seen,
        phone_number=None # не затираем телефон если он уже есть
    )
    # Достаем полные данные пользователя
    user_data = await subscriber_store.get_user_data(user_id)
    # 2. Если телефона нет — шлём запрос и возвращаем None (стоп-сигнал)
    if not existing_phone:
        logger.info(f"Запрос телефона у user_id={user_id} (@{username})")
        await m.answer(
            f"👋 Привет, {first_name}!\n\n"
            f"Для связи с вами нам нужен ваш номер телефона.\n\n"
            f"Пожалуйста, нажмите кнопку ниже чтобы поделиться номером.\n"
            f"Это нужно только для уведомлений о важных обновлениях.",
            reply_markup=PHONE_KEYBOARD
        )
        return None
        
    # 3. Всё супер, возвращаем обогащенный словарь
    return user_data

# кэширование полученных путей 
async def get_tree_cached():
    global TREE_CACHE, TREE_TS
    # кэшируем дерево чтобы постоянно не обращаться к api 15 sec 
    if TREE_CACHE and time.time() - TREE_TS < Settings.TIME_SET_WAIT:
        return TREE_CACHE

    TREE_CACHE = await get_kb_tree()
    TREE_TS = time.time()

    return TREE_CACHE