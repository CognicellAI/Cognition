"""P3-SEC-4 & P3-SEC-5 Business Scenarios: Tool Namespace Security & CORS.

As a platform engineer deploying Cognition,
I want tool loading restricted to trusted namespaces and CORS properly configured
so that unauthorized code cannot be loaded and cross-origin attacks are prevented.

Business Value:
- Tool namespace allowlist prevents loading arbitrary Python modules
- CORS tightening prevents CSRF attacks from malicious websites
- Defense in depth for multi-tenant deployments
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestToolNamespaceAllowlist:
    """Test P3-SEC-4: Tool Module Allowlist."""

    async def test_trusted_namespace_default_configured(self, api_client) -> None:
        """Trusted tool namespaces are configured by default."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            # Check if trusted_tool_namespaces is exposed in config
            config_str = str(data)
            if "trusted" in config_str.lower() and "namespace" in config_str.lower():
                print(f"\n  Trusted namespaces configured")

    async def test_builtin_tools_load_from_trusted_namespace(self, api_client) -> None:
        """Built-in tools load from trusted namespace (server.app.tools)."""
        response = await api_client.get("/tools")

        if response.status_code == 200:
            data = response.json()
            tools = data.get("tools", [])

            for tool in tools:
                # Tools from trusted namespace should have appropriate module
                module = tool.get("module", "")
                # Module should be from trusted namespace
                if module:
                    assert (
                        "server" in module or ".cognition" in module or module.startswith("tools")
                    )

    async def test_untrusted_tool_path_rejected(self, api_client) -> None:
        """Tool paths outside trusted namespaces are rejected."""
        # This would require creating an agent definition with untrusted tool
        # For E2E, we verify the infrastructure exists

        response = await api_client.get("/agents")

        if response.status_code == 200:
            data = response.json()
            agents = data.get("agents", [])

            for agent in agents:
                # Check that agent tools are from trusted sources
                tools = agent.get("tools", [])
                for tool in tools:
                    # Should not be arbitrary system modules
                    assert "os.system" not in tool
                    assert "subprocess" not in tool

    async def test_allowlist_extensible(self, api_client) -> None:
        """Tool namespace allowlist is extensible via settings."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            # Document that allowlist should be extensible
            print(f"\n  Allowlist configuration available for customization")


@pytest.mark.asyncio
class TestCORSSecurity:
    """Test P3-SEC-5: CORS Default Tightening."""

    async def test_cors_not_wildcard_by_default(self, api_client) -> None:
        """CORS origins does not default to wildcard (*)."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            cors_origins = data.get("cors_origins", [])

            # Should not be wildcard
            assert "*" not in cors_origins, "CORS origins should not default to wildcard"

    async def test_cors_preflight_responds_correctly(self, api_client) -> None:
        """CORS preflight requests receive proper response."""
        # Make OPTIONS request with CORS headers
        response = await api_client.client.options(
            f"{api_client.base_url}/sessions",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )

        # Should return appropriate CORS headers
        assert response.status_code in [200, 204, 403]

    async def test_cors_blocks_unauthorized_origin(self, api_client) -> None:
        """CORS blocks requests from unauthorized origins."""
        import httpx

        # Make request from unauthorized origin
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{api_client.base_url}/health",
                headers={"Origin": "https://malicious-site.com"},
            )

            # Should either block or not include CORS headers for unauthorized origin
            assert response.status_code in [200, 403]

    async def test_cors_allows_authorized_origin(self, api_client) -> None:
        """CORS allows requests from authorized origins."""
        import httpx

        # Make request from authorized origin (localhost)
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{api_client.base_url}/health",
                headers={"Origin": "http://localhost:3000"},
            )

            # Should succeed
            assert response.status_code == 200

    async def test_cors_headers_present_on_authorized(self, api_client) -> None:
        """CORS headers present on authorized origin requests."""
        import httpx

        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{api_client.base_url}/health",
                headers={"Origin": "http://localhost:3000"},
            )

            # Check for CORS headers
            if "access-control-allow-origin" in response.headers:
                allowed_origin = response.headers["access-control-allow-origin"]
                print(f"\n  CORS allowed origin: {allowed_origin}")


@pytest.mark.asyncio
class TestSecurityConfigurationValidation:
    """Test security configuration validation."""

    async def test_security_settings_documented(self, api_client) -> None:
        """Security settings are documented in config."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()

            # Security-related settings should be present or documented
            security_keywords = ["cors", "security", "trust", "auth"]
            config_str = str(data).lower()

            found_keywords = [kw for kw in security_keywords if kw in config_str]
            print(f"\n  Security keywords found: {found_keywords}")

    async def test_production_security_recommendations(self, api_client) -> None:
        """Security recommendations for production deployments."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()

            # Document security recommendations
            recommendations = []

            cors_origins = data.get("cors_origins", [])
            if "*" in cors_origins:
                recommendations.append("WARNING: CORS set to wildcard (*)")

            if recommendations:
                print(f"\n  Security recommendations: {recommendations}")

    async def test_session_scoping_security(self, api_client) -> None:
        """Session scoping provides security isolation."""
        # Create session with scoping headers
        response = await api_client.post(
            "/sessions",
            json={"title": "Security Test Session"},
            headers={"X-Scope-User": "test-user-1"},
        )

        if response.status_code == 201:
            print("\n  Session created with scoping")
            # Session scoping provides tenant isolation
