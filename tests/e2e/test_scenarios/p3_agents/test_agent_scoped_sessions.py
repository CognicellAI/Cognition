"""P3 Business Scenario: Agent Scoped Sessions.

As a frontend developer building a chat UI on top of Cognition's API,
I want GET /agents to return a list of available agents so that I can
render an agent-picker dropdown when a user starts a new session.

As a developer using the default primary agent, I want any subagent-mode
agents to be automatically available for delegation via the task tool.

Business Value:
- Agent selection UI can be populated from API
- Sessions are bound to specific agents for their lifetime
- Subagent delegation works automatically without configuration
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestAgentScopedSessions:
    """Test agent selection and session binding."""

    async def test_agents_list_structure(self, api_client) -> None:
        """Agent list has correct structure for UI rendering."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()

        # Structure validation
        assert "agents" in data
        assert isinstance(data["agents"], list)

        # Each agent should have UI-relevant fields
        for agent in data["agents"]:
            assert "name" in agent
            assert "description" in agent
            assert "mode" in agent

    async def test_agent_detail_structure_has_all_fields(self, api_client) -> None:
        """Agent detail has all fields needed for display."""
        response = await api_client.get("/agents/default")

        assert response.status_code == 200
        data = response.json()

        # All expected fields
        assert "name" in data
        assert "description" in data
        assert "mode" in data
        assert "hidden" in data
        assert "native" in data
        assert "model" in data
        assert "temperature" in data

    async def test_get_nonexistent_agent_returns_404(self, api_client) -> None:
        """Request for non-existent agent returns 404."""
        response = await api_client.get("/agents/nonexistent-agent-xyz123")

        assert response.status_code == 404

    async def test_create_session_with_valid_agent(self, api_client) -> None:
        """Create session with valid primary agent succeeds."""
        response = await api_client.post(
            "/sessions",
            json={"title": "Valid Agent Session", "agent_name": "default"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["agent_name"] == "default"

    async def test_create_session_with_invalid_agent_returns_422(self, api_client) -> None:
        """Create session with invalid agent returns 422."""
        response = await api_client.post(
            "/sessions",
            json={"title": "Invalid Agent Session", "agent_name": "no-such-agent"},
        )

        assert response.status_code == 422

    async def test_session_list_includes_agent_name_field(self, api_client) -> None:
        """GET /sessions returns sessions with agent_name field."""
        # Create a session first
        create_resp = await api_client.post(
            "/sessions",
            json={"title": "Listed Session", "agent_name": "readonly"},
        )
        assert create_resp.status_code == 201

        # List sessions
        response = await api_client.get("/sessions")

        assert response.status_code == 200
        data = response.json()

        # Sessions should have agent_name
        assert "sessions" in data
        for session in data["sessions"]:
            assert "agent_name" in session

    async def test_session_detail_includes_agent_name(self, api_client) -> None:
        """GET /sessions/{id} includes agent_name field."""
        # Create session
        create_resp = await api_client.post(
            "/sessions",
            json={"title": "Detail Session", "agent_name": "readonly"},
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Get detail
        response = await api_client.get(f"/sessions/{session_id}")

        assert response.status_code == 200
        data = response.json()

        assert "agent_name" in data
        assert data["agent_name"] == "readonly"

    async def test_default_session_agent_name_is_default(self, api_client) -> None:
        """Session created without agent_name has 'default' as agent_name."""
        response = await api_client.post(
            "/sessions",
            json={"title": "No Agent Specified Session"},
        )

        assert response.status_code == 201
        data = response.json()

        assert data["agent_name"] == "default"

    async def test_multiple_sessions_different_agents(self, api_client) -> None:
        """Multiple sessions can have different agents."""
        # Create sessions with different agents
        session1 = await api_client.post(
            "/sessions",
            json={"title": "Session 1", "agent_name": "default"},
        )
        session2 = await api_client.post(
            "/sessions",
            json={"title": "Session 2", "agent_name": "readonly"},
        )

        assert session1.status_code == 201
        assert session2.status_code == 201

        # Verify different agent names
        assert session1.json()["agent_name"] == "default"
        assert session2.json()["agent_name"] == "readonly"

        # Verify persistence
        get1 = await api_client.get(f"/sessions/{session1.json()['id']}")
        get2 = await api_client.get(f"/sessions/{session2.json()['id']}")

        assert get1.json()["agent_name"] == "default"
        assert get2.json()["agent_name"] == "readonly"

    async def test_agent_name_immutable_after_patch(self, api_client) -> None:
        """PATCH /sessions does not change agent_name."""
        # Create session with readonly agent
        create_resp = await api_client.post(
            "/sessions",
            json={"title": "Immutable Agent Session", "agent_name": "readonly"},
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Try to update title (not agent_name)
        patch_resp = await api_client.patch(
            f"/sessions/{session_id}",
            json={"title": "Updated Title"},
        )
        assert patch_resp.status_code == 200

        # Verify agent_name unchanged
        get_resp = await api_client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()

        assert data["agent_name"] == "readonly"
        assert data["title"] == "Updated Title"
