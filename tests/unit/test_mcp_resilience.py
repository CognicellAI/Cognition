from __future__ import annotations

from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_mcp_adapters.tools import convert_mcp_tool_to_langchain_tool
from mcp.types import Tool

from server.app.agent.mcp_client import (
    McpServerConfig,
    create_mcp_client,
    load_mcp_tools_per_server,
)
from server.app.settings import Settings


def transport_failure():
    request = httpx.Request("POST", "https://tools.example.test/mcp?secret=hidden")
    return ExceptionGroup(
        "unhandled errors in a TaskGroup",
        [
            httpx.HTTPStatusError(
                "provider-secret", request=request, response=httpx.Response(503, request=request)
            )
        ],
    )


async def test_transport_failure_is_a_redacted_tool_result_without_replay(monkeypatch):
    calls = 0

    @asynccontextmanager
    async def failed_session(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise transport_failure()
        yield  # pragma: no cover

    monkeypatch.setattr("langchain_mcp_adapters.tools.create_session", failed_session)
    client = create_mcp_client([], Settings())
    tool = convert_mcp_tool_to_langchain_tool(
        None,
        Tool(name="write", inputSchema={"type": "object", "properties": {}}),
        connection={"transport": "streamable_http", "url": "https://tools.example.test/mcp"},
        server_name="remote",
        tool_interceptors=client.tool_interceptors,
    )
    result = await tool.ainvoke({})
    assert "MCP transport unavailable" in str(result)
    assert "provider-secret" not in str(result)
    assert "hidden" not in str(result)
    assert calls == 1


async def test_discovery_retries_transient_initialization_once(monkeypatch):
    settings = Settings(
        mcp_outbound_transport_enabled=True, mcp_allowed_origins=["https://tools.example.test"]
    )
    client = AsyncMock()
    client.get_tools.side_effect = [transport_failure(), []]
    monkeypatch.setattr("server.app.agent.mcp_client.create_mcp_client", lambda *a, **kw: client)
    tools = await load_mcp_tools_per_server(
        [McpServerConfig(name="remote", url="https://tools.example.test/mcp")], settings
    )
    assert tools == []
    assert client.get_tools.await_count == 2


@pytest.mark.parametrize("status", [401, 403, 400])
async def test_discovery_does_not_retry_authorization_or_protocol_errors(monkeypatch, status):
    settings = Settings(
        mcp_outbound_transport_enabled=True, mcp_allowed_origins=["https://tools.example.test"]
    )
    request = httpx.Request("POST", "https://tools.example.test/mcp")
    client = AsyncMock()
    client.get_tools.side_effect = httpx.HTTPStatusError(
        "sensitive", request=request, response=httpx.Response(status, request=request)
    )
    monkeypatch.setattr("server.app.agent.mcp_client.create_mcp_client", lambda *a, **kw: client)
    from server.app.agent.mcp_client import McpServerDiscoveryError

    with pytest.raises(McpServerDiscoveryError):
        await load_mcp_tools_per_server(
            [McpServerConfig(name="remote", url="https://tools.example.test/mcp")], settings
        )
    assert client.get_tools.await_count == 1


async def test_discovery_exhausts_one_retry_and_keeps_required_failure(monkeypatch):
    settings = Settings(
        mcp_outbound_transport_enabled=True, mcp_allowed_origins=["https://tools.example.test"]
    )
    client = AsyncMock()
    client.get_tools.side_effect = transport_failure()
    monkeypatch.setattr("server.app.agent.mcp_client.create_mcp_client", lambda *a, **kw: client)
    from server.app.agent.mcp_client import McpServerDiscoveryError

    with pytest.raises(McpServerDiscoveryError):
        await load_mcp_tools_per_server(
            [McpServerConfig(name="remote", url="https://tools.example.test/mcp")], settings
        )
    assert client.get_tools.await_count == 2


def test_mixed_exception_group_and_cancellation_are_not_recoverable():
    import asyncio

    from server.app.agent.mcp_resilience import is_transient_transport_failure

    assert not is_transient_transport_failure(
        ExceptionGroup("mixed", [transport_failure(), ValueError("programmer error")])
    )
    assert not is_transient_transport_failure(asyncio.CancelledError())
