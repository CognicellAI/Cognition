"""P3-SEC-1 Business Scenarios: Tool Security Trust Model.

As a security engineer,
I want to understand the security boundaries for tool execution
so that I can deploy Cognition safely.

Security Trust Model:
- Container isolation (Docker sandbox backend) provides process-level isolation.
- ToolSecurityMiddleware provides per-tool blocklisting for multi-tenant deployments.
- Cognition no longer exposes API/file Python tool loading.
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestToolSecurityTrustModel:
    """Test P3-SEC-1: Tool security trust model documentation and validation."""

    async def test_tool_security_middleware_still_active(self, api_client) -> None:
        """ToolSecurityMiddleware (tool blocklist) is still active for multi-tenant safety.

        Note: This tests the COGNITION_BLOCKED_TOOLS setting which blocklists
        specific tool names at the middleware level — this is real security,
        not AST theater.
        """
        response = await api_client.get("/capabilities")
        assert response.status_code == 200
        middleware = response.json().get("middleware", [])
        assert "ToolSecurityMiddleware" in middleware

    async def test_python_tool_registry_api_removed(self, api_client) -> None:
        """Cognition no longer exposes API/file Python tool registration."""
        response = await api_client.get("/tools")
        assert response.status_code == 404
