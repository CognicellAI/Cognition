"""Bounded handling of remote MCP transport failures without replaying tools."""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import httpx
import structlog
from langchain_mcp_adapters.interceptors import MCPToolCallRequest, MCPToolCallResult
from mcp.types import CallToolResult, TextContent

logger = structlog.get_logger(__name__)


def is_transient_transport_failure(error: Exception) -> bool:
    """Recognize transport-only failures, including SDK task-group wrappers.

    Mixed groups, authorization failures, and programmer errors propagate. Never
    inspect or expose exception text: upstream URLs and bodies may contain secrets.
    """
    if isinstance(error, ExceptionGroup):
        return bool(error.exceptions) and all(
            is_transient_transport_failure(child) for child in error.exceptions
        )
    if isinstance(error, httpx.HTTPStatusError):
        return error.response.status_code in {408, 429} or error.response.status_code >= 500
    return isinstance(error, (httpx.TransportError, TimeoutError))


async def handle_mcp_transport_failure(
    request: MCPToolCallRequest,
    handler: Callable[[MCPToolCallRequest], Awaitable[MCPToolCallResult]],
) -> MCPToolCallResult:
    """Return a safe tool failure while leaving retry decisions to the caller.

    A failed response does not prove the provider did not execute the operation.
    In particular, never automatically repeat a possibly mutating tools/call.
    """
    try:
        return await handler(request)
    except Exception as error:
        if not is_transient_transport_failure(error):
            raise
        logger.warning("mcp_tool_transport_unavailable", error_type=type(error).__name__)
        return CallToolResult(
            isError=True,
            content=[
                TextContent(
                    type="text",
                    text=(
                        "MCP transport unavailable. No usable result was received; the operation's "
                        "outcome may be unknown. Do not assume success or automatically repeat "
                        "an operation that may have side effects."
                    ),
                )
            ],
        )
