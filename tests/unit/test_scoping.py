"""Tests for session scoping harness (P0-3).

Tests for the generic session scoping via X-Cognition-Scope headers.
"""

import pytest
from fastapi.testclient import TestClient
from unittest.mock import patch

from server.app.main import app
from server.app.api.scoping import SessionScope, extract_scope_from_headers, create_scope_dependency
from server.app.settings import Settings


client = TestClient(app)


class TestSessionScope:
    """Test the SessionScope class."""

    def test_get_scope_value(self):
        """Test retrieving scope values."""
        scope = SessionScope({"user": "alice", "project": "test-project"})

        assert scope.get("user") == "alice"
        assert scope.get("project") == "test-project"
        assert scope.get("nonexistent") is None

    def test_get_all_scopes(self):
        """Test retrieving all scope values."""
        scopes = {"user": "alice", "project": "test-project"}
        scope = SessionScope(scopes)

        assert scope.get_all() == scopes

    def test_scope_matching(self):
        """Test scope matching logic."""
        scope = SessionScope({"user": "alice", "project": "proj1"})

        # Exact match
        assert scope.matches({"user": "alice", "project": "proj1"}) is True

        # Superset match (has extra keys, but matching keys are equal)
        assert scope.matches({"user": "alice", "project": "proj1", "team": "eng"}) is True

        # Mismatch
        assert scope.matches({"user": "bob", "project": "proj1"}) is False
        assert scope.matches({"user": "alice", "project": "proj2"}) is False

    def test_is_empty(self):
        """Test empty scope detection."""
        assert SessionScope({}).is_empty() is True
        assert SessionScope({"user": "alice"}).is_empty() is False


class TestExtractScopeFromHeaders:
    """Test scope extraction from headers."""

    def test_extract_single_scope(self):
        """Test extracting a single scope dimension."""
        settings = Settings(scoping_enabled=False, scope_keys=["user"])

        scope = extract_scope_from_headers(settings, user="alice")

        assert scope.get("user") == "alice"

    def test_extract_multiple_scopes(self):
        """Test extracting multiple scope dimensions."""
        settings = Settings(scoping_enabled=False, scope_keys=["user", "project"])

        scope = extract_scope_from_headers(settings, user="alice", project="proj1")

        assert scope.get("user") == "alice"
        assert scope.get("project") == "proj1"

    def test_extract_missing_scope(self):
        """Test extraction with missing scope values."""
        settings = Settings(scoping_enabled=False, scope_keys=["user", "project"])

        scope = extract_scope_from_headers(settings, user="alice")

        assert scope.get("user") == "alice"
        assert scope.get("project") is None


class TestScopingIntegration:
    """Integration tests for scoping in API endpoints."""

    def test_session_creation_with_scope(self):
        """Test creating a session with scope headers."""
        with patch("server.app.api.routes.sessions.get_settings") as mock_get_settings:
            mock_get_settings.return_value = Settings(
                scoping_enabled=True,
                scope_keys=["user"],
            )

            response = client.post(
                "/sessions",
                json={"title": "Scoped Session"},
                headers={"X-Cognition-Scope-User": "alice"},
            )

            # Should succeed with scope header
            assert response.status_code == 201

    def test_session_creation_without_scope_fail_closed(self):
        """Test that missing scope headers fail when scoping is enabled."""
        with patch("server.app.api.routes.sessions.get_settings") as mock_get_settings:
            mock_get_settings.return_value = Settings(
                scoping_enabled=True,
                scope_keys=["user"],
            )

            response = client.post(
                "/sessions",
                json={"title": "Scoped Session"},
                # No scope header
            )

            # Should fail with 403
            assert response.status_code == 403
            assert "Missing required scope headers" in response.json()["detail"]

    def test_session_creation_disabled_scoping(self):
        """Test that sessions work without scope when scoping is disabled."""
        with patch("server.app.api.routes.sessions.get_settings") as mock_get_settings:
            mock_get_settings.return_value = Settings(
                scoping_enabled=False,
                scope_keys=["user"],
            )

            response = client.post(
                "/sessions",
                json={"title": "Unscoped Session"},
                # No scope header, but scoping is disabled
            )

            # Should succeed
            assert response.status_code == 201

    def test_multi_dimensional_scoping(self):
        """Test scoping with multiple dimensions."""
        with patch("server.app.api.routes.sessions.get_settings") as mock_get_settings:
            mock_get_settings.return_value = Settings(
                scoping_enabled=True,
                scope_keys=["user", "project"],
            )

            response = client.post(
                "/sessions",
                json={"title": "Multi-scoped Session"},
                headers={
                    "X-Cognition-Scope-User": "alice",
                    "X-Cognition-Scope-Project": "proj1",
                },
            )

            # Should succeed with both headers
            assert response.status_code == 201

    def test_multi_dimensional_scoping_partial(self):
        """Test that partial scope fails when all keys are required."""
        with patch("server.app.api.routes.sessions.get_settings") as mock_get_settings:
            mock_get_settings.return_value = Settings(
                scoping_enabled=True,
                scope_keys=["user", "project"],
            )

            response = client.post(
                "/sessions",
                json={"title": "Partially Scoped Session"},
                headers={
                    "X-Cognition-Scope-User": "alice",
                    # Missing project header
                },
            )

            # Should fail because project is missing
            assert response.status_code == 403
