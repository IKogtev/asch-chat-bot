##################################
# Вспомогательные функции
##################################
import html as html_module
import re
from bot.services.config import Settings
from utils import setup_logger
# логер событий
from utils.event_logger import EventLogger
from utils.document_handler import DocumentHandler
from utils.doc_search_format import render_doc_list_html

from aiogram.types import FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
from pathlib import Path
from bot.services.database import PostgresChatStore
import time
import asyncio
import uvicorn
from maxapi.types.attachments.buttons import CallbackButton
from maxapi.utils.inline_keyboard import InlineKeyboardBuilder
from maxapi.enums import TextFormat
from maxapi.types import InputMedia
import hashlib
from utils.bot_response_format import format_bot_response

# Настройка логгера
logger = setup_logger('utils_processing', 'utils_processing.log')
eventlogger = EventLogger()

#  нормализация телефона для разных форматов
def normalize_phone(phone: str) -> str:
    # если телефон пустой — возвращаем None
    if not phone:
        return phone
    phone = re.sub(r"[^\d+]", "", phone)
    # если без + и начинается с 8 → делаем +7
    if phone.startswith("8"):
        phone = "+7" + phone[1:]
    elif not phone.startswith("+"):
        phone = "+" + phone
    return phone

#  получаем имя файла из пути
def get_filename(filepath: str) -> str:
    return Path(filepath).name

def markdown_to_safe_html(text: str) -> str:
    """Конвертация ответа в единый безопасный HTML для Telegram и Max."""
    return format_bot_response(text)

def render_results(items: list[dict], total: int, offset: int = 0) -> str:
    """Рендер списка документов (логика общая с агентом doc_search)."""
    return render_doc_list_html(items, total, offset)

# функция для безопасного разбиения длинного текста на части, чтобы Telegram не обрезал его
def split_message(text: str, limit: int = 4000):
    return [text[i:i+limit] for i in range(0, len(text), limit)]

# конвертация HTML в безопасный формат
def html_to_bot(html: str) -> str:
    if not html:
        return ""

    # переносы строк
    html = html.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")

    # параграфы
    html = re.sub(r"<p[^>]*>", "", html)
    html = html.replace("</p>", "\n")

    # заголовки → bold
    html = re.sub(r"<h[1-6][^>]*>", "<b>", html)
    html = re.sub(r"</h[1-6]>", "</b>\n", html)

    # списки
    html = html.replace("<ul>", "").replace("</ul>", "")
    html = html.replace("<ol>", "").replace("</ol>", "")
    html = re.sub(r"<li[^>]*>", "• ", html)
    html = html.replace("</li>", "\n")

    # bold / italic
    html = html.replace("<strong>", "<b>").replace("</strong>", "</b>")
    html = html.replace("<em>", "<i>").replace("</em>", "</i>")

    # underline / strike
    html = html.replace("<u>", "<u>").replace("</u>", "</u>")
    html = html.replace("<s>", "<s>").replace("</s>", "</s>")

    # code block
    html = re.sub(r'<pre.*?>', '<pre>', html)
    html = re.sub(r'</pre>', '</pre>\n', html)

    # удалить ВСЕ лишние теги (очень важно)
    allowed_tags = ["b", "i", "u", "s", "a", "code", "pre"]

    def clean_tags(match):
        tag = match.group(1)
        if tag.split()[0].lower() in allowed_tags:
            return match.group(0)
        return ""

    html = re.sub(r"</?([^>\s]+)[^>]*>", clean_tags, html)

    # HTML entities
    html = html.replace("&nbsp;", " ")
    html = html.replace("&amp;", "&")

    # лишние переносы
    html = re.sub(r"\n{3,}", "\n\n", html)

    return html.strip()

# функция хранения пути
def register_callback_path(path: str) -> str:
    """Стабильный ID для path (не ломается после рестарта)"""

    if not path:
        return "root"

    # стабильный hash
    path_id = hashlib.md5(path.encode("utf-8")).hexdigest()[:10]

    Settings.CALLBACK_MAP[path_id] = path

    return path_id

# получить дерево папок
async def get_kb_tree():
    
    url = f"{Settings.KB_MANAGER_URL}/api/filesystem/folders"
    
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            return await resp.json()

# получить id документа
async def get_document_id(path: str) -> str | None:
    filename = path.split("/")[-1]

    url = f"{Settings.KB_MANAGER_URL}/api/documents"

    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                logger.error(f"kb-manager error: {resp.status}")
                return None

            docs = await resp.json()

    for doc in docs:
        if doc.get("source_name") == filename:
            return doc.get("document_id")

    return None

# Запуск HTTP сервера в отдельной задаче
async def run_http_server(app, port):
    """Запуск HTTP сервера"""
    try:
        config = uvicorn.Config(
            app,
            host="0.0.0.0",
            port=port,
            log_level="info"
        )
        server = uvicorn.Server(config)
        await server.serve()
    except asyncio.CancelledError:
        logger.info("HTTP сервер остановлен")

#  построить меню на основе дерева папок из kb_manager универсальное для нескольких источников
def build_universal_menu(tree: dict, path: list[str], channel: str = "telegram"):
    """
    Универсальная функция для построения дерева папок.
    channel: 'telegram' или 'max'
    """
    node = tree
    for p in path:
        node = node.get(p, {})

    # Список для хранения строк кнопок (каждая строка — список кнопок)
    rows = []

    # Вспомогательная функция для создания кнопки под нужный канал
    def create_btn(text, data):
        if channel == "telegram":
            return InlineKeyboardButton(text=text, callback_data=data)
        else:
            return CallbackButton(text=text, payload=data)

    # 1. Обработка папок
    for key in node.keys():
        if key == "files":
            continue
        full_path = "/".join(path + [key])
        pid = register_callback_path(full_path)
        # В телеграме папки идут по одной в строке, 
        # в Max API через builder.row тоже.
        rows.append([create_btn(f"📁 {key}", f"d:{pid}")])

    # 2. Обработка файлов
    for f in node.get("files", []):
        full_path = "/".join(path + [f])
        pid = register_callback_path(full_path)
        rows.append([create_btn(f"📄 {f}", f"f:{pid}")])

    # 3. Навигация (назад и на главную)
    if path:
        nav_row = []
        if len(path) == 1:
            # если мы на первом уровне — назад = home
            nav_row.append(create_btn("⬅ Назад", "home"))
        else:
            parent = "/".join(path[:-1])
            pid_back = register_callback_path(parent)
            nav_row.append(create_btn("⬅ Назад", f"d:{pid_back}"))
        
        nav_row.append(create_btn("🏠 на главную", "home"))
        rows.append(nav_row)

    # 4. Финальная сборка разметки
    if channel == "telegram":
        return InlineKeyboardMarkup(inline_keyboard=rows)
    else:
        builder = InlineKeyboardBuilder()
        for row in rows:
            builder.row(*row)
        return builder.as_markup()

#  обработка команды показать ещё результаты
async def handle_show_more(
    event, # Может быть Message (TG) или MessageCreated (Max)
    store: PostgresChatStore,
    user_id: int,
    session_id: str,
    page_size: int = Settings.SHOW_MAX,
    turn_id: str = None,
    start_time: float = None,
    platform: str = "telegram" # "telegram" или "max"
) -> bool:
    # 1. Получаем данные из хранилища
    meta = await store.get_last_search_meta(user_id, session_id)
    items = await store.get_last_search_results(user_id, session_id)

    if not meta or not items:
        return False

    # 2. Адаптация под платформу (настройка интерфейса ответа)
    if platform == "telegram":
        answer_func = event.answer
        html_param = {"parse_mode": "HTML"}
    else:
        # Для Max API ответ идет через event.message
        answer_func = event.message.answer
        # формат для Max API
        html_param = {"format": TextFormat.HTML}

    # 3. Расчет пагинации
    start = meta["shown_count"]
    end = min(start + page_size, len(items))
    
    # Считаем время ответа, если передан start_time
    response_time = int((time.time() - start_time) * 1000) if start_time else 0

    # 4. Если показывать больше нечего
    if start >= len(items):
        answer = "Это уже все найденные файлы."
        await answer_func(answer)
        
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
        return True

    # 5. Подготовка и отправка новой порции результатов
    chunk = items[start:end]
    text = render_results(chunk, total=len(items), offset=start)
    
    await store.update_shown_count(user_id, session_id, end)
    
    # Отправляем сообщение с нужным параметром форматирования
    await answer_func(text, **html_param)

    # 6. Логирование
    await eventlogger.log_event(
        event_type="response",
        user_id=str(user_id),
        session_id=session_id,
        channel=platform,
        payload={
            "turn_id": turn_id,
            "text": text,
            "response_time_ms": response_time
        }    
    )
    return True

# обработка команды показать все результаты
async def handle_show_all(
    event, # Message (TG) или MessageCreated (Max)
    store: PostgresChatStore,
    user_id: int,
    session_id: str,
    turn_id: str,
    start_time: float,
    platform: str = "telegram" # "telegram" или "max" или "web"
) -> bool:
    """Универсальная функция отображения всех результатов поиска"""
    
    # 1. Получаем данные
    items = await store.get_last_search_results(user_id, session_id)
    if not items:
        return False

    # 2. Формируем текст (общая логика для всех)
    text = render_results(items, total=len(items), offset=0)
    await store.update_shown_count(user_id, session_id, len(items))

    # 3. Адаптация под платформу
    if platform == "telegram":
        answer_func = event.answer
        # Параметры для Telegram
        send_params = {"parse_mode": "HTML"}
    else:
        # Для Max API ответ идет через event.message.answer
        answer_func = event.message.answer
        # Параметры для Max (предполагаем наличие TextFormat)
        send_params = {"format": TextFormat.HTML}

    # 4. Отправка ответа
    await answer_func(text, **send_params)

    # 5. Логирование (единый расчет времени)
    response_time = int((time.time() - start_time) * 1000)
    
    await eventlogger.log_event(
        event_type="response",
        user_id=str(user_id),
        session_id=session_id,
        channel=platform, # Используем имя платформы как канал
        payload={
            "turn_id": turn_id,
            "text": text,
            "response_time_ms": response_time
        }    
    )
    
    return True

# обработка команды скачивания по рангам
async def handle_download_by_ranks(
    event, # Message (TG) или MessageCreated (Max)
    store: PostgresChatStore,
    doc_handler: DocumentHandler,
    user_id: int,
    session_id: str,
    ranks: list[int],
    turn_id: str,
    start_time: float,
    platform: str = "telegram"
) -> bool:
    if not ranks:
        return False
        
    sent_any = False
    # Адаптер для текстовых ответов и данных пользователя
    if platform == "telegram":
        answer_func = event.answer
        username = event.from_user.username
    else:
        answer_func = event.message.answer
        username = event.from_user.username

    for rank in ranks:
        item = await store.get_result_by_rank(user_id, session_id, rank)
        
        # 1. Обработка отсутствия документа в списке
        if not item:
            answer = f"Не нашёл документ №{rank} в последнем списке."
            await answer_func(answer)
            await _log_download_res(user_id, session_id, platform, turn_id, answer, start_time)
            continue

        doc_id = item.get("document_id")
        if not doc_id:
            answer = f"Не удалось определить document_id для документа №{rank}."
            await answer_func(answer)
            await _log_download_res(user_id, session_id, platform, turn_id, answer, start_time)
            continue

        # 2. Попытка скачивания и отправки
        file_path = None
        try:
            file_path = await doc_handler.download_document(doc_id)
            if file_path and file_path.exists():
                # --- РАЗВИЛКА ОТПРАВКИ ФАЙЛА ---
                if platform == "telegram":
                    await event.answer_document(
                        FSInputFile(str(file_path), filename=file_path.name)
                    )
                else:
                    await event.message.answer(
                        attachments=[InputMedia(path=str(file_path))]
                    )
                
                # Лог успешной загрузки
                await eventlogger.log_event(
                    event_type="document_download",
                    user_id=str(user_id),
                    user_name=username,
                    session_id=session_id,
                    channel=platform,
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
                # Лог неудачи скачивания
                err_msg = f"Не удалось загрузить документ №{rank}."
                await answer_func(err_msg)
                await _log_download_err(user_id, username, session_id, platform, doc_id, rank, turn_id, file_path)

        except Exception as e:
            logger.error(f"Ошибка отправки документа rank={rank}, doc_id={doc_id}: {e}", exc_info=True)
            answer = f"Ошибка при загрузке документа №{rank}."
            await answer_func(answer)
            await _log_download_res(user_id, session_id, platform, turn_id, answer, start_time)
            
        finally:
            if file_path and file_path.exists():
                try:
                    file_path.unlink()
                except Exception:
                    pass

    return sent_any

# Вспомогательные функции, чтобы не дублировать длинные вызовы логгера
async def _log_download_res(uid, sid, channel, tid, text, start_t):
    response_time = int((time.time() - start_t) * 1000)
    await eventlogger.log_event(
        event_type="response",
        user_id=str(uid),
        session_id=sid,
        channel=channel,
        payload={"turn_id": tid, "text": text, "response_time_ms": response_time}
    )

async def _log_download_err(uid, uname, sid, channel, doc_id, rank, tid, path):
    await eventlogger.log_event(
        event_type="document_download_failed",
        user_id=str(uid),
        user_name=uname,
        session_id=sid,
        channel=channel,
        payload={
            "file_path": str(path),
            "text": f"Не удалось загрузить документ №{rank}.",
            "doc_id": doc_id, "rank": rank, "source": "search", "turn_id": tid
        }
    )
