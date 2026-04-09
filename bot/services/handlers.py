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
from utils.doc_search_format import (
    extract_document_id_lines,
    parse_download_ranks,
    strip_bot_search_meta,
)
from bot.services.utils import (
    markdown_to_safe_html,
    render_results,
    handle_show_more,
    handle_show_all,
    handle_download_by_ranks,
    build_menu_from_tree,
    get_document_id,
    get_kb_tree,
)

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
        logger.info(f"Команда /start от user_id={user_id} (@{user['username']})")

        # На /start не вызываем ADK.
        # Только обновляем пользователя в БД через get_authenticated_user()
        # и показываем стартовое меню.
        tree = await get_tree_cached()
        menu = build_menu_from_tree(tree, [])
        await m.answer(TITLE_START, reply_markup=menu)
        return

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

        # После получения телефона тоже не вызываем ADK.
        # ADK будет инициализирован лениво при первом текстовом сообщении.
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
        await m.answer(f"Текущая версия бота: {Settings.PLATFORM_VERSION}")

    # домашняя страница
    @dp.callback_query(lambda c: c.data == "home")
    async def go_home(callback: CallbackQuery):
        """Обработчик перехода на главную страницу"""
        await callback.answer()

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
        session_id = f"session-{user_id}"

        logger.info(f"Команда /reset от user_id={user_id} (@{username})")

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
            await m.answer("✅ История диалога и сессия сброшены")
            logger.info(f"История и сессия сброшены для user_id={user_id}")

        except Exception as e:
            logger.error(f"Ошибка при сбросе: {e}", exc_info=True)
            await m.answer("❌ Ошибка при сбросе истории")

        return

    # обработчик команды /help для отображения справки
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
            await adk.ensure_session(user_id=str(user_id), session_id=session_id)

            # Пагинация и скачивание по номеру — из БД, без вызова ADK
            if Settings.SHOW_MORE_RE.match(user_text):
                ok = await handle_show_more(m, store, user_id, session_id)
                if not ok:
                    await m.answer(
                        "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
                    )
                await store.append(user_id, "user", user_text)
                await store.append(
                    user_id,
                    "model",
                    "Показана следующая порция списка документов."
                    if ok
                    else "Список документов не найден.",
                )
                return

            if Settings.SHOW_ALL_RE.match(user_text) and not Settings.SHOW_MORE_RE.match(
                user_text
            ):
                ok = await handle_show_all(m, store, user_id, session_id)
                if not ok:
                    await m.answer(
                        "Нет сохранённого списка документов. Сначала найдите файлы по запросу."
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

            dl_ranks = parse_download_ranks(user_text)
            if dl_ranks:
                await handle_download_by_ranks(
                    m, store, doc_handler, user_id, session_id, dl_ranks
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

            meta_before = await store.get_last_search_meta(user_id, session_id)
            search_id_before = meta_before["search_id"] if meta_before else None

            answer, _ = await adk.run(
                user_id=str(user_id),
                session_id=session_id,
                text=user_text
            )

            logger.info(f"📤 Ответ для user_id={user_id}: {answer[:100]}")

            work = answer or ""
            work, doc_ids = extract_document_id_lines(work)
            for did in doc_ids:
                file_path = None
                try:
                    file_path = await doc_handler.download_document(did)
                    if file_path and file_path.exists():
                        await m.answer_document(
                            FSInputFile(str(file_path), filename=file_path.name)
                        )
                    else:
                        await m.answer("⚠️ Не удалось загрузить документ.")
                except Exception as doc_err:
                    logger.error(
                        f"Ошибка отправки документа doc_id={did}: {doc_err}",
                        exc_info=True,
                    )
                    await m.answer("❌ Ошибка при загрузке документа.")
                finally:
                    try:
                        if file_path and file_path.exists():
                            file_path.unlink()
                    except Exception:
                        pass

            work = strip_bot_search_meta(work)

            # 5. сохраняем историю диалога
            await store.append(user_id, "user", user_text)
            await store.append(user_id, "model", answer)

            # 6. Новый doc_search: список в БД — признак смены search_id, первая порция рендерится здесь
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
                    await m.answer(text, parse_mode="HTML")
                    return

            # 7. ответ пользователю (kb_answer и прочее)
            clean_answer = doc_handler.remove_document_ids(work)
            if clean_answer.strip():
                if "<b>" in clean_answer or clean_answer.lstrip().startswith("<"):
                    await m.answer(clean_answer, parse_mode="HTML")
                else:
                    html_answer = markdown_to_safe_html(clean_answer)
                    await m.answer(html_answer, parse_mode="HTML")

        except Exception as e:
            logger.error(f"❌ Ошибка обработки сообщения от user_id={user_id}: {e}", exc_info=True)
            await m.answer(
                "😔 Произошла ошибка при обработке запроса.\n"
                "Попробуйте позже или используйте /reset для сброса диалога."
            )

    # обработчик открытия папки в меню бота
    @dp.callback_query(F.data.startswith("d:"))
    async def open_dir(callback: CallbackQuery):
        """Команда обработчик открытия папки"""
        await callback.answer()
        pid = callback.data.split(":")[1]

        path = Settings.CALLBACK_MAP.get(pid)

        if path is None:
            await callback.answer("Кнопка устарела", show_alert=True)
            return

        Settings.CALLBACK_MAP.move_to_end(pid)
        path_list = path.split("/") if path else []
        tree = await get_tree_cached()

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

        Settings.CALLBACK_MAP.move_to_end(pid)
        doc_id = await get_document_id(path)
        if not doc_id:
            url = f"{Settings.KB_MANAGER_URL}/api/filesystem/download/?path={quote(path)}"
        else:
            url = f"{Settings.KB_MANAGER_URL}/api/documents/download/{doc_id}"
        filename = path.split("/")[-1]

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

                    await callback.message.answer_document(
                        document=FSInputFile(tmp_name, filename=filename),
                    )
                finally:
                    if tmp_name and os.path.exists(tmp_name):
                        os.remove(tmp_name)

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
async def get_authenticated_user(m: Message, subscriber_store) -> dict | None:
    """
    Проверяет регистрацию и наличие телефона.
    Если телефона нет — отправляет запрос и возвращает None.
    Если всё ок — возвращает словарь с готовыми данными.
    """
    # 1. Разруливаем фоллбеки ОДИН раз для всех
    user_id = int(m.from_user.id)
    username = m.from_user.username or "unknown"
    first_name = m.from_user.first_name or "Гость"
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