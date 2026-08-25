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
    cors_origins: list[str] = [
        part.strip()
        for part in (os.getenv("WEB_BFF_CORS_ORIGINS") or "").split(",")
        if part.strip()
    ]


settings = WebBffSettings()
