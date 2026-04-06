"""
Форматирование списка документов и разбор follow-up команд (общее для бота и агента).
"""
import html as html_module
import json
import re
from typing import List

# Совпадает с bot.services.config.Settings.DOWNLOAD_RE
_DOWNLOAD_RE = re.compile(
    r"^\s*(?:скачай|пришли|отправь|документ)?\s*((?:\d+\s*[,\s]\s*)*\d+)\s*$",
    re.IGNORECASE,
)


def parse_download_ranks(text: str) -> List[int]:
    """Извлекает номера документов из фразы вида «скачай 1 и 3» или «3»."""
    m = _DOWNLOAD_RE.match((text or "").strip())
    if not m:
        return []
    raw = m.group(1)
    return [int(x) for x in re.findall(r"\d+", raw)]


def render_doc_list_html(items: list[dict], total: int, offset: int = 0) -> str:
    """HTML-список документов для Telegram (аналог render_results в боте)."""
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
        text += (
            f"\n\nПоказано {shown} из {total}. Хотите получить весь список? Напишите "
            f"<b>ещё</b>, чтобы получить следующую порцию документов; <b>все</b>, "
            f"<b>покажи все</b> или <b>да</b>, чтобы получить весь список.\n"
            f"Или напишите номер документа, чтобы скачать его."
        )
    else:
        text += "\n\nНапишите номер документа, чтобы скачать его."

    return text


def strip_bot_search_meta(text: str) -> str:
    """Удаляет служебный блок meta из ответа агента."""
    return re.sub(
        r"<bot_search_meta>\s*.*?\s*</bot_search_meta>",
        "",
        text,
        flags=re.DOTALL,
    ).strip()


def extract_bot_search_meta(text: str) -> dict | None:
    """Парсит <bot_search_meta>{...}</bot_search_meta>."""
    m = re.search(
        r"<bot_search_meta>\s*(\{.*?\})\s*</bot_search_meta>",
        text,
        flags=re.DOTALL,
    )
    if not m:
        return None

    try:
        data = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def extract_document_id_lines(text: str) -> tuple[str, list[str]]:
    """
    Возвращает (текст без строк document_id, список id).
    Формат строк: document_id:12345
    """
    lines: list[str] = []
    ids: list[str] = []
    for line in (text or "").splitlines():
        s = line.strip()
        m = re.match(r"^document_id:\s*(\S+)\s*$", s, re.IGNORECASE)
        if m:
            ids.append(m.group(1))
            continue
        lines.append(line)
    return "\n".join(lines).strip(), ids
