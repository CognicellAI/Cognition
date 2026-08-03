"""Tests for the v0.14 per-agent MCP configuration contract."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from server.app.agent.definition import AgentDefinition
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


def test_outbound_auth_provider_uses_an_opaque_profile_not_a_credential() -> None:
    config = McpAuthConfig(type="outbound_auth_provider", profile="runtime-gateway")
    assert config.profile == "runtime-gateway"

    with pytest.raises(ValidationError, match="requires an opaque auth profile"):
        McpAuthConfig(type="outbound_auth_provider")


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


def test_agent_rejects_duplicate_mcp_aliases() -> None:
    with pytest.raises(ValidationError, match="aliases must be unique"):
        AgentDefinition(
            name="release-agent",
            system_prompt="Use configured tools.",
            mcp_servers=[
                AgentMcpServer(alias="github", url="https://one.example.test/mcp"),
                AgentMcpServer(alias="github", url="https://two.example.test/mcp"),
            ],
        )
