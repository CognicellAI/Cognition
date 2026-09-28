"""Compatibility transport controls for remote MCP Streamable HTTP sessions.

The MCP Streamable HTTP specification makes the client-initiated GET notification
stream optional.  Some conforming servers support POST-only request/response
traffic.  This module lets a runtime definition opt out of that optional stream
without changing the MCP request lifecycle or authentication behavior.

`langchain-mcp-adapters` currently does not expose this transport switch.  The
adapter creates sessions from module-level helpers, so install the narrow shim at
startup and preserve its standard code path for every normal connection.
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any, cast

import anyio
import httpx
from anyio.streams.memory import MemoryObjectReceiveStream, MemoryObjectSendStream
from langchain_mcp_adapters.callbacks import _MCPCallbacks
from langchain_mcp_adapters.sessions import Connection
from mcp import ClientSession
from mcp.client.streamable_http import (
    GetSessionIdCallback,
    StreamableHTTPTransport,
    create_mcp_http_client,
)
from mcp.shared.message import SessionMessage


class _PostOnlyStreamableHTTPTransport(StreamableHTTPTransport):
    """Suppress only the optional standalone server-notification GET stream."""

    def _is_initialized_notification(self, message: object) -> bool:
        del message
        return False


@asynccontextmanager
async def _post_only_streamable_http_client(
    url: str,
    *,
    http_client: httpx.AsyncClient | None = None,
    terminate_on_close: bool = True,
) -> AsyncGenerator[
    tuple[
        MemoryObjectReceiveStream[SessionMessage | Exception],
        MemoryObjectSendStream[SessionMessage],
        GetSessionIdCallback,
    ],
    None,
]:
    """Create a standard Streamable HTTP session without its optional GET stream."""
    read_stream_writer, read_stream = anyio.create_memory_object_stream[SessionMessage | Exception](
        0
    )
    write_stream, write_stream_reader = anyio.create_memory_object_stream[SessionMessage](0)
    client_provided = http_client is not None
    client = http_client or create_mcp_http_client()
    transport = _PostOnlyStreamableHTTPTransport(url)

    async with anyio.create_task_group() as task_group:
        try:
            async with contextlib.AsyncExitStack() as stack:
                if not client_provided:
                    await stack.enter_async_context(client)

                def ignore_optional_notification_stream() -> None:
                    return None

                task_group.start_soon(
                    transport.post_writer,
                    client,
                    write_stream_reader,
                    read_stream_writer,
                    write_stream,
                    ignore_optional_notification_stream,
                    task_group,
                )
                try:
                    yield read_stream, write_stream, transport.get_session_id
                finally:
                    if transport.session_id and terminate_on_close:
                        await transport.terminate_session(client)
                    task_group.cancel_scope.cancel()
        finally:
            await read_stream_writer.aclose()
            await write_stream.aclose()


@asynccontextmanager
async def _create_post_only_streamable_http_session(
    connection: Mapping[str, Any],
    *,
    mcp_callbacks: _MCPCallbacks | None,
) -> AsyncIterator[ClientSession]:
    """Build a ClientSession using the same options as the adapter's HTTP transport."""
    from datetime import timedelta

    url = str(connection["url"])
    headers = cast(dict[str, Any] | None, connection.get("headers"))
    timeout = connection.get("timeout", timedelta(seconds=30))
    sse_read_timeout = connection.get("sse_read_timeout", timedelta(minutes=5))
    timeout_seconds = timeout.total_seconds() if isinstance(timeout, timedelta) else timeout
    sse_read_timeout_seconds = (
        sse_read_timeout.total_seconds()
        if isinstance(sse_read_timeout, timedelta)
        else sse_read_timeout
    )
    factory = connection.get("httpx_client_factory") or create_mcp_http_client
    client = factory(
        headers=headers,
        timeout=httpx.Timeout(timeout_seconds, read=sse_read_timeout_seconds),
        auth=connection.get("auth"),
    )
    session_kwargs = dict(connection.get("session_kwargs") or {})
    if mcp_callbacks is not None:
        if mcp_callbacks.logging_callback is not None:
            session_kwargs["logging_callback"] = mcp_callbacks.logging_callback
        if mcp_callbacks.elicitation_callback is not None:
            session_kwargs["elicitation_callback"] = mcp_callbacks.elicitation_callback

    async with (
        client,
        _post_only_streamable_http_client(
            url,
            http_client=client,
            terminate_on_close=bool(connection.get("terminate_on_close", True)),
        ) as (read, write, _),
        ClientSession(read, write, **session_kwargs) as session,
    ):
        yield session


def server_notification_stream_enabled(connection: Mapping[str, Any]) -> bool:
    """Return whether the connection asks for optional server-initiated messages."""
    return connection.get("server_notification_stream", True) is not False


def install_server_notification_stream_compatibility() -> None:
    """Install the adapter shim once, retaining its native path by default."""
    import langchain_mcp_adapters.client as client_module
    import langchain_mcp_adapters.sessions as sessions_module
    import langchain_mcp_adapters.tools as tools_module

    original_create_session = sessions_module.create_session
    if getattr(original_create_session, "_cognition_post_only_compat", False):
        return

    @asynccontextmanager
    async def create_session(
        connection: Connection,
        *,
        mcp_callbacks: _MCPCallbacks | None = None,
    ) -> AsyncIterator[ClientSession]:
        if connection.get("transport") in {
            "streamable_http",
            "streamable-http",
            "http",
        } and not server_notification_stream_enabled(connection):
            async with _create_post_only_streamable_http_session(
                connection, mcp_callbacks=mcp_callbacks
            ) as session:
                yield session
            return
        standard_connection = cast(
            Connection,
            {
                key: value
                for key, value in connection.items()
                if key != "server_notification_stream"
            },
        )
        async with original_create_session(
            standard_connection, mcp_callbacks=mcp_callbacks
        ) as session:
            yield session

    create_session._cognition_post_only_compat = True  # type: ignore[attr-defined]
    sessions_module.create_session = create_session
    tools_module.create_session = create_session
    client_module.create_session = create_session
