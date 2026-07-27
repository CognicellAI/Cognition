"""Docker Compose e2e scenarios for remote MCP server configuration.

These tests target the running Cognition API (usually docker-compose at
``COGNITION_E2E_URL=http://localhost:8000``) and the public CoinGecko MCP
server at ``https://mcp.api.coingecko.com/sse``.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest

from tests.e2e.test_scenarios.conftest import (
    ScenarioTestClient,
    is_terminal_stream_event,
    stream_completed,
)

COINGECKO_MCP_URL = "https://mcp.api.coingecko.com/sse"


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _strict_mcp_tool_choice_enabled() -> bool:
    return os.getenv("COGNITION_STRICT_MCP_TOOL_CHOICE_E2E", "").lower() in {
        "1",
        "true",
        "yes",
    }


async def _collect_events(
    api_client: ScenarioTestClient,
    session_id: str,
    content: str,
    timeout: float = 90.0,
    max_events: int = 250,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event_type: str | None = None

    async with api_client.client.stream(
        "POST",
        f"{api_client.base_url}/sessions/{session_id}/messages",
        json={"content": content},
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            **api_client.scope_header,
        },
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event_type = line[7:].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if current_event_type:
                    payload["event"] = current_event_type
                events.append(payload)
                if is_terminal_stream_event(payload):
                    break
                current_event_type = None
                if len(events) >= max_events:
                    break
    return events


async def _register_coingecko(api_client: ScenarioTestClient, name: str) -> None:
    response = await api_client.post(
        "/mcp-servers",
        json={
            "name": name,
            "url": COINGECKO_MCP_URL,
            "enabled": True,
            "transport": "sse",
        },
    )
    assert response.status_code == 201, response.text


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestCoinGeckoMcpScenario:
    async def test_builder_can_configure_remote_mcp_server(
        self, api_client: ScenarioTestClient
    ) -> None:
        server_name = _unique("coingecko")
        try:
            await _register_coingecko(api_client, server_name)

            list_response = await api_client.get("/mcp-servers")
            assert list_response.status_code == 200, list_response.text
            servers = list_response.json()["servers"]
            configured = next(server for server in servers if server["name"] == server_name)
            assert configured["url"] == COINGECKO_MCP_URL
            assert configured["enabled"] is True
            assert configured["transport"] == "sse"

            get_response = await api_client.get(f"/mcp-servers/{server_name}")
            assert get_response.status_code == 200, get_response.text
            assert get_response.json()["name"] == server_name
        finally:
            await api_client.delete(f"/mcp-servers/{server_name}")

    async def test_agent_can_call_coingecko_mcp_tool(
        self, api_client: ScenarioTestClient
    ) -> None:
        server_name = "coingecko"
        session_id = await api_client.create_session(_unique("coingecko-mcp"))
        try:
            await api_client.delete(f"/mcp-servers/{server_name}")
            await _register_coingecko(api_client, server_name)

            events = await _collect_events(
                api_client,
                session_id,
                "Use the coingecko_search_docs MCP tool to search for the CoinGecko "
                "simple price endpoint, then answer with one sentence confirming the "
                "tool was used. You must call coingecko_search_docs before answering.",
            )

            assert stream_completed(events), [event.get("event") for event in events]
            tool_calls = [event for event in events if event.get("event") == "tool_call"]
            tool_names = [event.get("name") for event in tool_calls]
            if not tool_names and not _strict_mcp_tool_choice_enabled():
                pytest.skip("Configured E2E model did not choose the CoinGecko MCP tool")
            assert "coingecko_search_docs" in tool_names or "coingecko_execute" in tool_names, (
                f"Expected a prefixed CoinGecko MCP tool call, got {tool_names}. "
                f"Events: {[event.get('event') for event in events]}"
            )
        finally:
            await api_client.delete(f"/sessions/{session_id}")
            await api_client.delete(f"/mcp-servers/{server_name}")
