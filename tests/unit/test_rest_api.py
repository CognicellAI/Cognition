"""Tests for REST API.

Tests for the Phase 5 REST API implementation.
"""

import pytest
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


class TestProjectEndpoints:
    """Test project API endpoints."""

    def test_create_project(self):
        """Test creating a project."""
        response = client.post(
            "/projects",
            json={"name": "test-project", "description": "A test project"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-project"
        assert data["description"] == "A test project"
        assert "id" in data
        assert "path" in data
        assert "created_at" in data
        assert "updated_at" in data

    def test_create_project_validation(self):
        """Test project creation validation."""
        # Empty name should fail
        response = client.post("/projects", json={"name": ""})
        assert response.status_code == 422

    def test_list_projects(self):
        """Test listing projects."""
        response = client.get("/projects")
        assert response.status_code == 200
        data = response.json()
        assert "projects" in data
        assert "total" in data
        assert isinstance(data["projects"], list)

    def test_get_project_not_found(self):
        """Test getting a non-existent project."""
        response = client.get("/projects/non-existent-id")
        assert response.status_code == 404

    def test_delete_project_not_found(self):
        """Test deleting a non-existent project."""
        response = client.delete("/projects/non-existent-id")
        assert response.status_code == 404


class TestSessionEndpoints:
    """Test session API endpoints."""

    def test_create_session(self):
        """Test creating a session."""
        # First create a project
        project_resp = client.post(
            "/projects",
            json={"name": "test-session-project"},
        )
        assert project_resp.status_code == 201
        project_id = project_resp.json()["id"]

        # Create session
        response = client.post(
            "/sessions",
            json={
                "project_id": project_id,
                "title": "Test Session",
                "config": {"provider": "mock", "temperature": 0.7},
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["project_id"] == project_id
        assert data["title"] == "Test Session"
        assert "id" in data
        assert "thread_id" in data
        assert "config" in data
        assert data["status"] == "active"

    def test_create_session_validation(self):
        """Test session creation validation."""
        # Missing project_id should fail
        response = client.post("/sessions", json={"title": "Test"})
        assert response.status_code == 422

    def test_list_sessions(self):
        """Test listing sessions."""
        response = client.get("/sessions")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data
        assert "total" in data

    def test_list_sessions_by_project(self):
        """Test filtering sessions by project."""
        response = client.get("/sessions?project_id=test-id")
        assert response.status_code == 200
        data = response.json()
        assert "sessions" in data

    def test_get_session_not_found(self):
        """Test getting a non-existent session."""
        response = client.get("/sessions/non-existent-id")
        assert response.status_code == 404

    def test_update_session(self):
        """Test updating a session."""
        # Create project and session first
        project_resp = client.post("/projects", json={"name": "update-test"})
        assert project_resp.status_code == 201
        project_id = project_resp.json()["id"]

        session_resp = client.post(
            "/sessions",
            json={"project_id": project_id, "title": "Original"},
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Update session
        response = client.patch(
            f"/sessions/{session_id}",
            json={"title": "Updated"},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["title"] == "Updated"

    def test_delete_session(self):
        """Test deleting a session."""
        # Create project and session first
        project_resp = client.post("/projects", json={"name": "delete-test"})
        assert project_resp.status_code == 201
        project_id = project_resp.json()["id"]

        session_resp = client.post(
            "/sessions",
            json={"project_id": project_id},
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Delete session
        response = client.delete(f"/sessions/{session_id}")
        assert response.status_code == 204

        # Verify it's gone
        get_resp = client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 404


class TestMessageEndpoints:
    """Test message API endpoints."""

    def test_list_messages(self):
        """Test listing messages."""
        response = client.get("/sessions/test-session/messages")
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
        # Create project and session first
        project_resp = client.post("/projects", json={"name": "sse-test"})
        assert project_resp.status_code == 201
        project_id = project_resp.json()["id"]

        session_resp = client.post(
            "/sessions",
            json={"project_id": project_id},
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Send message
        response = client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Hello, agent!"},
            headers={"Accept": "text/event-stream"},
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "text/event-stream; charset=utf-8"

        # Check that we got SSE data
        content = response.content.decode()
        assert "event:" in content or "data:" in content


class TestConfigEndpoints:
    """Test config API endpoints."""

    def test_get_config(self):
        """Test getting server configuration."""
        response = client.get("/config")
        assert response.status_code == 200
        data = response.json()
        assert "server" in data
        assert "llm" in data
        assert "rate_limit" in data

        # Verify no secrets exposed
        assert "api_key" not in str(data).lower()
        assert "secret" not in str(data).lower()


class TestAPIIntegration:
    """Integration tests for full workflow."""

    def test_full_workflow(self):
        """Test complete workflow: create project → create session → send message."""
        # 1. Create project
        project_resp = client.post(
            "/projects",
            json={"name": "integration-test", "description": "Test workflow"},
        )
        assert project_resp.status_code == 201
        project_id = project_resp.json()["id"]

        # 2. Create session
        session_resp = client.post(
            "/sessions",
            json={"project_id": project_id, "title": "Integration Test"},
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # 3. Send message (SSE stream)
        message_resp = client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "Test message"},
            headers={"Accept": "text/event-stream"},
        )
        assert message_resp.status_code == 200

        # 4. List messages
        list_resp = client.get(f"/sessions/{session_id}/messages")
        assert list_resp.status_code == 200
        assert list_resp.json()["total"] >= 1

        # 5. Delete session
        delete_resp = client.delete(f"/sessions/{session_id}")
        assert delete_resp.status_code == 204
