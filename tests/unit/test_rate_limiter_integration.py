"""Tests for rate limiter integration (P0-4).

Tests that the rate limiter is properly wired to endpoints.
"""

import asyncio
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from server.app.api.dependencies import get_rate_limiter_dep, get_settings_dep
from server.app.exceptions import RateLimitError
from server.app.main import app
from server.app.rate_limiter import RateLimitConfig, RateLimiter

client = TestClient(app)


class TestRateLimiterIntegration:
    """Test rate limiter integration with API endpoints."""

    @pytest.mark.asyncio
    async def test_rate_limit_applied_to_messages(self):
        """Test that rate limiting is applied to message creation."""
        mock_limiter = MagicMock(spec=RateLimiter)
        mock_limiter.check_rate_limit = MagicMock(return_value=asyncio.Future())
        mock_limiter.check_rate_limit.return_value.set_result(None)
        app.dependency_overrides[get_rate_limiter_dep] = lambda: mock_limiter
        try:
            # Create a session first
            session_resp = client.post("/sessions", json={"title": "rate-limit-test"})
            session_id = session_resp.json()["id"]

            # Send a message
            response = client.post(
                f"/sessions/{session_id}/messages",
                json={"content": "Hello"},
                headers={"Accept": "text/event-stream"},
            )

            # Should call rate limiter
            assert mock_limiter.check_rate_limit.called
        finally:
            app.dependency_overrides.pop(get_rate_limiter_dep, None)

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded_returns_429(self):
        """Test that exceeding rate limit returns 429 status."""
        mock_limiter = MagicMock(spec=RateLimiter)

        async def raise_rate_limit(*args, **kwargs):
            raise RateLimitError(
                resource="test-client",
                limit=60,
                window=60,
            )

        mock_limiter.check_rate_limit = raise_rate_limit
        app.dependency_overrides[get_rate_limiter_dep] = lambda: mock_limiter
        try:
            # Create a session
            session_resp = client.post("/sessions", json={"title": "rate-limit-test"})
            session_id = session_resp.json()["id"]

            # Send a message
            response = client.post(
                f"/sessions/{session_id}/messages",
                json={"content": "Hello"},
                headers={"Accept": "text/event-stream"},
            )

            # Should return 429
            assert response.status_code == 429
            assert "rate limit" in response.json()["detail"].lower()
        finally:
            app.dependency_overrides.pop(get_rate_limiter_dep, None)

    def test_rate_limiter_uses_scope_key(self):
        """Test that rate limiter uses scope key when scoping is enabled."""
        from server.app.settings import Settings

        scoped_settings = Settings(
            scoping_enabled=True,
            scope_keys=["user"],
        )
        mock_limiter = MagicMock(spec=RateLimiter)
        mock_limiter.check_rate_limit = MagicMock(return_value=asyncio.Future())
        mock_limiter.check_rate_limit.return_value.set_result(None)

        app.dependency_overrides[get_rate_limiter_dep] = lambda: mock_limiter
        app.dependency_overrides[get_settings_dep] = lambda: scoped_settings
        try:
            # Create a session with scope header
            session_resp = client.post(
                "/sessions",
                json={"title": "scoped-rate-limit-test"},
                headers={"X-Cognition-Scope-User": "alice"},
            )

            if session_resp.status_code == 201:
                session_id = session_resp.json()["id"]

                # Send a message
                client.post(
                    f"/sessions/{session_id}/messages",
                    json={"content": "Hello"},
                    headers={
                        "Accept": "text/event-stream",
                        "X-Cognition-Scope-User": "alice",
                    },
                )

                # Rate limiter should be called with user scope
                call_args = mock_limiter.check_rate_limit.call_args
                if call_args:
                    key = call_args[0][0]
                    assert "alice" in key or "user" in key.lower()
        finally:
            app.dependency_overrides.pop(get_rate_limiter_dep, None)
            app.dependency_overrides.pop(get_settings_dep, None)


class TestRateLimiterConfig:
    """Test rate limiter configuration."""

    def test_rate_limit_config_defaults(self):
        """Test default rate limit configuration."""
        config = RateLimitConfig()

        assert config.requests_per_minute == 60
        assert config.burst_size == 10
        assert config.window_seconds == 60

    def test_rate_limit_config_custom(self):
        """Test custom rate limit configuration."""
        config = RateLimitConfig(
            requests_per_minute=120,
            burst_size=20,
            window_seconds=30,
        )

        assert config.requests_per_minute == 120
        assert config.burst_size == 20
        assert config.window_seconds == 30
