##################################
# Вспомогательные функции
##################################
import html as html_module
import re
from bot.services.config import Settings
from utils import setup_logger
from utils.document_handler import DocumentHandler
import json
from aiogram.types import Message, FSInputFile, InlineKeyboardMarkup, InlineKeyboardButton
import aiohttp
from bot.services.database import PostgresChatStore
from typing import Any

# Настройка логгера
logger = setup_logger('utils_processing', 'utils_processing.log')

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

# парсер для извлечения номеров документов из текста, например "скачай 1 и 3"
def parse_download_ranks(text: str) -> list[int]:
    m = Settings.DOWNLOAD_RE.match(text.strip())
    if not m:
        return []
    raw = m.group(1)
    return [int(x) for x in re.findall(r'\d+', raw)]

# рендер результатов для ответа пользователю
def render_results(items: list[dict], total: int, offset: int = 0) -> str:
    if not items:
        return "Ничего не нашёл."
        
    shown = offset + len(items)
    if shown < total:
        text = "Вот самые релевантные документы, которые удалось найти:\n"
    else:
        text = "Вот документы, которые удалось найти:\n"
    lines = []
    for i, item in enumerate(items, start=offset + 1):
        title = html_module.escape(item["source_name"])
        snippet = (item.get("snippet") or "").strip().replace("\n", " ")
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        snippet = html_module.escape(snippet)

        block = f"<b>{i}. {title}</b>"
        if snippet:
            block += f"\n{snippet}"
        lines.append(block)

    text += "\n\n".join(lines)

    if shown < total:
        text += f"\n\nПоказано {shown} из {total}. Хотите получить весь список? Напишите <b>ещё</b>, чтобы получить следующую порцию документов; <b>все</b>, <b>покажи все</b> или <b>да</b>, чтобы получить весь список.\nИли напишите номер документа, чтобы скачать его."
    else:
        text += "\n\nНапишите номер документа, чтобы скачать его."

    return text

# извлечение bot_contract
def extract_bot_contract(answer: str) -> dict | None:
    if not answer:
        return None
            
    m = re.search(
        r"<bot_contract>\s*(\{.*?\})\s*</bot_contract>",
        answer,
        flags=re.DOTALL,
    )
    
    logger.debug(f"RegExp {m}")
    if not m:
        return None

    raw_json = m.group(1).strip()

    try:
        data = json.loads(raw_json)
    except json.JSONDecodeError as e:
        logger.error(f"Не удалось распарсить bot_contract JSON: {e}")
        return None

    if not isinstance(data, dict):
        return None

    if data.get("mode") != "search_results":
        return None

    results = data.get("results")
    if not isinstance(results, list):
        return None

    return data

# нормализация результатов контракта
def normalize_contract_results(contract: dict) -> list[dict]:
    results = contract.get("results", [])
    normalized = []

    for item in results:
        if not isinstance(item, dict):
            continue

        document_id = item.get("document_id")
        source_name = item.get("source_name")
        if not document_id or not source_name:
            continue

        is_relevant = bool(item.get("is_relevant"))
        new_rank = item.get("new_rank")
        old_rank = item.get("old_rank")
        snippet = (item.get("snippet") or "").strip()
        source_path = item.get("source_path")

        if is_relevant and not isinstance(new_rank, int):
            continue

        normalized.append({
            "document_id": document_id,
            "source_name": str(source_name),
            "source_path": str(source_path) if source_path else None,
            "score": None,  # при желании можно позже сохранить score отдельно
            "snippet": snippet[:500],
            "rank": new_rank if is_relevant else 10_000 + (old_rank or 0),
            "old_rank": old_rank,
            "new_rank": new_rank,
            "is_relevant": is_relevant,
        })

    # сначала релевантные по new_rank, потом нерелевантные
    normalized.sort(
        key=lambda x: (
            not x["is_relevant"],
            x["rank"] if isinstance(x["rank"], int) else 10_000,
        )
    )

    # перенумеровываем только релевантные для UI
    relevant = [x for x in normalized if x["is_relevant"]]
    for idx, item in enumerate(relevant, start=1):
        item["rank"] = idx

    return relevant

def _extract_textish(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        parts = [_extract_textish(v) for v in value]
        return " ".join(x for x in parts if x).strip()
    if isinstance(value, dict):
        preferred_keys = [
            "text", "content", "snippet", "chunk_text", "body",
            "section", "summary", "description", "comment"
        ]
        parts = []
        for key in preferred_keys:
            if key in value:
                txt = _extract_textish(value.get(key))
                if txt:
                    parts.append(txt)
        if parts:
            return " ".join(parts).strip()
    return ""

def _find_tool_payloads(events: list[dict]) -> list[dict]:
    payloads = []

    for event in events or []:
        if not isinstance(event, dict):
            continue

        content = event.get("content")
        if isinstance(content, dict):
            for part in content.get("parts", []) or []:
                if not isinstance(part, dict):
                    continue

                # старые варианты
                if "tool_response" in part and isinstance(part["tool_response"], dict):
                    payloads.append(part["tool_response"])
                if "function_response" in part and isinstance(part["function_response"], dict):
                    payloads.append(part["function_response"])

                # новые варианты camelCase
                if "toolResponse" in part and isinstance(part["toolResponse"], dict):
                    payloads.append(part["toolResponse"])
                if "functionResponse" in part and isinstance(part["functionResponse"], dict):
                    payloads.append(part["functionResponse"])

        actions = event.get("actions")
        if isinstance(actions, dict):
            for key in ("function_response", "functionResponse", "tool_response", "toolResponse"):
                value = actions.get(key)
                if isinstance(value, dict):
                    payloads.append(value)

    return payloads

def _collect_candidate_dicts(obj: Any, out: list[dict]) -> None:
    if isinstance(obj, dict):
        # Похоже на один candidate/result/chunk
        keys = set(obj.keys())
        if (
            "document_id" in keys
            or "source_name" in keys
            or "source_path" in keys
            or "metadata" in keys
            or "content" in keys
            or "chunk_text" in keys
        ):
            out.append(obj)

        for v in obj.values():
            _collect_candidate_dicts(v, out)

    elif isinstance(obj, list):
        for item in obj:
            _collect_candidate_dicts(item, out)

def extract_search_results_from_events(events: list[dict]) -> list[dict]:
    """
    Извлекает document-level результаты kb_search из events.
    Приоритет:
    1. functionResponse.response.structuredContent.results
    2. functionResponse.response.structured_content.results
    3. fallback на старую эвристику
    """
    payloads = _find_tool_payloads(events)

    # 1. Нормальный путь: structuredContent
    for payload in payloads:
        if not isinstance(payload, dict):
            continue

        if payload.get("name") != "kb_search":
            continue

        response = payload.get("response")
        if not isinstance(response, dict):
            continue

        structured = response.get("structuredContent") or response.get("structured_content")
        if not isinstance(structured, dict):
            continue

        results = structured.get("results")
        if not isinstance(results, list):
            continue

        docs_by_id: dict[str, dict] = {}

        for item in results:
            if not isinstance(item, dict):
                continue

            document_id = item.get("document_id")
            if not document_id:
                metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
                document_id = metadata.get("document_id") or metadata.get("DOCUMENT_ID")

            if not document_id:
                continue

            source_name = (
                item.get("source_name")
                or item.get("source")
                or item.get("filename")
            )

            metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
            if not source_name:
                source_name = (
                    metadata.get("source_name")
                    or metadata.get("source")
                    or metadata.get("filename")
                    or metadata.get("file_name")
                    or metadata.get("name")
                )

            source_path = (
                item.get("relative_path")
                or item.get("source_path")
                or metadata.get("relative_path")
                or metadata.get("source_path")
            )

            if not source_name:
                if source_path:
                    source_name = str(source_path).split("/")[-1]
                else:
                    source_name = f"{document_id}.file"

            score = item.get("score")
            numeric_score = float(score) if isinstance(score, (int, float)) else None

            snippet = (
                _extract_textish(item.get("snippet"))
                or _extract_textish(item.get("content"))
                or _extract_textish(metadata.get("snippet"))
            )

            existing = docs_by_id.get(document_id)
            if not existing:
                docs_by_id[document_id] = {
                    "document_id": document_id,
                    "source_name": str(source_name),
                    "source_path": str(source_path) if source_path else None,
                    "score": numeric_score,
                    "snippet": snippet[:500] if snippet else "",
                }
                continue

            existing_score = existing.get("score")
            if numeric_score is not None and (existing_score is None or numeric_score > existing_score):
                existing["score"] = numeric_score
                if snippet:
                    existing["snippet"] = snippet[:500]

            if not existing.get("source_path") and source_path:
                existing["source_path"] = str(source_path)

        docs = list(docs_by_id.values())
        docs.sort(
            key=lambda x: (
                x["score"] is not None,
                x["score"] if x["score"] is not None else float("-inf"),
                x["source_name"].lower(),
            ),
            reverse=True,
        )

        for idx, doc in enumerate(docs, start=1):
            doc["rank"] = idx

        return docs

    # 2. fallback: старая эвристика
    raw_candidates: list[dict] = []
    for payload in payloads:
        _collect_candidate_dicts(payload, raw_candidates)

    if not raw_candidates:
        return []

    docs_by_id: dict[str, dict] = {}

    for item in raw_candidates:
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}

        document_id = (
            item.get("document_id")
            or metadata.get("document_id")
            or metadata.get("DOCUMENT_ID")
        )
        if not document_id:
            continue

        source_name = (
            item.get("source_name")
            or metadata.get("source_name")
            or metadata.get("source")
            or metadata.get("filename")
            or metadata.get("file_name")
            or metadata.get("name")
        )
        if not source_name:
            source_path = item.get("source_path") or metadata.get("source_path") or metadata.get("relative_path")
            if source_path:
                source_name = str(source_path).split("/")[-1]
            else:
                source_name = f"{document_id}.file"

        source_path = (
            item.get("source_path")
            or metadata.get("source_path")
            or metadata.get("relative_path")
        )

        score = item.get("score")
        if score is None:
            score = metadata.get("score")

        snippet = (
            _extract_textish(item.get("content"))
            or _extract_textish(item.get("chunk_text"))
            or _extract_textish(item.get("text"))
            or _extract_textish(item.get("snippet"))
            or _extract_textish(metadata.get("snippet"))
        )

        existing = docs_by_id.get(document_id)
        if not existing:
            docs_by_id[document_id] = {
                "document_id": document_id,
                "source_name": str(source_name),
                "source_path": str(source_path) if source_path else None,
                "score": float(score) if isinstance(score, (int, float)) else None,
                "snippet": snippet[:500] if snippet else "",
            }
            continue

        existing_score = existing.get("score")
        numeric_score = float(score) if isinstance(score, (int, float)) else None
        if numeric_score is not None and (existing_score is None or numeric_score > existing_score):
            existing["score"] = numeric_score
            if snippet:
                existing["snippet"] = snippet[:500]

        if not existing.get("source_path") and source_path:
            existing["source_path"] = str(source_path)

    docs = list(docs_by_id.values())
    docs.sort(
        key=lambda x: (
            x["score"] is not None,
            x["score"] if x["score"] is not None else float("-inf"),
            x["source_name"].lower(),
        ),
        reverse=True,
    )

    for idx, doc in enumerate(docs, start=1):
        doc["rank"] = idx

    return docs

# обработка команды скачивания по рангам
async def handle_download_by_ranks(
        m: Message,
        store: PostgresChatStore,
        doc_handler: DocumentHandler,
        user_id: int,
        session_id: str,
        ranks: list[int],
    ) -> bool:
    if not ranks:
        return False

    sent_any = False
    for rank in ranks:
        item = await store.get_result_by_rank(user_id, session_id, rank)
        if not item:
            await m.answer(f"Не нашёл документ №{rank} в последнем списке.")
            continue

        doc_id = item.get("document_id")
        if not doc_id:
            await m.answer(f"Не удалось определить document_id для документа №{rank}.")
            continue

        file_path = None
        try:
            file_path = await doc_handler.download_document(doc_id)
            if file_path and file_path.exists():
                await m.answer_document(
                    FSInputFile(str(file_path), filename=file_path.name)
                )
                sent_any = True
            else:
                await m.answer(f"Не удалось загрузить документ №{rank}.")
        except Exception as e:
            logger.error(f"Ошибка отправки документа rank={rank}, doc_id={doc_id}: {e}", exc_info=True)
            await m.answer(f"Ошибка при загрузке документа №{rank}.")
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
) -> bool:
    items = await store.get_last_search_results(user_id, session_id)
    if not items:
        return False

    text = render_results(items, total=len(items), offset=0)
    await store.update_shown_count(user_id, session_id, len(items))
    await m.answer(text, parse_mode="HTML")
    return True

#  обработка команды показать ещё результаты
async def handle_show_more(
    m: Message,
    store: PostgresChatStore,
    user_id: int,
    session_id: str,
    page_size: int = Settings.SHOW_MAX,
) -> bool:
    meta = await store.get_last_search_meta(user_id, session_id)
    items = await store.get_last_search_results(user_id, session_id)

    if not meta or not items:
        return False

    start = meta["shown_count"]
    end = min(start + page_size, len(items))

    if start >= len(items):
        await m.answer("Это уже все найденные файлы.")
        return True

    chunk = items[start:end]
    text = render_results(chunk, total=len(items), offset=start)
    await store.update_shown_count(user_id, session_id, end)
    await m.answer(text, parse_mode="HTML")
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