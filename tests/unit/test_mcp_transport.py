from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from langchain_mcp_adapters import sessions
from mcp.types import TextContent

from server.app.agent.mcp_transport import (
    install_server_notification_stream_compatibility,
    server_notification_stream_enabled,
)


def test_server_notification_stream_is_enabled_by_default() -> None:
    assert server_notification_stream_enabled({"transport": "streamable_http"}) is True


def test_server_notification_stream_can_be_disabled_per_connection() -> None:
    assert (
        server_notification_stream_enabled(
            {"transport": "streamable_http", "server_notification_stream": False}
        )
        is False
    )


class ProtocolServer:
    """Exercise the actual SDK and adapter against HTTP protocol responses."""

    def __init__(self, identity: str) -> None:
        self.identity = identity
        self.requests: list[httpx.Request] = []
        self.clients: list[httpx.AsyncClient] = []
        self.get_seen = asyncio.Event()
        self.tool_started = asyncio.Event()
        self.hang_tool = False
        self.sse_tool = False

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.method == "GET":
            self.get_seen.set()
            return httpx.Response(405)
        if request.method == "DELETE":
            return httpx.Response(200)
        message = json.loads(request.content)
        method = message["method"]
        if "id" not in message:
            return httpx.Response(202)
        result: dict[str, Any]
        if method == "initialize":
            result = {
                "protocolVersion": message["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "protocol-fixture", "version": "1"},
            }
        elif method == "tools/list":
            result = {"tools": [{"name": "echo", "inputSchema": {"type": "object"}}]}
        elif method == "tools/call":
            self.tool_started.set()
            if self.hang_tool:
                await asyncio.Event().wait()
            result = {"content": [{"type": "text", "text": self.identity}]}
        else:
            raise AssertionError(f"Unexpected MCP method: {method}")
        if method == "tools/call" and self.sse_tool:
            event = json.dumps({"jsonrpc": "2.0", "id": message["id"], "result": result})
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                content=f"event: message\ndata: {event}\n\n",
            )
        return httpx.Response(
            200,
            headers={"mcp-session-id": self.identity},
            json={"jsonrpc": "2.0", "id": message["id"], "result": result},
        )

    def factory(self, **kwargs: Any) -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(self.handle), **kwargs)
        self.clients.append(client)
        return client

    def connection(self, enabled: bool | None = False) -> Any:
        connection = {
            "transport": "streamable_http",
            "url": "https://mcp.example.test/mcp",
            "httpx_client_factory": self.factory,
            "auth": httpx.BasicAuth(self.identity, "dummy-password"),
            "headers": {"x-scope": self.identity},
        }
        if enabled is not None:
            connection["server_notification_stream"] = enabled
        return connection


@pytest.mark.asyncio
@pytest.mark.parametrize("enabled", [False, True, None])
async def test_protocol_calls_auth_and_cleanup(enabled: bool | None) -> None:
    install_server_notification_stream_compatibility()
    server = ProtocolServer("session-a")
    async with sessions.create_session(server.connection(enabled)) as session:
        await session.initialize()
        tools = await session.list_tools()
        assert tools.tools[0].name == "echo"
        result = await session.call_tool("echo", {})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "session-a"
        if enabled is not False:
            await asyncio.wait_for(server.get_seen.wait(), timeout=2)
    assert server.get_seen.is_set() is (enabled is not False)
    assert server.requests[-1].method == "DELETE"
    assert server.requests[-1].headers["mcp-session-id"] == "session-a"
    assert all(request.headers["x-scope"] == "session-a" for request in server.requests)
    assert all(request.headers["authorization"].startswith("Basic ") for request in server.requests)
    assert all(client.is_closed for client in server.clients)


@pytest.mark.asyncio
async def test_concurrent_modes_keep_sessions_separate() -> None:
    install_server_notification_stream_compatibility()
    servers = [ProtocolServer("post-only"), ProtocolServer("standard")]

    async def call(server: ProtocolServer, enabled: bool) -> None:
        async with sessions.create_session(server.connection(enabled)) as session:
            await session.initialize()
            result = await session.call_tool("echo", {})
            assert isinstance(result.content[0], TextContent)
            assert result.content[0].text == server.identity

    await asyncio.gather(call(servers[0], False), call(servers[1], True))
    for server in servers:
        for request in server.requests:
            if "mcp-session-id" in request.headers:
                assert request.headers["mcp-session-id"] == server.identity
    assert not servers[0].get_seen.is_set()


@pytest.mark.asyncio
async def test_cancellation_closes_post_only_client() -> None:
    install_server_notification_stream_compatibility()
    server = ProtocolServer("cancelled")
    server.hang_tool = True

    async def call() -> None:
        async with sessions.create_session(server.connection()) as session:
            await session.initialize()
            await session.call_tool("echo", {})

    task = asyncio.create_task(call())
    await asyncio.wait_for(server.tool_started.wait(), timeout=2)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, timeout=2)
    assert all(client.is_closed for client in server.clients)
    assert not server.get_seen.is_set()


@pytest.mark.asyncio
async def test_post_response_sse_and_explicit_session_retention() -> None:
    install_server_notification_stream_compatibility()
    server = ProtocolServer("retained")
    server.sse_tool = True
    connection = server.connection()
    connection["terminate_on_close"] = False
    async with sessions.create_session(connection) as session:
        await session.initialize()
        result = await session.call_tool("echo", {})
        assert isinstance(result.content[0], TextContent)
        assert result.content[0].text == "retained"
    assert all(request.method == "POST" for request in server.requests)
    assert all(client.is_closed for client in server.clients)
