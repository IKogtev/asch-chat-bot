import asyncio
from typing import Any, Callable, Optional

from google.adk.tools.base_toolset import BaseToolset
from google.adk.tools.mcp_tool import McpToolset

from utils.logger import setup_logger

logger = setup_logger("refreshing_mcp_toolset", "agent.log")

MCP_SESSION_ERROR_MARKERS = (
    "connection closed",
    "failed to get tools from mcp server",
    "attempted to exit cancel scope in a different task",
    "session termination failed",
    "session is closed",
    "stream closed",
    "server disconnected",
    "endofstream",
    "brokenresourceerror",
    "closedresourceerror",
    "mcperror",
)


def _iter_exception_tree(exc: BaseException) -> list[BaseException]:
    seen: set[int] = set()
    stack = [exc]
    result: list[BaseException] = []

    while stack:
        current = stack.pop()
        current_id = id(current)
        if current_id in seen:
            continue
        seen.add(current_id)
        result.append(current)

        nested = getattr(current, "exceptions", None)
        if nested:
            stack.extend(item for item in nested if isinstance(item, BaseException))

        cause = getattr(current, "__cause__", None)
        if isinstance(cause, BaseException):
            stack.append(cause)

        context = getattr(current, "__context__", None)
        if isinstance(context, BaseException):
            stack.append(context)

    return result


def is_mcp_session_error(exc: BaseException) -> bool:
    for item in _iter_exception_tree(exc):
        message = f"{type(item).__name__}: {item!r}".lower()
        if any(marker in message for marker in MCP_SESSION_ERROR_MARKERS):
            return True
    return False


class RefreshingMcpToolset(BaseToolset):
    """Refreshes the underlying McpToolset when tool discovery hits a stale session."""

    def __init__(
        self,
        *,
        connection_params: Any,
        tool_filter: Any = None,
        tool_name_prefix: Optional[str] = None,
        mcp_toolset_factory: Callable[..., McpToolset] = McpToolset,
        **mcp_toolset_kwargs: Any,
    ) -> None:
        super().__init__(tool_filter=tool_filter, tool_name_prefix=tool_name_prefix)
        self.connection_params = connection_params
        self._mcp_tool_filter = tool_filter
        self._mcp_tool_name_prefix = tool_name_prefix
        self._mcp_toolset_factory = mcp_toolset_factory
        self._mcp_toolset_kwargs = dict(mcp_toolset_kwargs)
        self._toolset = self._create_toolset()
        self._refresh_lock: asyncio.Lock | None = None
        self._refresh_lock_loop: asyncio.AbstractEventLoop | None = None

    def _create_toolset(self) -> McpToolset:
        return self._mcp_toolset_factory(
            connection_params=self.connection_params,
            tool_filter=self._mcp_tool_filter,
            tool_name_prefix=self._mcp_tool_name_prefix,
            **self._mcp_toolset_kwargs,
        )

    def _get_refresh_lock(self) -> asyncio.Lock:
        current_loop = asyncio.get_running_loop()
        if self._refresh_lock is None or self._refresh_lock_loop is not current_loop:
            self._refresh_lock = asyncio.Lock()
            self._refresh_lock_loop = current_loop
        return self._refresh_lock

    async def _close_toolset(self, toolset: McpToolset) -> None:
        try:
            await toolset.close()
        except Exception as exc:
            logger.warning("MCP toolset cleanup failed during refresh: %s", exc, exc_info=True)

    async def _refresh_toolset(self, stale_toolset: McpToolset) -> McpToolset:
        async with self._get_refresh_lock():
            if self._toolset is stale_toolset:
                await self._close_toolset(stale_toolset)
                self._toolset = self._create_toolset()
            return self._toolset

    async def get_tools(self, readonly_context: Any = None) -> list[Any]:
        current_toolset = self._toolset
        try:
            return await current_toolset.get_tools(readonly_context)
        except Exception as exc:
            if not is_mcp_session_error(exc):
                raise

            logger.warning(
                "MCP session error while getting tools, refreshing toolset once: %s",
                exc,
                exc_info=True,
            )
            refreshed_toolset = await self._refresh_toolset(current_toolset)
            return await refreshed_toolset.get_tools(readonly_context)

    async def close(self) -> None:
        await self._close_toolset(self._toolset)

    def get_auth_config(self) -> Any:
        return self._toolset.get_auth_config()

    async def process_llm_request(self, **kwargs: Any) -> None:
        await self._toolset.process_llm_request(**kwargs)

    async def get_resource_info(self, *args: Any, **kwargs: Any) -> Any:
        return await self._toolset.get_resource_info(*args, **kwargs)

    async def list_resources(self, *args: Any, **kwargs: Any) -> Any:
        return await self._toolset.list_resources(*args, **kwargs)

    async def read_resource(self, *args: Any, **kwargs: Any) -> Any:
        return await self._toolset.read_resource(*args, **kwargs)
