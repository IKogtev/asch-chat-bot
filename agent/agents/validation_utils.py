import json
from typing import Any, Iterable, Mapping


def _truncate(value: Any, limit: int = 300) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[:limit] + "..."


def build_validation_error(
    *,
    agent: str,
    stage: str,
    problem: str,
    data: Mapping[str, Any] | None = None,
    fields: Iterable[str] = (),
) -> ValueError:
    payload = dict(data or {})
    details = []
    for field in fields:
        if field in payload:
            details.append(f"{field}={_truncate(repr(payload.get(field)))}")

    payload_excerpt = _truncate(json.dumps(payload, ensure_ascii=False, default=str), 500)
    message = f"{agent} validation failed at {stage}: {problem}"
    if details:
        message += "; " + ", ".join(details)
    message += f"; payload={payload_excerpt}"
    return ValueError(message)
