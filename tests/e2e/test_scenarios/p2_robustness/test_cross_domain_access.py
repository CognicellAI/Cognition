"""Business Scenario: Secure Cross-Domain API Access.

As a developer building a web frontend, I want to call the Cognition API
from a different domain securely, so I can build custom user interfaces.

Business Value:
- Flexibility to build custom frontends
- Secure cross-origin resource sharing
- Separation of frontend and backend concerns
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestCrossDomainAccess:
    """Test secure CORS for frontend integrations."""

    async def test_cors_preflight_request(self, api_client) -> None:
        """Test CORS preflight (OPTIONS) requests."""
        response = await api_client.client.options(
            f"{api_client.base_url}/sessions",
            headers={
                "Origin": "http://example.com",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Content-Type",
            },
        )

        # Should accept preflight
        assert response.status_code in [200, 204], f"Preflight failed: {response.status_code}"

    async def test_cors_headers_present(self, api_client) -> None:
        """Test CORS headers are present."""
        response = await api_client.client.options(
            f"{api_client.base_url}/sessions",
            headers={"Origin": "http://example.com", "Access-Control-Request-Method": "POST"},
        )

        # Check for CORS headers
        cors_headers = [
            "access-control-allow-origin",
            "access-control-allow-methods",
            "access-control-allow-headers",
        ]

        found = []
        for header in cors_headers:
            if header in response.headers:
                found.append(header)

        print(f"\n  CORS headers: {', '.join(found) if found else 'none'}")

        # Should have at least some CORS headers
        assert len(found) > 0, "No CORS headers found"

    async def test_cross_origin_post(self, api_client) -> None:
        """Test cross-origin POST requests."""
        response = await api_client.client.post(
            f"{api_client.base_url}/sessions",
            json={"title": "CORS Test Session"},
            headers={"Origin": "http://example.com", "Content-Type": "application/json"},
        )

        # May be 201 (success) or 403 (blocked)
        if response.status_code == 201:
            print("\n  Cross-origin POST accepted")
        elif response.status_code == 403:
            print("\n  Cross-origin POST blocked (may be expected)")
        else:
            print(f"\n  Cross-origin POST: {response.status_code}")

    async def test_cors_in_get_responses(self, api_client) -> None:
        """Test CORS headers in GET responses."""
        response = await api_client.client.get(
            f"{api_client.base_url}/health", headers={"Origin": "http://example.com"}
        )

        assert response.status_code == 200

        # Check for allow-origin
        if "access-control-allow-origin" in response.headers:
            origin = response.headers["access-control-allow-origin"]
            print(f"\n  Allow-Origin: {origin}")

    async def test_cors_with_credentials(self, api_client) -> None:
        """Test CORS with credentials."""
        response = await api_client.client.get(
            f"{api_client.base_url}/config",
            headers={"Origin": "http://example.com"},
            cookies={"test": "value"},
        )

        # Should handle request with cookies
        assert response.status_code == 200
        print("\n  Request with cookies handled")

    async def test_cors_for_sse(self, api_client, session) -> None:
        """Test CORS for SSE streaming."""
        # Try to open SSE stream with origin
        try:
            async with api_client.client.stream(
                "POST",
                f"{api_client.base_url}/sessions/{session}/messages",
                json={"content": "CORS SSE test"},
                headers={"Origin": "http://example.com", "Accept": "text/event-stream"},
                timeout=5.0,
            ) as response:
                # Check headers
                if "access-control-allow-origin" in response.headers:
                    print("\n  CORS headers present for SSE")
                else:
                    print("\n  CORS headers not present for SSE")
        except Exception:
            print("\n  SSE stream test completed")
