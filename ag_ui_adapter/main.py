"""
AG-UI HTTP endpoint for CopilotKit (@ag-ui/client HttpAgent).

Uses the same LlmAgent + MCP configuration as `agent/agent_v1.py`.
Telegram continues to use `adk api_server` (POST /run) on the `adk-agent` service; this
process is only for the web CopilotKit contour.
"""

from __future__ import annotations

import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ag_ui_adk import ADKAgent, add_adk_fastapi_endpoint

load_dotenv(override=True)

# Import after env — initializes root_agent, MCP, prompt watcher (same as prod agent module)
from agent import root_agent  # noqa: E402


def _optional_api_key_guard(app: FastAPI) -> None:
    secret = (os.getenv("AG_UI_ADAPTER_API_KEY") or "").strip()
    if not secret:
        return

    @app.middleware("http")
    async def _check_key(request: Request, call_next):
        if request.url.path == "/health":
            return await call_next(request)
        auth = request.headers.get("authorization") or ""
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
        if token != secret:
            return JSONResponse({"detail": "Unauthorized"}, status_code=401)
        return await call_next(request)


def create_app() -> FastAPI:
    app = FastAPI(title="AG-UI ADK adapter", version="0.1.0")

    app_name = os.getenv("ADK_APP_NAME", "agent").strip() or "agent"
    default_user = os.getenv("AG_UI_DEFAULT_USER_ID", "web").strip() or "web"
    session_timeout = int(os.getenv("AG_UI_SESSION_TIMEOUT_SEC", "3600"))
    use_thread_as_session = os.getenv("AG_UI_USE_THREAD_AS_SESSION", "true").lower() in (
        "1",
        "true",
        "yes",
    )

    wrapped = ADKAgent(
        adk_agent=root_agent,
        app_name=app_name,
        user_id=default_user,
        session_timeout_seconds=session_timeout,
        use_in_memory_services=True,
        use_thread_id_as_session_id=use_thread_as_session,
    )

    # CopilotKit HttpAgent expects the AG-UI stream at the root URL (trailing slash in client).
    add_adk_fastapi_endpoint(
        app,
        wrapped,
        path="/",
        extract_headers=["x-web-user-id", "x-web-session-id"],
    )

    @app.get("/health")
    async def health() -> dict:
        return {"status": "ok", "app_name": app_name}

    _optional_api_key_guard(app)
    return app


app = create_app()
