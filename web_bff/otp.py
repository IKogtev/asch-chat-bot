"""OTP: код только как хеш, в dev — stub (лог + dev_code в ответе)."""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from typing import Any, Optional

import asyncpg

from utils.logger import setup_logger
from web_bff.config import WebBffSettings
from web_bff.otp_delivery import deliver_otp_code
from web_bff.phones import normalize_phone
from web_bff.users import get_messenger_accounts, get_user_by_phone

logger = setup_logger("web_bff_otp", "web_bff.log")

OTP_OK = {"status": "ok"}
_CODE_RE = re.compile(r"^\d{4,6}$")


class OtpError(Exception):
    def __init__(self, status_code: int, detail: str):
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


def hash_secret(secret: str, value: str) -> str:
    return hmac.new(secret.encode("utf-8"), value.encode("utf-8"), hashlib.sha256).hexdigest()


def hash_session_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_otp_code(length: int = 6) -> str:
    return f"{secrets.randbelow(10 ** length):0{length}d}"


def generate_session_token() -> str:
    return secrets.token_urlsafe(32)


class OtpService:
    def __init__(self, pool: asyncpg.Pool, settings: WebBffSettings):
        self.pool = pool
        self.settings = settings

    def _code_hash(self, code: str) -> str:
        return hash_secret(self.settings.otp_secret, code)

    def _stub_pair(self) -> tuple[str, str] | None:
        if not self.settings.otp_stub:
            return None
        phone = normalize_phone(self.settings.otp_stub_phone)
        code = (self.settings.otp_stub_code or "").strip()
        if not phone or not _CODE_RE.match(code):
            return None
        return phone, code

    async def request(self, raw_phone: str) -> dict[str, Any]:
        phone = normalize_phone(raw_phone)
        if len(re.sub(r"\D", "", phone)) < 10:
            raise OtpError(400, "invalid_phone")

        async with self.pool.acquire() as conn:
            recent = await conn.fetchval(
                """
                SELECT COUNT(*) FROM otp_challenges
                WHERE phone_number = $1
                  AND created_at > now() - ($2 * interval '1 second')
                """,
                phone,
                self.settings.otp_request_window_sec,
            )
            if int(recent or 0) >= self.settings.otp_max_requests:
                logger.info("otp request rate-limited phone=%s", phone)
                return dict(OTP_OK)

        user = await get_user_by_phone(self.pool, phone)
        eligible = user is not None and not user["is_blocked"]
        code = generate_otp_code()
        if self.settings.otp_stub:
            pair = self._stub_pair()
            if pair is None:
                logger.warning("otp stub enabled but WEB_BFF_OTP_STUB_PHONE/CODE are not set")
                eligible = False
            elif phone != pair[0]:
                eligible = False
            else:
                code = pair[1]

        accounts: list = []
        if eligible and not self.settings.otp_stub:
            accounts = await get_messenger_accounts(self.pool, user["id"])
            if not accounts:
                logger.info("otp no messenger accounts user=%s", user["id"])
                raise OtpError(400, "messenger_required")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                if eligible:
                    await conn.execute(
                        """
                        UPDATE otp_challenges
                        SET consumed_at = now()
                        WHERE user_id = $1 AND consumed_at IS NULL
                        """,
                        user["id"],
                    )
                await conn.execute(
                    """
                    INSERT INTO otp_challenges (
                        phone_number, user_id, code_hash, max_attempts, expires_at, consumed_at
                    )
                    VALUES (
                        $1, $2, $3, $4,
                        now() + ($5 * interval '1 second'),
                        CASE WHEN $6 THEN NULL ELSE now() END
                    )
                    """,
                    phone,
                    user["id"] if user else "",
                    self._code_hash(code),
                    self.settings.otp_max_attempts,
                    self.settings.otp_ttl_sec,
                    eligible,
                )

        if not eligible:
            logger.info("otp request skipped exists=%s", user is not None)
            return dict(OTP_OK)

        if self.settings.otp_stub:
            logger.info("otp stub user=%s phone=%s code=%s", user["id"], phone, code)
            return {"status": "ok", "dev_code": code}

        sent = await deliver_otp_code(self.settings, accounts, code)
        if sent == 0:
            logger.error("otp delivery failed for all channels user=%s", user["id"])
        return dict(OTP_OK)

    async def verify(self, raw_phone: str, code: str) -> str:
        phone = normalize_phone(raw_phone)
        code = (code or "").strip()
        if not phone or not _CODE_RE.match(code):
            raise OtpError(401, "invalid_code")

        async with self.pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT id, user_id, code_hash, attempts, max_attempts
                    FROM otp_challenges
                    WHERE phone_number = $1
                      AND consumed_at IS NULL
                      AND expires_at > now()
                    ORDER BY created_at DESC
                    LIMIT 1
                    FOR UPDATE
                    """,
                    phone,
                )
                if row is None:
                    raise OtpError(401, "invalid_code")

                attempts = int(row["attempts"]) + 1
                await conn.execute(
                    "UPDATE otp_challenges SET attempts = $2 WHERE id = $1",
                    row["id"],
                    attempts,
                )
                if attempts > int(row["max_attempts"]):
                    await conn.execute(
                        "UPDATE otp_challenges SET consumed_at = now() WHERE id = $1",
                        row["id"],
                    )
                    raise OtpError(401, "invalid_code")

                expected = str(row["code_hash"])
                if not hmac.compare_digest(expected, self._code_hash(code)):
                    raise OtpError(401, "invalid_code")

                await conn.execute(
                    "UPDATE otp_challenges SET consumed_at = now() WHERE id = $1",
                    row["id"],
                )
                token = generate_session_token()
                await conn.execute(
                    """
                    INSERT INTO web_sessions (user_id, token_hash, expires_at)
                    VALUES ($1, $2, now() + ($3 * interval '1 second'))
                    """,
                    row["user_id"],
                    hash_session_token(token),
                    self.settings.session_ttl_sec,
                )
        return token

    async def user_id_for_token(self, token: str) -> Optional[str]:
        raw = (token or "").strip()
        if not raw:
            return None
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT user_id FROM web_sessions
                WHERE token_hash = $1
                  AND revoked_at IS NULL
                  AND expires_at > now()
                """,
                hash_session_token(raw),
            )
        return str(row["user_id"]) if row else None

    async def revoke_token(self, token: str) -> None:
        raw = (token or "").strip()
        if not raw:
            return
        async with self.pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE web_sessions
                SET revoked_at = now()
                WHERE token_hash = $1 AND revoked_at IS NULL
                """,
                hash_session_token(raw),
            )
