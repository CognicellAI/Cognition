"""Tests for the v0.14 per-agent MCP configuration contract."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from server.app.agent.definition import AgentDefinition
from server.app.agent.mcp_client import McpServerUnavailableError, load_agent_mcp_tools
from server.app.agent.mcp_config import AgentMcpServer, McpAuthConfig, canonical_mcp_tool_identity


def test_agent_owns_a_streamable_http_mcp_server() -> None:
    agent = AgentDefinition(
        name="release-agent",
        system_prompt="Use configured tools.",
        mcp_servers=[
            AgentMcpServer(
                alias="github",
                url="https://mcp.example.test/github",
                auth=McpAuthConfig(type="mcp_oauth"),
            )
        ],
    )

    assert agent.mcp_servers[0].alias == "github"
    assert agent.mcp_servers[0].required
    assert canonical_mcp_tool_identity("github", "search_issues") == ("github", "search_issues")


def test_workload_exchange_uses_an_opaque_profile_not_a_credential() -> None:
    config = McpAuthConfig(type="workload_token_exchange", profile="runtime-gateway")
    assert config.profile == "runtime-gateway"

    with pytest.raises(ValidationError, match="requires an opaque auth profile"):
        McpAuthConfig(type="workload_token_exchange")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"alias": "bad.alias", "url": "https://mcp.example.test"},
        {"alias": "github", "url": "file:///tmp/mcp"},
        {"alias": "github", "url": "https://token@example.test/mcp"},
    ],
)
def test_agent_mcp_rejects_untrusted_transport_configuration(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        AgentMcpServer(**kwargs)


def test_static_bearer_never_accepts_a_raw_token() -> None:
    config = McpAuthConfig(type="static_bearer", env="LOCAL_MCP_TOKEN")
    assert config.env == "LOCAL_MCP_TOKEN"

    with pytest.raises(ValidationError):
        McpAuthConfig(type="static_bearer", env="Bearer secret")


@pytest.mark.asyncio
async def test_optional_server_failure_preserves_healthy_tools() -> None:
    healthy = AgentMcpServer(alias="healthy", url="https://healthy.example.test")
    optional = AgentMcpServer(alias="optional", url="https://optional.example.test", required=False)
    healthy_client = MagicMock()
    healthy_client.get_tools = AsyncMock(return_value=[MagicMock(name="search")])

    with patch(
        "server.app.agent.mcp_client.create_agent_mcp_client",
        side_effect=[healthy_client, McpServerUnavailableError("optional", "discovery_failed")],
    ):
        tools = await load_agent_mcp_tools([healthy, optional])

    assert len(tools) == 1


@pytest.mark.asyncio
async def test_required_server_failure_stops_before_model_execution() -> None:
    required = AgentMcpServer(alias="required", url="https://required.example.test")

    with (
        patch(
            "server.app.agent.mcp_client.create_agent_mcp_client",
            side_effect=McpServerUnavailableError("required", "discovery_failed"),
        ),
        pytest.raises(McpServerUnavailableError, match="required"),
    ):
        await load_agent_mcp_tools([required])
