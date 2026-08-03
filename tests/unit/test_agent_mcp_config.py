from __future__ import annotations

from types import SimpleNamespace

import pytest

from server.app.agent.definition import AgentDefinition
from server.app.agent.mcp_client import (
    McpServerConfig,
    McpServerDiscoveryError,
    McpTransportAuthenticationError,
    create_mcp_client,
    load_mcp_tools_per_server,
    mcp_config_to_connection,
)
from server.app.settings import Settings


def test_agent_definition_carries_agent_owned_mcp_config() -> None:
    agent = AgentDefinition.model_validate(
        {
            "name": "tenant-agent",
            "system_prompt": "Use configured tools.",
            "mcp": {
                "servers": {
                    "github": {
                        "url": "https://mcp.example.test/github",
                        "transport": "streamable_http",
                        "required": True,
                        "auth": {
                            "type": "workload_token_exchange",
                            "profile": "agent-machine",
                        },
                    }
                }
            },
        }
    )

    server = agent.mcp.servers["github"]
    assert server.transport == "streamable_http"
    assert server.auth.type == "workload_token_exchange"
    assert server.auth.profile == "agent-machine"


@pytest.mark.parametrize(
    "auth",
    [
        {"type": "none"},
        {"type": "mcp_oauth"},
        {"type": "workload_token_exchange", "profile": "internal-egress"},
        {"type": "static_bearer", "env": "MCP_ACCESS_TOKEN"},
    ],
)
def test_agent_definition_accepts_each_standard_mcp_auth_type(auth) -> None:
    agent = AgentDefinition.model_validate(
        {
            "name": "auth-agent",
            "system_prompt": "Use configured tools.",
            "mcp": {
                "servers": {
                    "remote": {
                        "url": "https://mcp.example.test/tools",
                        "auth": auth,
                    }
                }
            },
        }
    )

    assert agent.mcp.servers["remote"].auth.model_dump(exclude_none=True) == auth


@pytest.mark.parametrize(
    "auth",
    [
        {"type": "oauth"},
        {"type": "outbound_provider", "auth_profile": "legacy"},
        {"type": "static_bearer", "env": "invalid-name"},
        {"type": "workload_token_exchange"},
        {"type": "none", "headers": {"Authorization": "Bearer secret"}},
    ],
)
def test_agent_mcp_config_rejects_legacy_or_unbounded_auth(auth) -> None:
    with pytest.raises(ValueError):
        AgentDefinition.model_validate(
            {
                "name": "bad-auth-agent",
                "system_prompt": "Do not accept transport secrets.",
                "mcp": {
                    "servers": {
                        "remote": {
                            "url": "https://mcp.example.test/tools",
                            "auth": auth,
                        }
                    }
                },
            }
        )


def test_agent_mcp_config_rejects_local_servers() -> None:
    with pytest.raises(ValueError, match="HTTP/HTTPS"):
        AgentDefinition.model_validate(
            {
                "name": "bad-agent",
                "system_prompt": "No local MCP.",
                "mcp": {"servers": {"local": {"url": "stdio://filesystem"}}},
            }
        )


@pytest.mark.parametrize(
    "server",
    [
        {"url": "https://user:password@mcp.example.test/tools"},
        {"url": "https://mcp.example.test/tools#fragment"},
        {"url": "https://mcp.example.test/tools", "headers": {"X-Token": "secret"}},
        {"url": "https://mcp.example.test/sse", "transport": "sse"},
        {"url": "https://mcp.example.test/tools", "enabled": False},
    ],
)
def test_agent_mcp_config_rejects_unbounded_server_transport_fields(server) -> None:
    with pytest.raises(ValueError):
        AgentDefinition.model_validate(
            {
                "name": "bad-server-agent",
                "system_prompt": "Use only bounded remote MCP.",
                "mcp": {"servers": {"remote": server}},
            }
        )


def test_workload_profile_resolves_from_deployment_configuration_only() -> None:
    agent = AgentDefinition.model_validate(
        {
            "name": "gateway-agent",
            "system_prompt": "Use the approved gateway.",
            "mcp": {
                "servers": {
                    "github": {
                        "url": "https://mcp-egress.internal/mcp/github",
                        "auth": {
                            "type": "workload_token_exchange",
                            "profile": "egress",
                        },
                    }
                }
            },
        }
    )
    settings = Settings.model_validate(
        {
            "mcp_auth_profiles": {
                "egress": {
                    "type": "oauth_token_exchange",
                    "token_endpoint": "https://identity.internal/token",
                    "subject_token_source": "workload_identity",
                    "audience": "canonical_server_uri",
                }
            }
        }
    )

    resolved = McpServerConfig.from_agent_config(
        "github",
        agent.mcp.servers["github"],
        settings,
        agent_name=agent.name,
        agent_revision=3,
        effective_scope={"tenant": "acme"},
    )

    assert resolved.workload_profile is not None
    assert resolved.workload_profile.token_endpoint == "https://identity.internal/token"
    assert "workload_profile" not in resolved.model_dump(mode="json")


def test_unknown_workload_profile_fails_resolution() -> None:
    agent = AgentDefinition.model_validate(
        {
            "name": "gateway-agent",
            "system_prompt": "Fail closed when deployment config is absent.",
            "mcp": {
                "servers": {
                    "github": {
                        "url": "https://mcp-egress.internal/mcp/github",
                        "auth": {
                            "type": "workload_token_exchange",
                            "profile": "missing",
                        },
                    }
                }
            },
        }
    )

    with pytest.raises(ValueError, match="Unknown MCP authentication profile"):
        McpServerConfig.from_agent_config(
            "github",
            agent.mcp.servers["github"],
            Settings(),
            agent_name=agent.name,
            agent_revision=1,
            effective_scope={"tenant": "acme"},
        )


def test_protected_auth_never_silently_builds_anonymous_transport() -> None:
    config = McpServerConfig.model_validate(
        {
            "name": "github",
            "url": "https://mcp.example.test/github",
            "auth": {"type": "mcp_oauth"},
        }
    )

    with pytest.raises(
        McpTransportAuthenticationError,
        match="oauth_configuration_unavailable",
    ):
        mcp_config_to_connection(config, Settings())


@pytest.mark.asyncio
async def test_optional_mcp_server_failure_preserves_healthy_tools(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, config: McpServerConfig) -> None:
            self._config = config

        async def get_tools(self, *, server_name: str | None = None):
            if self._config.name == "optional":
                raise RuntimeError("connection refused")
            return [SimpleNamespace(name=f"{server_name}__search")]

    def fake_create_client(configs, settings, callbacks=None, tool_interceptors=None):
        return FakeClient(list(configs)[0])

    monkeypatch.setattr("server.app.agent.mcp_client.create_mcp_client", fake_create_client)

    tools = await load_mcp_tools_per_server(
        [
            McpServerConfig(name="optional", url="https://optional.test/mcp", required=False),
            McpServerConfig(name="healthy", url="https://healthy.test/mcp", required=True),
        ],
        Settings(),
    )

    assert [tool.name for tool in tools] == ["healthy__search"]


@pytest.mark.asyncio
async def test_optional_auth_failure_preserves_healthy_server(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeClient:
        async def get_tools(self, *, server_name: str | None = None):
            return [SimpleNamespace(name=f"{server_name}__search")]

    def create_client(configs, settings, callbacks=None, tool_interceptors=None):
        config = list(configs)[0]
        if config.name == "optional-auth":
            return create_mcp_client(
                [config],
                settings,
                callbacks=callbacks,
                tool_interceptors=tool_interceptors,
            )
        return FakeClient()

    monkeypatch.setattr("server.app.agent.mcp_client.create_mcp_client", create_client)
    tools = await load_mcp_tools_per_server(
        [
            McpServerConfig.model_validate(
                {
                    "name": "optional-auth",
                    "url": "https://optional.test/mcp",
                    "required": False,
                    "auth": {"type": "static_bearer", "env": "MISSING_OPTIONAL_TOKEN"},
                }
            ),
            McpServerConfig(name="healthy", url="https://healthy.test/mcp"),
        ],
        Settings(),
    )

    assert [tool.name for tool in tools] == ["healthy__search"]


@pytest.mark.asyncio
async def test_required_mcp_server_failure_is_typed_and_redacted(monkeypatch) -> None:
    class FakeClient:
        async def get_tools(self, *, server_name: str | None = None):
            raise RuntimeError("token=secret-value")

    monkeypatch.setattr(
        "server.app.agent.mcp_client.create_mcp_client",
        lambda configs, settings, callbacks=None, tool_interceptors=None: FakeClient(),
    )

    with pytest.raises(McpServerDiscoveryError) as exc_info:
        await load_mcp_tools_per_server(
            [McpServerConfig(name="github", url="https://github.test/mcp", required=True)],
            Settings(),
        )

    assert exc_info.value.server_alias == "github"
    assert "secret-value" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_required_auth_failure_is_typed_and_redacted() -> None:
    with pytest.raises(McpServerDiscoveryError) as exc_info:
        await load_mcp_tools_per_server(
            [
                McpServerConfig.model_validate(
                    {
                        "name": "github",
                        "url": "https://github.test/mcp",
                        "required": True,
                        "auth": {"type": "mcp_oauth"},
                    }
                )
            ],
            Settings(),
        )

    assert exc_info.value.category == "oauth_configuration_unavailable"
    assert str(exc_info.value) == (
        "MCP server 'github' failed: oauth_configuration_unavailable"
    )


@pytest.mark.asyncio
async def test_duplicate_mcp_tool_identity_fails_discovery(monkeypatch) -> None:
    class FakeClient:
        async def get_tools(self, *, server_name: str | None = None):
            return [SimpleNamespace(name="duplicate_tool")]

    monkeypatch.setattr(
        "server.app.agent.mcp_client.create_mcp_client",
        lambda configs, settings, callbacks=None, tool_interceptors=None: FakeClient(),
    )

    with pytest.raises(McpServerDiscoveryError, match="duplicate_tool_identity"):
        await load_mcp_tools_per_server(
            [
                McpServerConfig(name="one", url="https://one.test/mcp"),
                McpServerConfig(name="two", url="https://two.test/mcp"),
            ],
            Settings(),
        )


def test_global_mcp_servers_route_is_not_registered() -> None:
    from server.app.main import app

    paths = {str(getattr(route, "path", "")) for route in app.routes}
    assert "/mcp-servers" not in paths
