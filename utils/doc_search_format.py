"""
Форматирование списка документов и разбор follow-up команд (общее для бота и агента).
"""
import html as html_module
import os
import re
from typing import List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv(override=True)

_ARCHIVE_LABEL = "(Архивный)"
_ARCHIVE_FOLDER_RE = re.compile(r"^\d+\s+Архив$", re.UNICODE)


def _doc_search_archive_section() -> str:
    """Имя папки архива (без зависимости от qdrant_client)."""
    return os.getenv("DOC_SEARCH_ARCHIVE_SECTION", "6 Архив").strip()


def _first_path_segment(path: str) -> str:
    return path.strip().replace("\\", "/").split("/", 1)[0].strip()


def _is_archive_folder_name(name: str) -> bool:
    """True для «5 Архив», «6 Архив» и совпадения с DOC_SEARCH_ARCHIVE_SECTION."""
    folder = name.strip()
    if not folder:
        return False
    configured = _doc_search_archive_section()
    if configured and folder == configured:
        return True
    return bool(_ARCHIVE_FOLDER_RE.match(folder))

_RANK_SEP = r"\s*(?:[,\s]+|\s+и\s+)\s*"


def max_doc_list_rank() -> int:
    """Максимальный номер документа в списке (KB_TOP_K / SHOW_LIST_SIZE)."""
    return max(
        1,
        int(
            os.getenv(
                "KB_TOP_K", 20
            )
        ),
    )


def _rank_number_pattern(max_rank: int) -> str:
    if max_rank < 1:
        return "(?!)"
    return "(?:" + "|".join(str(i) for i in range(max_rank, 0, -1)) + ")"


def _rank_list_pattern(rank_pat: str) -> str:
    return rf"(?:{rank_pat}{_RANK_SEP})*{rank_pat}"


def build_download_rank_patterns(max_rank: int) -> Tuple[re.Pattern[str], re.Pattern[str], re.Pattern[str]]:
    """Собирает regexp для разбора номеров документов (1..max_rank)."""
    rank_pat = _rank_number_pattern(max_rank)
    rank_list = _rank_list_pattern(rank_pat)
    download_re = re.compile(
        rf"^\s*(?:скачай|пришли|отправь|документ)?\s*({rank_list})\s*$",
        re.IGNORECASE | re.UNICODE,
    )
    ranks_only_re = re.compile(
        rf"^\s*({rank_list})\s*$",
        re.IGNORECASE | re.UNICODE,
    )
    rank_block_at_end = re.compile(
        rf"(?<![\d.])({rank_pat}(?:{_RANK_SEP}{rank_pat})+)\s*$",
        re.UNICODE | re.IGNORECASE,
    )
    return download_re, ranks_only_re, rank_block_at_end


_MAX_DOC_LIST_RANK = max_doc_list_rank()
_DOWNLOAD_RE, _RANKS_ONLY_RE, _RANK_BLOCK_AT_END = build_download_rank_patterns(_MAX_DOC_LIST_RANK)
# Совпадает с bot.services.config.Settings.DOWNLOAD_RE
DOWNLOAD_RE = _DOWNLOAD_RE

_LOOSE_TAIL_MAX_CHARS = 15


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


def is_archive_document(item: dict) -> bool:
    """True, если документ лежит в папке архива (DOC_SEARCH_ARCHIVE_SECTION или «N Архив»)."""
    for key in ("source_path", "relative_path", "file_path"):
        raw = (item.get(key) or "").strip().replace("\\", "/")
        if raw and _is_archive_folder_name(_first_path_segment(raw)):
            return True

    kb_id = (item.get("kb_id") or "").strip()
    if kb_id and _is_archive_folder_name(kb_id):
        return True

    section_path = item.get("section_path")
    if isinstance(section_path, list) and section_path:
        if _is_archive_folder_name(str(section_path[0]).strip()):
            return True
    elif isinstance(section_path, str) and section_path.strip():
        first = section_path.replace(" / ", "/").split("/", 1)[0].strip()
        if _is_archive_folder_name(first):
            return True

    section_relationships = item.get("section_relationships")
    if isinstance(section_relationships, list):
        for rel in section_relationships:
            if _is_archive_folder_name(str(rel).strip()):
                return True

    return False


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
        archive_prefix = f"{_ARCHIVE_LABEL} " if is_archive_document(item) else ""
        snippet = (item.get("snippet") or "").strip().replace("\n", " ")
        if len(snippet) > 180:
            snippet = snippet[:177] + "..."
        snippet = html_module.escape(snippet)

        block = f"<b>{i}. {archive_prefix}{title}</b>"
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