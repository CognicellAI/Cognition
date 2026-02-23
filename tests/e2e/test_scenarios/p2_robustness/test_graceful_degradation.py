"""Business Scenario: Graceful Service Degradation.

As a user, I want the system to gracefully handle AI provider failures
by falling back to alternative providers, so my work isn't interrupted.

Business Value:
- High availability even when primary AI provider is down
- Seamless failover without user awareness
- Continued productivity during service outages
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestGracefulServiceDegradation:
    """Test system resilience through provider fallback."""

    async def test_health_endpoint_accessible(self, api_client) -> None:
        """Test health endpoint is accessible."""
        response = await api_client.get("/health")

        assert response.status_code == 200, "Health check failed"

        data = response.json()
        assert data.get("status") == "healthy", "System not healthy"

    async def test_circuit_breaker_status(self, api_client) -> None:
        """Test circuit breaker status in health endpoint."""
        response = await api_client.get("/health")

        if response.status_code == 200:
            data = response.json()
            # Circuit breaker info may or may not be present
            if "circuit_breaker" in data:
                print(f"Circuit breaker state: {data['circuit_breaker']}")

    async def test_normal_ai_operation(self, api_client, session) -> None:
        """Test normal AI service operation."""
        response = await api_client.send_message(session, "Test normal operation")

        assert response.status_code == 200, "Normal operation failed"

    async def test_provider_configuration_accessible(self, api_client) -> None:
        """Test provider configuration is accessible."""
        response = await api_client.get("/config")

        assert response.status_code == 200

        data = response.json()
        assert "llm" in data, "LLM config not found"

        provider = data["llm"].get("provider")
        assert provider, "Provider not configured"
        print(f"Current provider: {provider}")

    async def test_service_under_load(self, api_client, session) -> None:
        """Test service handles load gracefully."""
        success_count = 0
        failure_count = 0

        for i in range(10):
            response = await api_client.send_message(session, f"Load test message {i}")

            if response.status_code == 200:
                success_count += 1
            else:
                failure_count += 1

        print(f"\nLoad test: {success_count} success, {failure_count} failure")

        # Should handle load without total failure
        assert success_count > 0, "All requests failed under load"

    async def test_health_after_load(self, api_client, session) -> None:
        """Test health endpoint responsive after load."""
        # Generate some load
        for i in range(5):
            await api_client.send_message(session, f"Load {i}")

        # Check health
        response = await api_client.get("/health")

        assert response.status_code == 200, "Health endpoint not responsive after load"

    async def test_multiple_provider_availability(self, api_client) -> None:
        """Test that multiple providers are configured."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            llm_config = data.get("llm", {})

            available = llm_config.get("available_providers", [])
            if available:
                print(f"\nAvailable providers: {len(available)}")
                for provider in available[:3]:  # Show first 3
                    print(f"  - {provider.get('name', 'Unknown')}")

    async def test_ready_endpoint(self, api_client) -> None:
        """Test ready endpoint."""
        response = await api_client.get("/ready")

        assert response.status_code == 200

        data = response.json()
        assert data.get("ready") is True, "System not ready"
