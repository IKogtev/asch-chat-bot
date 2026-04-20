"""
Форматирование списка документов и разбор follow-up команд (общее для бота и агента).
"""
import html as html_module
import re
from typing import List, Optional

# Совпадает с bot.services.config.Settings.DOWNLOAD_RE
_DOWNLOAD_RE = re.compile(
    r"^\s*(?:скачай|пришли|отправь|документ)?\s*((?:\d+\s*(?:[,\s]+|\s+и\s+)\s*)*\d+)\s*$",
    re.IGNORECASE | re.UNICODE,
)
# Только номера через запятую/пробел/«и» (вся строка), без глагола — как «8,13» или «8 и 13».
_RANKS_ONLY_RE = re.compile(
    r"^\s*(\d+(?:\s*(?:[,\s]+|\s+и\s+)\s*\d+)*)\s*$",
    re.IGNORECASE | re.UNICODE,
)

_LOOSE_TAIL_MAX_CHARS = 15
# Два и больше номеров в конце строки (после «… Fort Knox 1 и 5»).
_RANK_BLOCK_AT_END = re.compile(
    r"(?<![\d.])(\d+(?:\s*(?:[,\s]+|\s+и\s+)\s*\d+)+)\s*$",
    re.UNICODE | re.IGNORECASE,
)


def parse_download_ranks(text: str) -> List[int]:
    """Извлекает номера документов из фразы вида «скачай 1 и 3», «3» или «8,13»."""
    raw_text = (text or "").strip()
    m = _DOWNLOAD_RE.match(raw_text)
    if m:
        raw = m.group(1)
        return [int(x) for x in re.findall(r"\d+", raw)]
    m2 = _RANKS_ONLY_RE.match(raw_text)
    if m2:
        return [int(x) for x in re.findall(r"\d+", m2.group(1))]
    return []


def extract_loose_tail_ranks(text: str) -> List[int]:
    """Номера в хвосте строки (несколько через запятую/пробел/«и»), только для коротких фраз."""
    t = (text or "").strip()
    if not t or len(t) > _LOOSE_TAIL_MAX_CHARS:
        return []
    m = _RANK_BLOCK_AT_END.search(t)
    if not m:
        return []
    return parse_download_ranks(m.group(1).strip())


def extract_download_ranks(user_text: str, extra_hint: Optional[str] = None) -> List[int]:
    """
    Ранги для скачивания: строгий разбор, затем хвост строки; при необходимости — второй проход по
    подсказке (например search_query диспетчера «… 1 и 5»), чтобы не запускать ложный doc_search.
    """
    ut = (user_text or "").strip()
    r = parse_download_ranks(ut)
    if r:
        return r
    r = extract_loose_tail_ranks(ut)
    if r:
        return r
    if extra_hint:
        h = extra_hint.strip()
        r = parse_download_ranks(h)
        if r:
            return r
        r = extract_loose_tail_ranks(h)
        if r:
            return r
    return []


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