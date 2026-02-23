"""Tests for REST API.

Tests for the Phase 5 REST API implementation with workspace-based sessions.
"""

from fastapi.testclient import TestClient

from server.app.main import app

# Create test client
client = TestClient(app)


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
            json={"title": "Test Session"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["title"] == "Test Session"
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
        client.post("/sessions", json={"title": "list-test-session"})

        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "total" in data
        assert isinstance(data["sessions"], list)

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
            json={"title": "updated-title"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "updated-title"

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


class TestConfigEndpoints:
    """Test config API endpoints."""

    def test_get_config(self):
        """Test getting server config."""
        response = client.get("/config")
        assert response.status_code == 200
        data = response.json()
        assert "server" in data
        assert "llm" in data


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
