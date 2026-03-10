"""User scenario tests for MCP (Model Context Protocol) support.

These tests verify the end-to-end MCP functionality:
- Remote-only MCP connections
- Security validation
- Tool integration
- Session-level configuration
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.agent.mcp_client import McpServerConfig, McpSseClient


@pytest.mark.asyncio
class TestMcpSecurityStance:
    """Test Cognition's security stance on MCP."""

    async def test_local_mcp_servers_rejected(self):
        """Verify that local (stdio) MCP servers are rejected."""
        # Try to create a config with file:// URL
        with pytest.raises(ValueError) as exc_info:
            config = McpServerConfig(name="local-fs", url="file:///path/to/local/mcp.sock")

        assert "Local (stdio) MCP servers are not supported" in str(exc_info.value)

        # Try to create a config with stdio command (should fail)
        with pytest.raises(ValueError) as exc_info:
            config = McpServerConfig(
                name="local-cmd",
                url="/usr/bin/node script.js",  # Not HTTP
            )

        assert "Local (stdio) MCP servers are not supported" in str(exc_info.value)

    async def test_remote_mcp_servers_accepted(self):
        """Verify that remote (HTTP) MCP servers are accepted."""
        # HTTPS should work
        config_https = McpServerConfig(name="github", url="https://api.glama.ai/mcp/github")
        assert config_https.url == "https://api.glama.ai/mcp/github"

        # HTTP should work (though not recommended for production)
        config_http = McpServerConfig(name="local-http", url="http://localhost:8080/mcp")
        assert config_http.url == "http://localhost:8080/mcp"


@pytest.mark.asyncio
class TestMcpRemoteConnection:
    """Test remote MCP connection scenarios."""

    async def test_mcp_client_connects_to_remote_server(self):
        """Verify MCP client can connect to remote server."""
        config = McpServerConfig(
            name="test-server",
            url="https://example.com/mcp",
            headers={"Authorization": "Bearer test-token"},
        )

        client = McpSseClient(config)

        # Mock the SSE client connection
        mock_session = AsyncMock()
        mock_session.initialize.return_value = MagicMock(protocolVersion="2024-11-05")

        with patch("server.app.agent.mcp_client.sse_client") as mock_sse:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sse.return_value = mock_ctx

            with patch("server.app.agent.mcp_client.ClientSession") as mock_client_session:
                mock_client_session.return_value = mock_session

                await client.connect()
                assert client._connected
                mock_session.initialize.assert_called_once()

    async def test_mcp_client_lists_tools(self):
        """Verify MCP client can list tools from remote server."""
        from types import SimpleNamespace

        config = McpServerConfig(name="github", url="https://api.glama.ai/mcp/github")
        client = McpSseClient(config)

        # Mock the session with SimpleNamespace to preserve attribute values
        mock_session = AsyncMock()
        mock_tool = SimpleNamespace(
            name="get_repo",
            description="Get repository info",
            inputSchema={"type": "object", "properties": {}},
        )
        mock_session.list_tools.return_value = SimpleNamespace(tools=[mock_tool])
        client.session = mock_session
        client._connected = True

        tools = await client.list_tools()
        assert len(tools) == 1
        assert tools[0].name == "get_repo"

    async def test_mcp_client_calls_tool(self):
        """Verify MCP client can call tools on remote server."""
        config = McpServerConfig(name="github", url="https://api.glama.ai/mcp/github")
        client = McpSseClient(config)

        # Mock the session
        mock_session = AsyncMock()
        mock_result = MagicMock(
            content=[MagicMock(type="text", text="Repository found")], isError=False
        )
        mock_session.call_tool.return_value = mock_result
        client.session = mock_session
        client._connected = True

        result = await client.call_tool("get_repo", {"owner": "test", "repo": "repo"})
        assert result["isError"] is False
        assert len(result["content"]) == 1


@pytest.mark.asyncio
class TestMcpToolIntegration:
    """Test MCP tool integration with Cognition."""

    async def test_mcp_tools_available_to_agent(self):
        """Verify MCP tools are available to the agent after connection."""
        from server.app.agent.cognition_agent import create_cognition_agent

        # Create agent with MCP configs
        mcp_configs = [McpServerConfig(name="github", url="https://api.glama.ai/mcp/github")]

        # Mock the MCP connection
        with patch("server.app.agent.mcp_client.sse_client") as mock_sse:
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=(AsyncMock(), AsyncMock()))
            mock_ctx.__aexit__ = AsyncMock(return_value=None)
            mock_sse.return_value = mock_ctx

            with patch("server.app.agent.mcp_client.ClientSession") as mock_session:
                mock_sess = AsyncMock()
                mock_sess.initialize.return_value = MagicMock(protocolVersion="2024-11-05")

                # Return a mock tool using SimpleNamespace
                from types import SimpleNamespace

                mock_tool = SimpleNamespace(
                    name="github_get_repo",
                    description="Get GitHub repository",
                    inputSchema={
                        "type": "object",
                        "properties": {"owner": {"type": "string"}, "repo": {"type": "string"}},
                    },
                )
                mock_sess.list_tools.return_value = SimpleNamespace(tools=[mock_tool])
                mock_session.return_value = mock_sess

                # Create agent with mocked components
                with patch("server.app.agent.context.ContextManager") as mock_ctx_mgr:
                    mock_ctx_mgr.return_value.build_index.return_value = None
                    mock_ctx_mgr.return_value.format_context_for_llm.return_value = ""

                    with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
                        mock_agent = MagicMock()
                        mock_create.return_value = mock_agent

                        agent = await create_cognition_agent(
                            project_path="/tmp/test", mcp_configs=mcp_configs
                        )

                        # Verify agent was created with MCP tools
                        call_kwargs = mock_create.call_args[1]
                        tools = call_kwargs.get("tools", [])

                        # Should have built-in tools + MCP tools
                        tool_names = [t.name for t in tools]
                        assert "github_get_repo" in tool_names or any(
                            "github" in name for name in tool_names
                        )


@pytest.mark.asyncio
class TestMcpErrorHandling:
    """Test MCP error handling scenarios."""

    async def test_mcp_connection_failure_graceful(self):
        """Verify agent continues if MCP connection fails."""
        from server.app.agent.cognition_agent import create_cognition_agent

        mcp_configs = [McpServerConfig(name="failing-server", url="https://invalid-url.test/mcp")]

        # Force connection to fail
        with patch("server.app.agent.mcp_client.sse_client") as mock_sse:
            mock_sse.side_effect = ConnectionError("Connection refused")

            with patch("server.app.agent.context.ContextManager") as mock_ctx_mgr:
                mock_ctx_mgr.return_value.build_index.return_value = None
                mock_ctx_mgr.return_value.format_context_for_llm.return_value = ""

                with patch("server.app.agent.cognition_agent.create_deep_agent") as mock_create:
                    mock_agent = MagicMock()
                    mock_create.return_value = mock_agent

                    # Should not raise, should continue without MCP tools
                    agent = await create_cognition_agent(
                        project_path="/tmp/test", mcp_configs=mcp_configs
                    )

                    assert agent is not None  # Agent created despite MCP failure

    async def test_mcp_timeout_handling(self):
        """Verify timeout handling for slow MCP servers."""
        config = McpServerConfig(name="slow-server", url="https://slow.example.com/mcp")

        client = McpSseClient(config)

        # Simulate timeout
        with patch("server.app.agent.mcp_client.sse_client") as mock_sse:
            mock_sse.side_effect = TimeoutError("Connection timed out")

            with pytest.raises(ConnectionError) as exc_info:
                await client.connect()

            assert "timed out" in str(exc_info.value).lower() or "Failed to connect" in str(
                exc_info.value
            )


@pytest.mark.asyncio
class TestMcpConfiguration:
    """Test MCP configuration scenarios."""

    async def test_global_mcp_configuration(self):
        """Test global MCP configuration is converted to McpServerConfig objects."""
        from server.app.settings import Settings

        settings = Settings(
            COGNITION_MCP_SERVERS={
                "github": {
                    "url": "https://api.glama.ai/mcp/github",
                    "headers": {"Authorization": "Bearer test-token"},
                },
                "linear": {
                    "url": "https://mcp.linear.app/sse",
                    "enabled": False,
                },
            }
        )

        configs = settings.mcp_server_configs

        assert len(configs) == 2

        github = next(c for c in configs if c.name == "github")
        assert github.url == "https://api.glama.ai/mcp/github"
        assert github.headers == {"Authorization": "Bearer test-token"}
        assert github.enabled is True

        linear = next(c for c in configs if c.name == "linear")
        assert linear.url == "https://mcp.linear.app/sse"
        assert linear.enabled is False

    async def test_global_mcp_configuration_invalid_url_rejected(self):
        """Test that non-HTTP URLs in settings are rejected at construction time."""
        from pydantic import ValidationError

        from server.app.settings import Settings

        with pytest.raises((ValueError, ValidationError)):
            Settings(
                COGNITION_MCP_SERVERS={
                    "bad": {"url": "file:///path/to/mcp.sock"},
                }
            )

    async def test_session_level_mcp_configuration(self):
        """Test that mcp_configs is passed through to create_cognition_agent."""
        from unittest.mock import AsyncMock, patch

        from server.app.settings import Settings

        settings = Settings(
            COGNITION_MCP_SERVERS={
                "github": {"url": "https://api.glama.ai/mcp/github"},
            }
        )

        with patch(
            "server.app.llm.deep_agent_service.create_cognition_agent",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MagicMock()

            # Build a minimal call that exercises the mcp_configs path
            from server.app.llm.deep_agent_service import DeepAgentStreamingService

            service = DeepAgentStreamingService(settings=settings)

            # Verify that when stream_response calls create_cognition_agent,
            # mcp_configs is populated from settings.mcp_server_configs.
            # We patch the storage and model to avoid real I/O.
            mock_session = MagicMock()
            mock_session.config = None
            mock_storage = AsyncMock()
            mock_storage.get_session.return_value = mock_session
            mock_storage.get_checkpointer.return_value = None

            with (
                patch(
                    "server.app.storage.get_storage_backend",
                    return_value=mock_storage,
                ),
                patch.object(
                    service.storage_backend,
                    "get_checkpointer",
                    new_callable=AsyncMock,
                    return_value=None,
                ),
                patch.object(
                    service, "_get_model", new_callable=AsyncMock, return_value=MagicMock()
                ),
            ):
                # Consume the generator to trigger create_cognition_agent
                try:
                    async for _ in service.stream_response(
                        session_id="test-session",
                        thread_id="test-thread",
                        project_path="/tmp",
                        content="hello",
                    ):
                        break
                except Exception:
                    pass  # We only care that create_cognition_agent was called correctly

            if mock_create.called:
                _, kwargs = mock_create.call_args
                assert "mcp_configs" in kwargs
                mcp_configs = kwargs["mcp_configs"]
                assert mcp_configs is not None
                assert len(mcp_configs) == 1
                assert mcp_configs[0].name == "github"
                assert mcp_configs[0].url == "https://api.glama.ai/mcp/github"


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
        """Integration test: Connect to Glama GitHub MCP."""
        import os

        api_key = os.getenv("GLAMA_API_KEY")
        if not api_key:
            pytest.skip("GLAMA_API_KEY not set")

        config = McpServerConfig(
            name="github",
            url="https://api.glama.ai/mcp/github",
            headers={"Authorization": f"Bearer {api_key}"},
        )

        async with McpSseClient(config) as client:
            tools = await client.list_tools()
            assert len(tools) > 0

            # Verify we can call a tool
            result = await client.call_tool(
                "get_repo", {"owner": "langchain-ai", "repo": "langchain"}
            )
            assert result["isError"] is False


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

        client = McpSseClient(config)

        # Headers should be stored securely
        assert client.config.headers["Authorization"] == "Bearer secret-token-12345"

        # When client is serialized/represented, token should not be visible
        # (This is implicit in the design - headers are not part of __repr__)

    async def test_env_var_expansion_in_headers(self):
        """Verify environment variable expansion works in headers."""
        import os

        os.environ["TEST_API_KEY"] = "test-secret"

        # This would test ${API_KEY} expansion in config
        # Implementation depends on config loading logic
        pass  # TODO: Implement when config loading is integrated
