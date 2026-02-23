"""Business Scenario: Real-Time Configuration Updates.

As a DevOps engineer, I want configuration changes to take effect
without server restarts, so I can tune the system dynamically.

Business Value:
- Zero-downtime configuration updates
- Dynamic resource allocation
- Immediate response to changing conditions
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestRealTimeConfiguration:
    """Test dynamic configuration management."""

    async def test_configuration_endpoint_accessible(self, api_client) -> None:
        """Test configuration endpoint is accessible."""
        response = await api_client.get("/config")

        assert response.status_code == 200, "Config endpoint failed"

        data = response.json()
        assert "server" in data, "Missing server config"
        assert "llm" in data, "Missing LLM config"
        assert "rate_limit" in data, "Missing rate limit config"

    async def test_configuration_sections(self, api_client) -> None:
        """Test configuration sections."""
        response = await api_client.get("/config")
        data = response.json()

        print("\n  Configuration sections:")
        print(f"    Server: {list(data.get('server', {}).keys())}")
        print(f"    LLM: {data.get('llm', {}).get('provider', 'unknown')}")

    async def test_configuration_consistency(self, api_client) -> None:
        """Test configuration consistent across reads."""
        configs = []
        for _ in range(3):
            response = await api_client.get("/config")
            assert response.status_code == 200
            configs.append(response.json())

        # All should have same structure
        first_keys = set(configs[0].keys())
        for config in configs[1:]:
            assert set(config.keys()) == first_keys, "Config inconsistent"

        print("\n  Configuration consistent across reads")

    async def test_rate_limit_configuration(self, api_client) -> None:
        """Test rate limit configuration."""
        response = await api_client.get("/config")
        data = response.json()

        rate_config = data.get("rate_limit", {})

        if "per_minute" in rate_config:
            print(f"\n  Rate limit: {rate_config['per_minute']}/min")
        if "burst" in rate_config:
            print(f"  Burst: {rate_config['burst']}")

    async def test_llm_provider_config(self, api_client) -> None:
        """Test LLM provider configuration."""
        response = await api_client.get("/config")
        data = response.json()

        llm_config = data.get("llm", {})
        provider = llm_config.get("provider", "unknown")
        model = llm_config.get("model", "unknown")

        print(f"\n  Provider: {provider}")
        print(f"  Model: {model}")

    async def test_server_settings(self, api_client) -> None:
        """Test server settings."""
        response = await api_client.get("/config")
        data = response.json()

        server_config = data.get("server", {})

        if "scoping_enabled" in server_config:
            print(f"\n  Scoping: {server_config['scoping_enabled']}")
        if "version" in server_config:
            print(f"  Version: {server_config['version']}")

    async def test_config_reflected_in_behavior(self, api_client) -> None:
        """Test configuration reflected in system behavior."""
        # Create session (validates system works)
        sid = await api_client.create_session("Config Test")

        response = await api_client.send_message(sid, "Test config")
        assert response.status_code == 200, "System not responding to config"

    async def test_health_reflects_config(self, api_client) -> None:
        """Test health endpoint reflects configuration."""
        response = await api_client.get("/health")

        assert response.status_code == 200

        data = response.json()
        assert data.get("status") == "healthy"

        print(f"\n  Health: {data.get('status')}")
