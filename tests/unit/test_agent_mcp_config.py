from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.app.agent.definition import AgentDefinition
from server.app.agent.mcp_client import (
    McpServerConfig,
    McpServerDiscoveryError,
    load_mcp_tools_per_server,
)


def test_agent_definition_carries_agent_owned_mcp_config() -> None:
    agent = AgentDefinition.model_validate(
        {
            "name": "tenant-agent",
            "system_prompt": "Use configured tools.",
            "mcp": {
            "servers": {
                "github": {
                    "url": "https://mcp.example.test/github",
                    "transport": "http",
                    "required": True,
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

    server = agent.mcp.servers["github"]
    assert server.transport == "streamable_http"
    assert server.auth.type == "outbound_provider"
    assert server.auth.header_allowlist == ["authorization"]


def test_agent_mcp_config_rejects_local_servers() -> None:
    with pytest.raises(ValueError, match="HTTP/HTTPS"):
        AgentDefinition.model_validate(
            {
                "name": "bad-agent",
                "system_prompt": "No local MCP.",
                "mcp": {"servers": {"local": {"url": "stdio://filesystem"}}},
            }
        )


@pytest.mark.asyncio
async def test_optional_mcp_server_failure_preserves_healthy_tools(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, config: McpServerConfig) -> None:
            self._config = config

        async def get_tools(self, *, server_name: str | None = None):
            if self._config.name == "optional":
                raise RuntimeError("connection refused")
            return [SimpleNamespace(name=f"{server_name}__search")]

    def fake_create_client(configs, callbacks=None, tool_interceptors=None):
        return FakeClient(list(configs)[0])

    monkeypatch.setattr("server.app.agent.mcp_client.create_mcp_client", fake_create_client)

    tools = await load_mcp_tools_per_server(
        [
            McpServerConfig(name="optional", url="https://optional.test/mcp", required=False),
            McpServerConfig(name="healthy", url="https://healthy.test/mcp", required=True),
        ]
    )

    assert [tool.name for tool in tools] == ["healthy__search"]


@pytest.mark.asyncio
async def test_required_mcp_server_failure_is_typed_and_redacted(monkeypatch) -> None:
    class FakeClient:
        async def get_tools(self, *, server_name: str | None = None):
            raise RuntimeError("token=secret-value")

    monkeypatch.setattr(
        "server.app.agent.mcp_client.create_mcp_client",
        lambda configs, callbacks=None, tool_interceptors=None: FakeClient(),
    )

    with pytest.raises(McpServerDiscoveryError) as exc_info:
        await load_mcp_tools_per_server(
            [McpServerConfig(name="github", url="https://github.test/mcp", required=True)]
        )

    assert exc_info.value.server_alias == "github"
    assert "secret-value" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_duplicate_mcp_tool_identity_fails_discovery(monkeypatch) -> None:
    class FakeClient:
        async def get_tools(self, *, server_name: str | None = None):
            return [SimpleNamespace(name="duplicate_tool")]

    monkeypatch.setattr(
        "server.app.agent.mcp_client.create_mcp_client",
        lambda configs, callbacks=None, tool_interceptors=None: FakeClient(),
    )

    with pytest.raises(McpServerDiscoveryError, match="duplicate_tool_identity"):
        await load_mcp_tools_per_server(
            [
                McpServerConfig(name="one", url="https://one.test/mcp"),
                McpServerConfig(name="two", url="https://two.test/mcp"),
            ]
        )


def test_global_mcp_servers_route_is_not_registered() -> None:
    from server.app.main import app

    paths = {str(getattr(route, "path", "")) for route in app.routes}
    assert "/mcp-servers" not in paths
