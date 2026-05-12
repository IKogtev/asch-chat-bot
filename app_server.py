"""
Custom ADK API server with `/mcp-healthz` endpoint for liveness checks.

Standard `adk api_server` is replaced by this module so we can attach
extra routes to the FastAPI app returned by `get_fast_api_app`.

`/mcp-healthz` exercises live `McpToolset` instances held by the running
agent process. If any MCP toolset cannot list its tools within the budget,
the endpoint returns 503 and kubelet's liveness probe restarts the pod —
which clears stale MCP sessions (`Connection closed`, `cancel scope` leaks).
"""
from __future__ import annotations

import asyncio
import os
from typing import Any, Iterable, List

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from google.adk.cli.fast_api import get_fast_api_app
from google.adk.tools.mcp_tool import McpToolset

from utils.logger import setup_logger

logger = setup_logger("app_server", "agent.log")

AGENTS_DIR = os.environ.get("ADK_AGENTS_DIR", "/app")
MCP_HEALTHZ_TIMEOUT_SEC = float(os.environ.get("MCP_HEALTHZ_TIMEOUT_SEC", "8"))

app: FastAPI = get_fast_api_app(agents_dir=AGENTS_DIR)


def _walk_toolsets(agent: Any, seen: set[int]) -> Iterable[McpToolset]:
    """Recursively yield every `McpToolset` reachable from an agent tree."""
    if agent is None or id(agent) in seen:
        return
    seen.add(id(agent))

    for tool in getattr(agent, "tools", None) or []:
        if isinstance(tool, McpToolset):
            yield tool

    for sub in getattr(agent, "sub_agents", None) or []:
        yield from _walk_toolsets(sub, seen)


def _collect_toolsets() -> List[McpToolset]:
    """Collect live `McpToolset` instances from the running root agent."""
    from agent.start_agent import root_agent  # imported lazily to avoid cycles

    return list(_walk_toolsets(root_agent, set()))


async def _probe_toolset(toolset: McpToolset) -> dict[str, Any]:
    """Probe a single toolset by listing its tools."""
    label = getattr(toolset, "name", None) or toolset.__class__.__name__
    try:
        tools = await asyncio.wait_for(
            toolset.get_tools(readonly_context=None),
            timeout=MCP_HEALTHZ_TIMEOUT_SEC,
        )
        return {"toolset": label, "ok": True, "tool_count": len(tools)}
    except asyncio.TimeoutError:
        return {
            "toolset": label,
            "ok": False,
            "error": f"timeout after {MCP_HEALTHZ_TIMEOUT_SEC}s",
        }
    except Exception as exc:
        return {
            "toolset": label,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


@app.get("/mcp-healthz")
async def mcp_healthz() -> JSONResponse:
    """
    Liveness probe for live MCP sessions held by the agent process.

    Returns 200 only if every `McpToolset` can list its tools.
    Returns 503 if any toolset fails, signaling kubelet to restart the pod.
    """
    toolsets = _collect_toolsets()
    if not toolsets:
        return JSONResponse(
            status_code=200,
            content={"status": "ok", "toolsets": [], "note": "no McpToolset registered"},
        )

    results = await asyncio.gather(*(_probe_toolset(t) for t in toolsets))
    healthy = all(item["ok"] for item in results)
    payload = {"status": "ok" if healthy else "unhealthy", "toolsets": results}

    if not healthy:
        logger.warning("mcp-healthz failed: %s", results)

    return JSONResponse(status_code=200 if healthy else 503, content=payload)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    """Cheap liveness/readiness probe for the FastAPI process itself."""
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.environ.get("HOST", "0.0.0.0"),
        port=int(os.environ.get("PORT", "8000")),
    )
