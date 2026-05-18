import html
import re


_TOKEN_PREFIX = "\u0000BOTFMT"
_TOKEN_SUFFIX = "\u0000"
_ALLOWED_SIMPLE_TAGS = {
    "b": "b",
    "strong": "b",
    "i": "i",
    "em": "i",
    "code": "code",
}
_TAG_RE = re.compile(r"</?(?:b|strong|i|em|code)\s*>|<a\s+href=[\"'][^\"']+[\"']\s*>|</a>", re.I)
_URL_RE = re.compile(r"^(?:https?://|mailto:)[^\s<>\"']+$", re.I)


def _strip_markdown_tables(text: str) -> str:
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            lines.append(line)
            continue
        if re.fullmatch(r"\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?", stripped):
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            cells = [cell.strip() for cell in stripped.strip("|").split("|")]
            cells = [cell for cell in cells if cell]
            if cells:
                lines.append("- " + (": ".join(cells[:2]) if len(cells) == 2 else " - ".join(cells)))
            continue
        lines.append(line)
    return "\n".join(lines)


def _preserve_allowed_tag(match: re.Match, tokens: list[str]) -> str:
    tag = match.group(0)
    tag_lower = tag.lower()

    if tag_lower == "</a>":
        normalized = "</a>"
    elif tag_lower.startswith("<a"):
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", tag, re.I)
        href = href_match.group(1).strip() if href_match else ""
        if not _URL_RE.match(href):
            return html.escape(tag)
        normalized = f'<a href="{html.escape(href, quote=True)}">'
    else:
        closing = tag_lower.startswith("</")
        name = re.sub(r"[</>\s]", "", tag_lower)
        normalized_name = _ALLOWED_SIMPLE_TAGS.get(name)
        if not normalized_name:
            return html.escape(tag)
        normalized = f"</{normalized_name}>" if closing else f"<{normalized_name}>"

    token = f"{_TOKEN_PREFIX}{len(tokens)}{_TOKEN_SUFFIX}"
    tokens.append(normalized)
    return token


def _escape_preserving_allowed_tags(text: str) -> str:
    tokens: list[str] = []
    protected = _TAG_RE.sub(lambda match: _preserve_allowed_tag(match, tokens), text)
    escaped = html.escape(protected)
    for index, tag in enumerate(tokens):
        escaped = escaped.replace(html.escape(f"{_TOKEN_PREFIX}{index}{_TOKEN_SUFFIX}"), tag)
    return escaped


def _convert_markdown_subset(text: str) -> str:
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<i>\1</i>", text)
    text = re.sub(r"`([^`\n]+?)`", r"<code>\1</code>", text)
    text = re.sub(
        r"\[([^\]\n]+?)\]\((https?://[^\s)<>]+|mailto:[^\s)<>]+)\)",
        r'<a href="\2">\1</a>',
        text,
    )
    return text


def _bold_line_label(line: str) -> str:
    match = re.match(r"^(\s*(?:[-*]\s+|\d+\.\s+)?)([^:\n<]{2,60}):(\s+.+)$", line)
    if not match:
        return line
    prefix, label, rest = match.groups()
    if "<b>" in label or "</b>" in label:
        return line
    return f"{prefix}<b>{label.strip()}:</b>{rest}"


def _bold_first_phrase(lines: list[str]) -> list[str]:
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if "<b>" in stripped or stripped.startswith(("-", "*")) or re.match(r"^\d+\.", stripped):
            return lines

        leading = line[: len(line) - len(line.lstrip())]
        content = stripped
        end = len(content)
        for match in re.finditer(r"[.!?。]\s+", content):
            if match.end() <= 140:
                end = match.end()
                break
        if end > 140:
            split_at = content.rfind(" ", 0, 140)
            end = split_at if split_at > 0 else 140
        if end <= 0:
            return lines

        lines[index] = f"{leading}<b>{content[:end].strip()}</b>{content[end:]}"
        return lines
    return lines


def _wrap_long_lines(text: str, limit: int = 820) -> str:
    wrapped = []
    for line in text.splitlines():
        current = line
        while len(current) > limit:
            split_at = current.rfind(" ", 0, limit)
            if split_at < limit // 2:
                split_at = limit
            wrapped.append(current[:split_at].rstrip())
            current = current[split_at:].lstrip()
        wrapped.append(current)
    return "\n".join(wrapped)


def format_bot_response(text: str) -> str:
    """Возвращает единый безопасный HTML для Telegram и Max."""
    if not text:
        return ""

    normalized = _strip_markdown_tables(str(text).strip())
    normalized = _wrap_long_lines(normalized)
    formatted = _escape_preserving_allowed_tags(normalized)
    formatted = _convert_markdown_subset(formatted)

    lines = [_bold_line_label(line) for line in formatted.splitlines()]
    lines = _bold_first_phrase(lines)
    return "\n".join(lines).strip()
