"""Tests for REST API.

Tests for the Phase 5 REST API implementation with workspace-based sessions.
"""

import tempfile
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from server.app.api.dependencies import set_config_store
from server.app.main import app

# Create test client
client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_agent_registry():
    """Initialize agent registry for tests."""
    import tempfile

    from server.app.api.dependencies import set_model_catalog_dep
    from server.app.llm.model_catalog import ModelCatalog
    from server.app.settings import get_settings
    from server.app.storage.config_registry import MemoryConfigRegistry
    from server.app.storage.config_store import DefaultConfigStore

    with tempfile.TemporaryDirectory() as tmpdir:
        set_config_store(DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmpdir))
        s = get_settings()
        set_model_catalog_dep(ModelCatalog(catalog_url=s.model_catalog_url))
        yield


class TestHealthEndpoints:
    """Test health check endpoints."""

    def test_health_check(self):
        """Test health endpoint returns healthy status."""
        response = client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "healthy"
        assert "version" in data
        assert "active_sessions" in data
        assert "timestamp" in data

    def test_ready_check(self):
        """Test ready endpoint returns ready status."""
        response = client.get("/ready")
        assert response.status_code == 200
        data = response.json()
        assert data["ready"] is True


class TestSessionEndpoints:
    """Test session API endpoints."""

    def test_create_session(self):
        """Test creating a session."""
        response = client.post(
            "/sessions",
            json={"title": "Test Session", "metadata": {"workflow": "review", "repo": "acme/app"}},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Session"
        assert data["metadata"] == {"workflow": "review", "repo": "acme/app"}
        assert "id" in data
        assert "thread_id" in data
        # Note: No workspace_path or config in response (server uses global settings)

    def test_create_session_validation(self):
        """Test session creation validation."""
        # Title too long should fail
        response = client.post("/sessions", json={"title": "x" * 201})
        assert response.status_code == 422

    def test_list_sessions(self):
        """Test listing sessions."""
        # Create a session first
        client.post(
            "/sessions",
            json={"title": "list-test-session", "metadata": {"repo": "myorg/myrepo"}},
        )

        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "total" in data
        assert isinstance(data["sessions"], list)

        filtered = client.get("/sessions?metadata.repo=myorg/myrepo")
        assert filtered.status_code == 200
        filtered_data = filtered.json()
        assert any(
            session["metadata"].get("repo") == "myorg/myrepo"
            for session in filtered_data["sessions"]
        )

    def test_get_session(self):
        """Test getting a session."""
        # Create a session
        create_resp = client.post("/sessions", json={"title": "get-test-session"})
        session_id = create_resp.json()["id"]

        # Get the session
        response = client.get(f"/sessions/{session_id}")
        assert response.status_code == 200
        data = response.json()
        assert data["id"] == session_id
        assert data["title"] == "get-test-session"

    def test_get_session_not_found(self):
        """Test getting a non-existent session."""
        response = client.get("/sessions/non-existent-id")
        assert response.status_code == 404

    def test_update_session(self):
        """Test updating a session."""
        # Create a session
        create_resp = client.post("/sessions", json={"title": "original-title"})
        session_id = create_resp.json()["id"]

        # Update the session
        response = client.patch(
            f"/sessions/{session_id}",
            json={"title": "updated-title", "metadata": {"ticket": "ABC-123"}},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "updated-title"
        assert data["metadata"] == {"ticket": "ABC-123"}

    def test_update_session_model_only_resolves_unambiguous_provider(self):
        provider_resp = client.post(
            "/models/providers",
            json={
                "id": "session-openai-compatible",
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )
        assert provider_resp.status_code == 201

        create_resp = client.post("/sessions", json={"title": "session-provider-resolution"})
        session_id = create_resp.json()["id"]

        response = client.patch(
            f"/sessions/{session_id}",
            json={"config": {"model": "google/gemini-3-flash-preview"}},
        )
        assert response.status_code == 200

    def test_update_session_model_only_rejects_ambiguous_provider_resolution(self):
        client.post(
            "/models/providers",
            json={
                "id": "session-openai-compatible-ambiguous",
                "provider": "openai_compatible",
                "model": "shared-model",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )
        client.post(
            "/models/providers",
            json={
                "id": "session-openai-ambiguous",
                "provider": "openai",
                "model": "shared-model",
            },
        )

        create_resp = client.post("/sessions", json={"title": "session-provider-ambiguous"})
        session_id = create_resp.json()["id"]

        response = client.patch(
            f"/sessions/{session_id}",
            json={"config": {"model": "shared-model"}},
        )
        assert response.status_code == 422
        assert "multiple provider types" in response.json()["detail"]

    def test_update_session_model_only_rejects_unknown_model(self):
        create_resp = client.post("/sessions", json={"title": "session-provider-unknown-model"})
        session_id = create_resp.json()["id"]

        response = client.patch(
            f"/sessions/{session_id}",
            json={"config": {"model": "not-configured-anywhere"}},
        )
        assert response.status_code == 422
        assert "is not configured on any enabled provider" in response.json()["detail"]

    def test_delete_session(self):
        """Test deleting a session."""
        # Create a session
        create_resp = client.post("/sessions", json={"title": "delete-test-session"})
        session_id = create_resp.json()["id"]

        # Delete the session
        response = client.delete(f"/sessions/{session_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 404


class TestMessageEndpoints:
    """Test message API endpoints."""

    def test_list_messages(self):
        """Test listing messages."""
        # Create a session first
        session_resp = client.post("/sessions", json={"title": "msg-list-test"})
        session_id = session_resp.json()["id"]

        response = client.get(f"/sessions/{session_id}/messages")
        assert response.status_code == 200
        data = response.json()
        assert "messages" in data
        assert "total" in data
        assert "has_more" in data

    def test_list_messages_session_not_found(self):
        """Test listing messages for non-existent session."""
        response = client.get("/sessions/non-existent/messages")
        assert response.status_code == 404

    def test_send_message_sse(self):
        """Test sending a message returns SSE stream."""
        # Create session first
        session_resp = client.post("/sessions", json={"title": "sse-test"})
        session_id = session_resp.json()["id"]

        response = client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Hello, world!"},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")

    def test_send_message_accepts_callback_url(self):
        """Test sending a message with callback_url returns SSE stream."""
        session_resp = client.post("/sessions", json={"title": "callback-test"})
        session_id = session_resp.json()["id"]

        with patch(
            "server.app.api.routes.messages._post_completion_callback",
            new=AsyncMock(),
        ) as mock_callback:
            response = client.post(
                f"/sessions/{session_id}/messages",
                json={
                    "content": "Hello, world!",
                    "callback_url": "https://example.com/callback",
                },
                headers={"Accept": "text/event-stream"},
            )

        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        assert mock_callback.await_count == 1


class TestConfigEndpoints:
    """Test config API endpoints."""

    def test_get_config(self):
        """Test getting server config."""
        response = client.get("/config")
        assert response.status_code == 200
        data = response.json()
        assert "server" in data
        assert "llm" in data


class TestModelEndpoints:
    """Test model catalog API behavior."""

    @pytest.fixture(autouse=True)
    def reset_config_store(self):
        from server.app.api.dependencies import set_model_catalog_dep
        from server.app.llm.model_catalog import ModelCatalog
        from server.app.settings import get_settings
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        with tempfile.TemporaryDirectory() as tmpdir:
            set_config_store(DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmpdir))
            settings = get_settings()
            set_model_catalog_dep(ModelCatalog(catalog_url=settings.model_catalog_url))
            yield

    def test_list_models_returns_empty_without_configured_providers(self):
        response = client.get("/models")
        assert response.status_code == 200
        assert response.json()["models"] == []

    def test_list_models_returns_only_configured_provider_types(self):
        client.post(
            "/models/providers",
            json={"id": "catalog-openai", "provider": "openai", "model": "gpt-4o"},
        )

        response = client.get("/models")
        assert response.status_code == 200
        models = response.json()["models"]
        assert models
        assert all(model["provider"] == "openai" for model in models)

    def test_list_models_filters_to_requested_configured_provider(self):
        client.post(
            "/models/providers",
            json={"id": "catalog-openai-2", "provider": "openai", "model": "gpt-4o"},
        )
        client.post(
            "/models/providers",
            json={
                "id": "catalog-anthropic-2",
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            },
        )

        response = client.get("/models", params={"provider": "anthropic"})
        assert response.status_code == 200
        models = response.json()["models"]
        assert models
        assert all(model["provider"] == "anthropic" for model in models)

    def test_list_models_excludes_unconfigured_provider_filter(self):
        client.post(
            "/models/providers",
            json={"id": "catalog-openai-3", "provider": "openai", "model": "gpt-4o"},
        )

        response = client.get("/models", params={"provider": "anthropic"})
        assert response.status_code == 200
        assert response.json()["models"] == []

    def test_list_models_openai_compatible_contributes_no_catalog_models(self):
        client.post(
            "/models/providers",
            json={
                "id": "catalog-openrouter",
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
                "base_url": "https://openrouter.ai/api/v1",
            },
        )

        response = client.get("/models")
        assert response.status_code == 200
        assert response.json()["models"] == []


class TestAPIIntegration:
    """Integration tests for full workflows."""

    def test_full_workflow(self):
        """Test complete workflow."""
        # Create session
        session_resp = client.post("/sessions", json={"title": "integration-test"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # List sessions
        list_resp = client.get("/sessions")
        assert list_resp.status_code == 200
        assert any(s["id"] == session_id for s in list_resp.json()["sessions"])

        # Get session
        get_resp = client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 200
        assert get_resp.json()["id"] == session_id

        # Delete session
        del_resp = client.delete(f"/sessions/{session_id}")
        assert del_resp.status_code == 204

        # Verify deletion
        verify_resp = client.get(f"/sessions/{session_id}")
        assert verify_resp.status_code == 404


class TestSessionAgentName:
    """Test session creation with agent_name parameter."""

    def test_create_session_with_agent_name(self):
        """Test creating a session with explicit agent_name."""
        response = client.post(
            "/sessions",
            json={"title": "Agent Test Session", "agent_name": "readonly"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["agent_name"] == "readonly"

    def test_create_session_default_agent(self):
        """Test creating session without agent_name defaults to 'default'."""
        response = client.post(
            "/sessions",
            json={"title": "Default Agent Session"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["agent_name"] == "default"

    def test_create_session_invalid_agent_name(self):
        """Test creating session with unknown agent_name returns 422."""
        response = client.post(
            "/sessions",
            json={"title": "Invalid Agent Session", "agent_name": "nonexistent-agent"},
        )
        assert response.status_code == 422

    def test_session_agent_name_persisted(self):
        """Test agent_name is persisted and returned in session details."""
        # Create session
        create_resp = client.post(
            "/sessions",
            json={"title": "Persisted Agent", "agent_name": "readonly"},
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Get session and verify agent_name
        get_resp = client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()
        assert data["agent_name"] == "readonly"


class TestAgentEndpoints:
    """Test agent management API endpoints."""

    def test_list_agents(self):
        """Test listing agents endpoint."""
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)

    def test_list_agents_contains_builtins(self):
        """Test that built-in agents are in the list."""
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()
        agent_names = [a["name"] for a in data["agents"]]
        assert "default" in agent_names
        assert "readonly" in agent_names

    def test_list_agents_structure(self):
        """Test that agent list items have correct structure."""
        response = client.get("/agents")
        assert response.status_code == 200
        data = response.json()

        for agent in data["agents"]:
            assert "name" in agent
            assert "description" in agent
            assert "mode" in agent
            assert "hidden" in agent
            assert "native" in agent

    def test_get_agent(self):
        """Test getting a specific agent."""
        response = client.get("/agents/default")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "default"
        assert "description" in data
        assert "mode" in data

    def test_get_agent_not_found(self):
        """Test getting non-existent agent returns 404."""
        response = client.get("/agents/nonexistent-agent-12345")
        assert response.status_code == 404

    def test_get_agent_fields(self):
        """Test agent detail has all expected fields."""
        response = client.get("/agents/default")
        assert response.status_code == 200
        data = response.json()

        assert "name" in data
        assert "description" in data
        assert "mode" in data
        assert "hidden" in data
        assert "native" in data
        assert "model" in data
        assert "temperature" in data
