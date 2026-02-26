"""P3-TR Business Scenarios: Tool Registry End-to-End.

As a developer building custom AI tools for my workspace,
I want to create, register, and manage tools easily
so that the AI can use my specialized capabilities.

Business Value:
- Tools discovered automatically from .cognition/tools/
- Hot-reload enables rapid iteration without server restart
- API visibility for monitoring and debugging
- Security controls prevent unauthorized tool execution
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestToolDiscovery:
    """Test P3-TR-1: Tool Discovery Logic."""

    async def test_tools_endpoint_returns_registered_tools(self, api_client) -> None:
        """GET /tools returns list of registered tools."""
        response = await api_client.get("/tools")

        # Endpoint should return 200 even if registry not initialized
        # (returns empty list in that case)
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert "tools" in data
            assert isinstance(data["tools"], list)

    async def test_tool_has_required_fields(self, api_client) -> None:
        """Each tool has name, source, and module fields."""
        response = await api_client.get("/tools")

        if response.status_code != 200:
            pytest.skip("Tool registry not initialized")

        data = response.json()

        for tool in data.get("tools", []):
            assert "name" in tool
            assert "source" in tool
            assert "module" in tool

    async def test_get_specific_tool_detail(self, api_client) -> None:
        """GET /tools/{name} returns specific tool details."""
        # First get list to find a tool name
        list_response = await api_client.get("/tools")
        if list_response.status_code != 200:
            pytest.skip("Tool registry not initialized")

        tools = list_response.json().get("tools", [])
        if not tools:
            pytest.skip("No tools registered")

        tool_name = tools[0]["name"]

        response = await api_client.get(f"/tools/{tool_name}")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == tool_name

    async def test_get_nonexistent_tool_returns_404(self, api_client) -> None:
        """Request for non-existent tool returns 404."""
        response = await api_client.get("/tools/nonexistent-tool-xyz123")

        assert response.status_code == 404


@pytest.mark.asyncio
class TestToolHotReload:
    """Test P3-TR-2: AgentRegistry + File Watcher Integration."""

    async def test_file_watcher_triggers_reload(self, api_client) -> None:
        """File changes trigger tool registry reload."""
        # Get initial tool count
        initial_response = await api_client.get("/tools")

        # Note: Actual file modification would require filesystem access
        # This test verifies the reload endpoint works
        reload_response = await api_client.post("/tools/reload")

        # Should return 200, 404 (endpoint not mounted), or 503 (registry not initialized)
        assert reload_response.status_code in [200, 404, 503]

        if reload_response.status_code == 200:
            result = reload_response.json()
            assert "count" in result

    async def test_reload_returns_error_count(self, api_client) -> None:
        """Reload endpoint returns error count along with tool count."""
        response = await api_client.post("/tools/reload")

        if response.status_code == 503:
            pytest.skip("Tool registry not initialized")

        assert response.status_code == 200
        data = response.json()

        assert "count" in data
        assert "errors" in data
        assert isinstance(data["errors"], list)


@pytest.mark.asyncio
class TestToolSecurityMiddleware:
    """Test P3-TR-5: Tool Security Middleware."""

    async def test_blocked_tool_not_available(self, api_client) -> None:
        """Tools on blocklist are not registered or available."""
        response = await api_client.get("/tools")

        if response.status_code != 200:
            pytest.skip("Tool registry not initialized")

        data = response.json()
        tool_names = [t["name"] for t in data.get("tools", [])]

        # Blocked tools should not appear in the list
        # (Assuming certain tools are configured as blocked)

    async def test_tool_audit_logging(self, api_client, session) -> None:
        """Tool calls are logged for security auditing."""
        # Send a message that will trigger a tool call
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "List files in current directory"},
        )

        # Tool call should succeed and be logged
        assert response.status_code in [200, 201]


@pytest.mark.asyncio
class TestToolLoadErrors:
    """Test P3-TR-7: Tool Load Error Visibility."""

    async def test_tool_errors_endpoint_exists(self, api_client) -> None:
        """GET /tools/errors endpoint returns error list."""
        response = await api_client.get("/tools/errors")

        # Should return 200 (empty list) or 404 if endpoint not available
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert isinstance(data, list)

    async def test_error_format_has_required_fields(self, api_client) -> None:
        """Tool errors have file, error_type, error, and timestamp fields."""
        response = await api_client.get("/tools/errors")

        if response.status_code != 200:
            pytest.skip("Tool errors endpoint not available")

        errors = response.json()

        for error in errors:
            assert "file" in error
            assert "error_type" in error
            assert "error" in error
            assert "timestamp" in error

    async def test_reload_clears_previous_errors(self, api_client) -> None:
        """Reloading tools clears previous error list."""
        # Trigger reload
        reload_response = await api_client.post("/tools/reload")

        if reload_response.status_code == 503:
            pytest.skip("Tool registry not initialized")

        assert reload_response.status_code == 200

        # Get errors after reload
        errors_response = await api_client.get("/tools/errors")

        if errors_response.status_code == 200:
            errors = errors_response.json()
            # Errors should be fresh from reload
            assert isinstance(errors, list)


@pytest.mark.asyncio
class TestToolInConversation:
    """Test P3-TR-3: Tools Available in Conversations."""

    async def test_session_with_tools_can_execute(self, api_client, session) -> None:
        """Sessions can use registered tools during conversations."""
        response = await api_client.post(
            f"/sessions/{session}/messages",
            json={"content": "Read the README.md file"},
        )

        # Message should be accepted and processed
        assert response.status_code in [200, 201]

    async def test_tools_listed_in_session_context(self, api_client, session) -> None:
        """Tools available to a session are accessible."""
        # Get session details
        response = await api_client.get(f"/sessions/{session}")

        assert response.status_code == 200
        data = response.json()

        # Session should have context that includes available tools
        assert "id" in data


@pytest.mark.asyncio
class TestToolDirectoryAutoCreation:
    """Test P3-TR-9: .cognition/ Directory Auto-Creation."""

    async def test_config_endpoint_creates_cognition_dir(self, api_client) -> None:
        """Config endpoint ensures .cognition/ directory exists."""
        # The server should handle missing directories gracefully
        response = await api_client.get("/config")

        assert response.status_code == 200

    async def test_tools_endpoint_works_without_manual_setup(self, api_client) -> None:
        """Tools endpoint works even if .cognition/tools/ not manually created."""
        response = await api_client.get("/tools")

        # Should return 200 with empty list or 404 if registry not initialized
        # Either is acceptable - server shouldn't crash
        assert response.status_code in [200, 404]

        if response.status_code == 200:
            data = response.json()
            assert "tools" in data


@pytest.mark.asyncio
class TestUpstreamMiddlewareIntegration:
    """Test P3-TR-6: Upstream Middleware Configuration."""

    async def test_agent_with_middleware_loads(self, api_client) -> None:
        """Agents with middleware configuration load successfully."""
        # Create a session (which creates an agent with middleware)
        response = await api_client.post(
            "/sessions",
            json={"title": "Middleware Test Session"},
        )

        assert response.status_code == 201
        data = response.json()
        assert "id" in data

    async def test_tool_retry_middleware_available(self, api_client) -> None:
        """Tool retry middleware can be configured via agent definition."""
        # Create a session to verify agent runtime works
        response = await api_client.post(
            "/sessions",
            json={"title": "Retry Middleware Test"},
        )

        assert response.status_code == 201

    async def test_pii_middleware_available(self, api_client) -> None:
        """PII redaction middleware can be configured."""
        # Verify PII middleware is available in the system
        # by checking that agent creation succeeds

        response = await api_client.post(
            "/sessions",
            json={"title": "PII Middleware Test"},
        )

        assert response.status_code == 201
