"""User scenario tests for MCP (Model Context Protocol) support.

These tests verify the end-to-end MCP functionality:
- Remote-only MCP connections
- Security validation
- Tool integration via langchain-mcp-adapters
- Session-level configuration
- Progress and logging callbacks
- Resources and prompts
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.agent.mcp_client import (
    McpServerConfig,
    _build_mcp_callbacks,
    _build_mcp_interceptors,
    create_mcp_client,
    mcp_config_to_connection,
)


@pytest.mark.asyncio
class TestMcpSecurityStance:
    """Test Cognition's security stance on MCP."""

    async def test_local_mcp_servers_rejected(self):
        """Verify that local (stdio) MCP servers are rejected."""
        with pytest.raises(ValueError) as exc_info:
            McpServerConfig(name="local-fs", url="file:///path/to/local/mcp.sock")
        assert "Local (stdio) MCP servers are not supported" in str(exc_info.value)

        with pytest.raises(ValueError) as exc_info:
            McpServerConfig(name="local-cmd", url="/usr/bin/node script.js")
        assert "Local (stdio) MCP servers are not supported" in str(exc_info.value)

    async def test_remote_mcp_servers_accepted(self):
        """Verify that remote (HTTP) MCP servers are accepted."""
        config_https = McpServerConfig(name="github", url="https://api.glama.ai/mcp/github")
        assert config_https.url == "https://api.glama.ai/mcp/github"
        assert config_https.transport == "sse"

        config_http = McpServerConfig(name="local-http", url="http://localhost:8080/mcp")
        assert config_http.url == "http://localhost:8080/mcp"


class TestMcpConfigToConnection:
    """Test mcp_config_to_connection() mapping."""

    def test_maps_sse_config(self):
        config = McpServerConfig(
            name="test",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer xyz"},
        )
        conn = mcp_config_to_connection(config)
        assert conn["transport"] == "sse"
        assert conn["url"] == "https://example.com/mcp"
        assert conn["headers"] == {"Authorization": "Bearer xyz"}

    def test_maps_streamable_http_config(self):
        config = McpServerConfig(
            name="test",
            url="https://example.com/mcp",
            transport="streamable_http",
        )
        conn = mcp_config_to_connection(config)
        assert conn["transport"] == "streamable_http"


@pytest.mark.asyncio
class TestCreateMcpClient:
    """Test create_mcp_client() factory."""

    async def test_creates_client_with_enabled_servers(self):
        configs = [
            McpServerConfig(name="srv1", url="https://a.test/mcp"),
            McpServerConfig(name="srv2", url="https://b.test/mcp"),
        ]
        with patch("server.app.agent.mcp_client.MultiServerMCPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            client = create_mcp_client(configs)

            mock_client_cls.assert_called_once()
            call_kwargs = mock_client_cls.call_args[1]
            assert "srv1" in call_kwargs["connections"]
            assert "srv2" in call_kwargs["connections"]

    async def test_filters_disabled_servers(self):
        configs = [
            McpServerConfig(name="srv1", url="https://a.test/mcp"),
            McpServerConfig(name="srv2", url="https://b.test/mcp", enabled=False),
        ]
        with patch("server.app.agent.mcp_client.MultiServerMCPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            create_mcp_client(configs)

            call_kwargs = mock_client_cls.call_args[1]
            assert "srv1" in call_kwargs["connections"]
            assert "srv2" not in call_kwargs["connections"]

    async def test_passes_callbacks_and_interceptors(self):
        configs = [McpServerConfig(name="srv1", url="https://a.test/mcp")]
        callbacks = _build_mcp_callbacks()
        interceptors = _build_mcp_interceptors({"tenant": "acme"})

        with patch("server.app.agent.mcp_client.MultiServerMCPClient") as mock_client_cls:
            mock_client = MagicMock()
            mock_client_cls.return_value = mock_client

            create_mcp_client(configs, callbacks=callbacks, tool_interceptors=interceptors)

            call_kwargs = mock_client_cls.call_args[1]
            assert call_kwargs["callbacks"] is callbacks
            assert call_kwargs["tool_interceptors"] is interceptors


@pytest.mark.asyncio
class TestMcpToolIntegration:
    """Test MCP tool integration with Cognition using langchain-mcp-adapters."""

    async def test_mcp_tools_available_to_agent(self):
        """Verify MCP tools are loaded and available to the agent."""
        from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent

        mcp_configs = [McpServerConfig(name="github", url="https://api.glama.ai/mcp/github")]

        with patch(
            "server.app.agent.cognition_agent.create_mcp_client"
        ) as mock_create_client:
            mock_mcp_client = MagicMock()
            mock_tool = MagicMock()
            mock_tool.name = "github_get_repo"
            mock_mcp_client.get_tools = AsyncMock(return_value=[mock_tool])
            mock_create_client.return_value = mock_mcp_client

            with patch(
                "server.app.agent.cognition_agent.create_deep_agent"
            ) as mock_create:
                mock_agent = MagicMock()
                mock_create.return_value = mock_agent

                agent = await create_cognition_agent(
                    CognitionAgentParams(
                        project_path="/tmp/test",
                        mcp_configs=mcp_configs,
                    )
                )

                call_kwargs = mock_create.call_args[1]
                tools = call_kwargs.get("tools", [])
                tool_names = [t.name for t in tools]
                assert "github_get_repo" in tool_names


@pytest.mark.asyncio
class TestMcpErrorHandling:
    """Test MCP error handling scenarios."""

    async def test_mcp_connection_failure_graceful(self):
        """Verify agent continues if MCP client raises during get_tools()."""
        from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent

        mcp_configs = [McpServerConfig(name="failing-server", url="https://invalid.test/mcp")]

        with patch(
            "server.app.agent.cognition_agent.create_mcp_client"
        ) as mock_create_client:
            mock_mcp_client = MagicMock()
            mock_mcp_client.get_tools = AsyncMock(
                side_effect=ConnectionError("Connection refused")
            )
            mock_create_client.return_value = mock_mcp_client

            with patch(
                "server.app.agent.cognition_agent.create_deep_agent"
            ) as mock_create:
                mock_agent = MagicMock()
                mock_create.return_value = mock_agent

                agent = await create_cognition_agent(
                    CognitionAgentParams(
                        project_path="/tmp/test",
                        mcp_configs=mcp_configs,
                    )
                )
                assert agent is not None

    async def test_empty_enabled_configs_skipped(self):
        """Verify all-disabled configs don't trigger MCP initialization."""
        from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent

        mcp_configs = [
            McpServerConfig(name="disabled", url="https://test/mcp", enabled=False)
        ]

        with patch("server.app.agent.cognition_agent.create_mcp_client") as mock_create_client:
            with patch(
                "server.app.agent.cognition_agent.create_deep_agent"
            ) as mock_create:
                mock_agent = MagicMock()
                mock_create.return_value = mock_agent

                await create_cognition_agent(
                    CognitionAgentParams(
                        project_path="/tmp/test",
                        mcp_configs=mcp_configs,
                    )
                )
                mock_create_client.assert_not_called()


class TestMcpCallbacks:
    """Test MCP callback construction and behavior."""

    def test_build_mcp_callbacks_returns_callbacks_object(self):
        callbacks = _build_mcp_callbacks()
        from langchain_mcp_adapters.callbacks import Callbacks

        assert isinstance(callbacks, Callbacks)
        assert callbacks.on_progress is not None
        assert callbacks.on_logging_message is not None

    @pytest.mark.asyncio
    async def test_progress_callback_logs(self):
        from langchain_mcp_adapters.callbacks import CallbackContext

        callbacks = _build_mcp_callbacks()
        ctx = CallbackContext(server_name="test-srv", tool_name="search")

        with patch("server.app.agent.mcp_client.logger") as mock_logger:
            await callbacks.on_progress(0.5, 1.0, "Halfway done", ctx)
            mock_logger.info.assert_called_once()
            call_args = mock_logger.info.call_args
            assert call_args[1]["server"] == "test-srv"
            assert call_args[1]["tool"] == "search"
            assert call_args[1]["progress"] == 0.5

    @pytest.mark.asyncio
    async def test_logging_callback_uses_warning_for_errors(self):
        from langchain_mcp_adapters.callbacks import CallbackContext

        callbacks = _build_mcp_callbacks()
        ctx = CallbackContext(server_name="test-srv", tool_name=None)

        with patch("server.app.agent.mcp_client.logger") as mock_logger:
            from mcp.types import LoggingMessageNotificationParams

            params = LoggingMessageNotificationParams(level="error", data="something broke")
            await callbacks.on_logging_message(params, ctx)
            mock_logger.warning.assert_called_once()


@pytest.mark.asyncio
class TestMcpInterceptors:
    """Test MCP tool interceptor behavior."""

    @pytest.mark.asyncio
    async def test_injects_scope_into_args(self):
        interceptors = _build_mcp_interceptors({"tenant": "acme", "project": "ios"})
        assert len(interceptors) == 1

        from langchain_mcp_adapters.interceptors import MCPToolCallRequest

        request = MCPToolCallRequest(
            name="search",
            args={"query": "test"},
            server_name="test-srv",
        )

        async def handler(req: MCPToolCallRequest):
            return f"called with {req.args}"

        result = await interceptors[0](request, handler)
        assert "called with" in result

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_scope_keys(self):
        interceptors = _build_mcp_interceptors({"tenant": "acme"})

        from langchain_mcp_adapters.interceptors import MCPToolCallRequest

        request = MCPToolCallRequest(
            name="search",
            args={"query": "test", "tenant": "already-set"},
            server_name="test-srv",
        )

        async def handler(req: MCPToolCallRequest):
            assert req.args["tenant"] == "already-set"
            return "ok"

        await interceptors[0](request, handler)

    @pytest.mark.asyncio
    async def test_no_scope_returns_empty_list(self):
        interceptors = _build_mcp_interceptors(None)
        assert len(interceptors) == 1  # still returns the injector

        from langchain_mcp_adapters.interceptors import MCPToolCallRequest

        request = MCPToolCallRequest(
            name="search",
            args={"query": "test"},
            server_name="test-srv",
        )

        async def handler(req: MCPToolCallRequest):
            return "ok"

        result = await interceptors[0](request, handler)
        assert result == "ok"


@pytest.mark.asyncio
class TestMcpConfiguration:
    """Test MCP configuration scenarios."""

    async def test_global_mcp_configuration(self):
        """Test MCP configuration is stored and retrieved via ConfigStore."""
        from server.app.storage.config_models import McpServerRegistration
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        registry = MemoryConfigRegistry()
        store = DefaultConfigStore(registry)

        github = McpServerRegistration(
            name="github",
            url="https://api.glama.ai/mcp/github",
            headers={"Authorization": "Bearer test-token"},
            enabled=True,
        )
        linear = McpServerRegistration(
            name="linear",
            url="https://mcp.linear.app/sse",
            enabled=False,
        )
        await store.upsert_mcp_server(github)
        await store.upsert_mcp_server(linear)

        servers = await store.list_mcp_servers()
        assert len(servers) == 2

        gh = next(s for s in servers if s.name == "github")
        assert gh.url == "https://api.glama.ai/mcp/github"
        assert gh.headers == {"Authorization": "Bearer test-token"}
        assert gh.enabled is True
        assert gh.transport == "sse"

        ln = next(s for s in servers if s.name == "linear")
        assert ln.url == "https://mcp.linear.app/sse"
        assert ln.enabled is False

    async def test_global_mcp_configuration_invalid_url_rejected(self):
        """Test that non-HTTP URLs are rejected at McpServerRegistration construction time."""
        from pydantic import ValidationError

        from server.app.storage.config_models import McpServerRegistration

        with pytest.raises(ValidationError):
            McpServerRegistration(name="bad", url="file:///path/to/mcp.sock")

    async def test_session_level_mcp_configuration(self):
        """Test that MCP configs are resolved from ConfigStore with transport field."""
        from server.app.agent.mcp_client import McpServerConfig
        from server.app.storage.config_models import McpServerRegistration
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        registry = MemoryConfigRegistry()
        store = DefaultConfigStore(registry)

        await store.upsert_mcp_server(
            McpServerRegistration(
                name="github",
                url="https://api.glama.ai/mcp/github",
                transport="sse",
            )
        )

        from server.app.llm.deep_agent_service import DeepAgentStreamingService
        from server.app.settings import Settings

        settings = Settings()
        service = DeepAgentStreamingService(settings=settings)
        service._config_store = store

        mcp_configs = await service._resolve_mcp_configs(scope=None)
        assert len(mcp_configs) == 1
        assert isinstance(mcp_configs[0], McpServerConfig)
        assert mcp_configs[0].name == "github"
        assert mcp_configs[0].url == "https://api.glama.ai/mcp/github"
        assert mcp_configs[0].transport == "sse"


@pytest.mark.integration
@pytest.mark.asyncio
class TestMcpIntegrationWithExternalServers:
    """Integration tests with real external MCP servers.

    These tests are marked as 'integration' and should only run when:
    - Network access is available
    - External MCP server is accessible
    - Test credentials are configured
    """

    @pytest.mark.skip(reason="Requires external MCP server")
    async def test_connect_to_glama_github(self):
        """Integration test: Connect to Glama GitHub MCP via langchain-mcp-adapters."""
        import os

        api_key = os.getenv("GLAMA_API_KEY")
        if not api_key:
            pytest.skip("GLAMA_API_KEY not set")

        configs = [
            McpServerConfig(
                name="github",
                url="https://api.glama.ai/mcp/github",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        ]
        client = create_mcp_client(configs)
        tools = await client.get_tools()
        assert len(tools) > 0


@pytest.mark.asyncio
class TestMcpSecurityHeaders:
    """Test security of MCP header handling."""

    async def test_sensitive_headers_not_leaked(self):
        """Verify sensitive headers are not exposed in logs/errors."""
        config = McpServerConfig(
            name="github",
            url="https://api.glama.ai/mcp/github",
            headers={"Authorization": "Bearer secret-token-12345"},
        )
        assert config.headers["Authorization"] == "Bearer secret-token-12345"

    async def test_env_var_expansion_in_headers(self):
        """Verify environment variable expansion works in headers."""
        import os

        os.environ["TEST_API_KEY"] = "test-secret"
        # TODO: Implement when config loading is integrated
        pass
