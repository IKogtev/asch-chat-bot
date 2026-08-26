import os

from dotenv import load_dotenv

load_dotenv(override=True)


def _as_bool(raw: str | None, default: bool = False) -> bool:
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


class WebBffSettings:
    host: str = os.getenv("WEB_BFF_HOST", "0.0.0.0")
    port: int = int(os.getenv("WEB_BFF_PORT", "8010"))
    database_url: str = (os.getenv("DATABASE_URL") or os.getenv("POSTGRES_DSN") or "").strip()
    adk_api_base: str = os.getenv("ADK_API_BASE", "http://adk-agent:8000").strip()
    adk_app_name: str = os.getenv("ADK_APP_NAME", "agent").strip() or "agent"
    adk_timeout_sec: int = int(os.getenv("ADK_TIMEOUT_SEC", "180"))
    allow_dev_auth: bool = _as_bool(os.getenv("WEB_BFF_ALLOW_DEV_AUTH"), default=False)
    otp_stub: bool = _as_bool(os.getenv("WEB_BFF_OTP_STUB"), default=True)
    otp_stub_phone: str = os.getenv("WEB_BFF_OTP_STUB_PHONE", "").strip()
    otp_stub_code: str = os.getenv("WEB_BFF_OTP_STUB_CODE", "").strip()
    otp_secret: str = os.getenv("WEB_BFF_OTP_SECRET", "").strip() or "dev-otp-secret-change-me"
    otp_ttl_sec: int = int(os.getenv("WEB_BFF_OTP_TTL_SEC", "600"))
    otp_max_attempts: int = int(os.getenv("WEB_BFF_OTP_MAX_ATTEMPTS", "5"))
    otp_max_requests: int = int(os.getenv("WEB_BFF_OTP_MAX_REQUESTS", "5"))
    otp_request_window_sec: int = int(os.getenv("WEB_BFF_OTP_REQUEST_WINDOW_SEC", "900"))
    session_ttl_sec: int = int(os.getenv("WEB_BFF_SESSION_TTL_SEC", str(7 * 24 * 3600)))
    cookie_name: str = os.getenv("WEB_BFF_COOKIE_NAME", "nastya_web").strip() or "nastya_web"
    cookie_secure: bool = _as_bool(os.getenv("WEB_BFF_COOKIE_SECURE"), default=False)
    kb_manager_url: str = os.getenv("KB_MANAGER_URL", "http://kb-manager:5000").strip()
    kb_manager_token: str | None = os.getenv("KB_MANAGER_TOKEN", "").strip() or None
    downloads_dir: str = os.getenv("DOWNLOADS_DIR", "/app/data/upload/web_files").strip()
    file_secret: str = os.getenv("WEB_BFF_FILE_SECRET", "").strip() or "dev-file-secret-change-me"
    cors_origins: list[str] = [
        part.strip()
        for part in (os.getenv("WEB_BFF_CORS_ORIGINS") or "").split(",")
        if part.strip()
    ]


settings = WebBffSettings()
