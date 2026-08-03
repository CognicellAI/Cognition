"""v0.14 Agent-owned MCP configuration scenarios."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.app.agent.definition import AgentDefinition
from server.app.agent.mcp_client import (
    McpServerConfig,
    McpServerDiscoveryError,
    load_mcp_tools_per_server,
)


def test_agent_owned_mcp_config_round_trips_through_definition() -> None:
    definition = AgentDefinition.model_validate(
        {
            "name": "mcp-agent",
            "system_prompt": "Use MCP tools when configured.",
            "mcp": {
                "servers": {
                    "github": {
                        "url": "https://mcp.example.test/github",
                        "required": True,
                        "transport": "http",
                        "auth": {
                            "type": "outbound_provider",
                            "auth_profile": "agent-machine",
                            "header_allowlist": ["Authorization"],
                        },
                    }
                }
            },
        }
    )

    dumped = definition.model_dump(mode="json")
    assert dumped["mcp"]["servers"]["github"]["transport"] == "streamable_http"
    assert dumped["mcp"]["servers"]["github"]["auth"]["auth_profile"] == "agent-machine"
    assert "headers" not in dumped["mcp"]["servers"]["github"]["auth"]


def test_agent_owned_mcp_rejects_stdio_server() -> None:
    with pytest.raises(ValueError, match="HTTP/HTTPS"):
        AgentDefinition.model_validate(
            {
                "name": "bad-agent",
                "system_prompt": "No local MCP.",
                "mcp": {"servers": {"filesystem": {"url": "stdio://filesystem"}}},
            }
        )


@pytest.mark.asyncio
async def test_per_server_discovery_keeps_optional_failure_isolated(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, config: McpServerConfig) -> None:
            self._config = config

        async def get_tools(self, *, server_name: str | None = None):
            if self._config.name == "optional":
                raise RuntimeError("optional server offline")
            return [SimpleNamespace(name=f"{server_name}__lookup")]

    monkeypatch.setattr(
        "server.app.agent.mcp_client.create_mcp_client",
        lambda configs, callbacks=None, tool_interceptors=None: FakeClient(list(configs)[0]),
    )

    tools = await load_mcp_tools_per_server(
        [
            McpServerConfig(name="optional", url="https://optional.test/mcp", required=False),
            McpServerConfig(name="required", url="https://required.test/mcp", required=True),
        ]
    )

    assert [tool.name for tool in tools] == ["required__lookup"]


@pytest.mark.asyncio
async def test_required_discovery_failure_is_redacted(monkeypatch) -> None:
    class FakeClient:
        async def get_tools(self, *, server_name: str | None = None):
            raise RuntimeError("Authorization: secret-token")

    monkeypatch.setattr(
        "server.app.agent.mcp_client.create_mcp_client",
        lambda configs, callbacks=None, tool_interceptors=None: FakeClient(),
    )

    with pytest.raises(McpServerDiscoveryError) as exc_info:
        await load_mcp_tools_per_server(
            [McpServerConfig(name="github", url="https://github.test/mcp", required=True)]
        )

    assert exc_info.value.category == "discovery_failed"
    assert "secret-token" not in str(exc_info.value)
