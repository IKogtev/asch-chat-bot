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

from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
from pathlib import Path
from bot.services.database import PostgresChatStore
import time

# Настройка логгера
logger = setup_logger('utils_processing', 'utils_processing.log')
eventlogger = EventLogger()

def get_filename(filepath: str) -> str:
    return Path(filepath).name

def markdown_to_safe_html(text: str) -> str:
    """Конвертация Markdown в безопасный HTML для Telegram"""
    # Экранируем HTML
    text = html_module.escape(text)
    
    # **bold** -> <b>bold</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    
    # *italic* -> <i>italic</i> (только если не внутри bold)
    text = re.sub(r'(?<!</b>)\*([^*]+?)\*(?!<b>)', r'<i>\1</i>', text)
    
    # `code` -> <code>code</code>
    text = re.sub(r'`(.+?)`', r'<code>\1</code>', text)
    
    # [text](url) -> <a href="url">text</a>
    text = re.sub(r'\[(.+?)\]\((.+?)\)', r'<a href="\2">\1</a>', text)
    
    return text

def render_results(items: list[dict], total: int, offset: int = 0) -> str:
    """Рендер списка документов (логика общая с агентом doc_search)."""
    return render_doc_list_html(items, total, offset)


# обработка команды скачивания по рангам
async def handle_download_by_ranks(
        m: Message,
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
            await m.answer(answer)
            response_time = int((time.time() - start_time) * 1000)
            await eventlogger.log_event(
                event_type="response",
                user_id=str(user_id),
                session_id=session_id,
                channel="telegram",
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
            await m.answer(answer)
            response_time = int((time.time() - start_time) * 1000)
            await eventlogger.log_event(
                event_type="response",
                user_id=str(user_id),
                session_id=session_id,
                channel="telegram",
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
                await m.answer_document(
                    FSInputFile(str(file_path), filename=file_path.name)
                )
                await eventlogger.log_event(
                    event_type="document_download",
                    user_id=str(user_id),
                    user_name=m.from_user.username,
                    session_id=session_id,
                    channel="telegram",
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
                await m.answer(f"Не удалось загрузить документ №{rank}.")
                await eventlogger.log_event(
                    event_type="document_download_failed",
                    user_id=str(user_id),
                    user_name=m.from_user.username,
                    session_id=session_id,
                    channel="telegram",
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
            await m.answer(answer)
            response_time = int((time.time() - start_time) * 1000)
            await eventlogger.log_event(
                event_type="response",
                user_id=str(user_id),
                session_id=session_id,
                channel="telegram",
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
    m: Message,
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
    await m.answer(text, parse_mode="HTML")
    response_time = int((time.time() - start_time) * 1000)
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

#  обработка команды показать ещё результаты
async def handle_show_more(
    m: Message,
    store: PostgresChatStore,
    user_id: int,
    session_id: str,
    page_size: int = Settings.SHOW_MAX,
    turn_id: str=None,
    start_time: float=None
) -> bool:
    meta = await store.get_last_search_meta(user_id, session_id)
    items = await store.get_last_search_results(user_id, session_id)

    if not meta or not items:
        return False

    start = meta["shown_count"]
    end = min(start + page_size, len(items))

    if start >= len(items):
        answer = "Это уже все найденные файлы."
        await m.answer(answer)
        response_time = int((time.time() - start_time) * 1000)
        await eventlogger.log_event(
            event_type="response",
            user_id=str(user_id),
            session_id=session_id,
            channel="telegram",
            payload={
                "turn_id": turn_id,
                "text": answer,
                "response_time_ms": response_time
            }    
        )
        
        return True

    chunk = items[start:end]
    text = render_results(chunk, total=len(items), offset=start)
    response_time = int((time.time() - start_time) * 1000)
    await store.update_shown_count(user_id, session_id, end)
    await m.answer(text, parse_mode="HTML")
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

# функция для безопасного разбиения длинного текста на части, чтобы Telegram не обрезал его
def split_message(text: str, limit: int = 4000):
    return [text[i:i+limit] for i in range(0, len(text), limit)]

# конвертация HTML в безопасный для Telegram формат
def html_to_telegram(html: str) -> str:
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
    """Регистрирует путь и возвращает его короткий ID (хэш)"""
    # path_id = str(hash(path))
    path_id = str(len(Settings.CALLBACK_MAP) + 1)
    
    # Если ID уже есть, перемещаем его в конец (он теперь "свежий")
    if path_id in Settings.CALLBACK_MAP:
        Settings.CALLBACK_MAP.move_to_end(path_id)
    
    Settings.CALLBACK_MAP[path_id] = path
    
    # Если превысили лимит, удаляем самый старый элемент (из начала)
    if len(Settings.CALLBACK_MAP) > Settings.MAX_CALLBACK_ENTRIES:
        Settings.CALLBACK_MAP.popitem(last=False)
        
    return path_id

#  построить меню на основе дерева папок из kb_manager
def build_menu_from_tree(tree: dict, path: list[str]):
    """Функция для строительства деревовидной структуры папок"""
    node = tree
    for p in path:
        node = node[p]
    buttons = []
    # папки
    for key, value in node.items():
        if key == "files":
            continue
        full_path = "/".join(path + [key])
        pid = register_callback_path(full_path)
        buttons.append([
            InlineKeyboardButton(
                text=f"📁 {key}",
                callback_data=f"d:{pid}"
            )
        ])
    # файлы
    for f in node.get("files", []):
        full_path = "/".join(path + [f])
        pid = register_callback_path(full_path)
        buttons.append([
            InlineKeyboardButton(
                text=f"📄 {f}",
                callback_data=f"f:{pid}"
            )
        ])
    # навигация
    nav_buttons = []
    if path:
        parent = "/".join(path[:-1])
        pid = register_callback_path(parent)
        nav_buttons.append(
            InlineKeyboardButton(
                text="⬅ Назад",
                callback_data=f"d:{pid}"
            )
        )
        nav_buttons.append(
            InlineKeyboardButton(
                text="🏠 на главную",
                callback_data="home"
            )
        )
    if nav_buttons:
        buttons.append(nav_buttons)

    return InlineKeyboardMarkup(inline_keyboard=buttons)

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