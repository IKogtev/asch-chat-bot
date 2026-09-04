"""Подписанные URL файлов: браузер не видит путь комплекта и токен KB."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from bot.services.config import Settings
from bot.services.product_kits import _resolve_inside

TOKEN_TTL_SEC = 12 * 3600


class FileTokenError(ValueError):
    pass


def _b64url(raw: bytes) -> str:
    import base64

    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(text: str) -> bytes:
    import base64

    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


class FileUrlIssuer:
    def __init__(self, secret: str, ttl_sec: int = TOKEN_TTL_SEC):
        self.secret = secret.encode("utf-8")
        self.ttl_sec = ttl_sec

    def _sign(self, payload: dict[str, Any]) -> str:
        body = _b64url(json.dumps(payload, separators=(",", ":")).encode("utf-8"))
        sig = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
        return f"{body}.{sig}"

    def kit_url(self, user_id: str, path: str, name: str) -> str:
        kind, rel = kit_ref_from_path(path)
        token = self._sign(
            {
                "u": user_id,
                "k": "kit",
                "root": kind,
                "p": rel,
                "n": name,
                "exp": int(time.time()) + self.ttl_sec,
            }
        )
        return f"/files/{quote(token, safe='')}"

    def kb_url(self, user_id: str, document_id: str, name: str) -> str:
        token = self._sign(
            {
                "u": user_id,
                "k": "kb",
                "id": document_id,
                "n": name,
                "exp": int(time.time()) + self.ttl_sec,
            }
        )
        return f"/files/{quote(token, safe='')}"

    def parse(self, token: str, *, allow_expired: bool = False) -> dict[str, Any]:
        try:
            body, sig = token.split(".", 1)
        except ValueError as exc:
            raise FileTokenError("malformed") from exc
        expected = hmac.new(self.secret, body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
        if not hmac.compare_digest(expected, sig):
            raise FileTokenError("bad_signature")
        try:
            payload = json.loads(_b64url_decode(body))
        except (json.JSONDecodeError, ValueError) as exc:
            raise FileTokenError("malformed") from exc
        if not allow_expired and int(payload.get("exp") or 0) < int(time.time()):
            raise FileTokenError("expired")
        return payload

    def refresh_url(self, url: str, user_id: str) -> str:
        """Перевыпустить сохранённую ссылку после проверки подписи и владельца."""
        token = unquote_token(url)
        payload = self.parse(token, allow_expired=True)
        if str(payload.get("u") or "") != str(user_id):
            raise FileTokenError("wrong_user")
        payload["exp"] = int(time.time()) + self.ttl_sec
        return f"/files/{quote(self._sign(payload), safe='')}"


def unquote_token(url: str) -> str:
    from urllib.parse import unquote

    path = (url or "").split("?", 1)[0].rstrip("/")
    token = path.rsplit("/", 1)[-1]
    if not token:
        raise FileTokenError("malformed")
    return unquote(token)


def kit_ref_from_path(path: str) -> tuple[str, str]:
    resolved = Path(path).resolve()
    mapping = (
        ("products", Path(Settings.PRODUCT_KITS_ROOT)),
        ("archive", Path(Settings.ARCHIVE_KITS_ROOT)),
    )
    for kind, root in mapping:
        try:
            rel = resolved.relative_to(root.resolve())
        except ValueError:
            continue
        return kind, rel.as_posix()
    raise FileTokenError("path_outside_kits")


def resolve_kit_file(root_kind: str, relative: str) -> Path:
    if root_kind == "archive":
        root = Path(Settings.ARCHIVE_KITS_ROOT)
    elif root_kind == "products":
        root = Path(Settings.PRODUCT_KITS_ROOT)
    else:
        raise FileTokenError("bad_root")
    child = root / Path(relative)
    _, resolved = _resolve_inside(root, child)
    if not resolved.is_file():
        raise FileTokenError("not_found")
    return resolved
