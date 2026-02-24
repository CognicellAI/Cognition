"""P3 Business Scenario: Platform Built-in Agents.

As a platform engineer deploying Cognition as a shared service for my organization,
I want built-in agents available out of the box so that I can give developers
a safe agent for code review and analysis in CI pipelines.

Business Value:
- Built-in agents work immediately without configuration
- Readonly agent provides safe analysis without write/execute
- Default agent provides full coding capabilities
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestPlatformBuiltInAgents:
    """Test platform engineer experience with built-in agents."""

    async def test_agents_endpoint_reachable(self, api_client) -> None:
        """GET /agents is available and returns 200."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()
        assert "agents" in data

    async def test_default_agent_in_list(self, api_client) -> None:
        """Built-in 'default' agent appears in agent list."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()
        agent_names = [a["name"] for a in data["agents"]]

        assert "default" in agent_names

    async def test_readonly_agent_in_list(self, api_client) -> None:
        """Built-in 'readonly' agent appears in agent list."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()
        agent_names = [a["name"] for a in data["agents"]]

        assert "readonly" in agent_names

    async def test_builtin_agents_have_descriptions(self, api_client) -> None:
        """Built-in agents have non-empty descriptions."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()

        builtin_agents = [a for a in data["agents"] if a.get("native") is True]

        for agent in builtin_agents:
            assert agent.get("description") is not None
            assert len(agent["description"]) > 0

    async def test_builtin_agents_are_primary_mode(self, api_client) -> None:
        """Built-in agents have mode='primary' for user selection."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()

        builtin_agents = [a for a in data["agents"] if a.get("native") is True]

        for agent in builtin_agents:
            # Built-ins should be primary or all mode
            assert agent["mode"] in ("primary", "all")

    async def test_default_agent_detail(self, api_client) -> None:
        """GET /agents/default returns default agent details."""
        response = await api_client.get("/agents/default")

        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "default"
        assert data["native"] is True
        assert data["mode"] in ("primary", "all")
        assert data["hidden"] is False

    async def test_readonly_agent_detail(self, api_client) -> None:
        """GET /agents/readonly returns readonly agent details."""
        response = await api_client.get("/agents/readonly")

        assert response.status_code == 200
        data = response.json()

        assert data["name"] == "readonly"
        assert data["native"] is True
        assert data["mode"] in ("primary", "all")
        assert data["hidden"] is False

    async def test_create_session_default_agent_explicit(self, api_client) -> None:
        """Create session with explicit agent_name='default'."""
        response = await api_client.post(
            "/sessions",
            json={"title": "Default Agent Session", "agent_name": "default"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["agent_name"] == "default"

    async def test_create_session_readonly_agent(self, api_client) -> None:
        """Create session with agent_name='readonly'."""
        response = await api_client.post(
            "/sessions",
            json={"title": "Readonly Session", "agent_name": "readonly"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["agent_name"] == "readonly"

    async def test_session_agent_name_persisted(self, api_client) -> None:
        """Agent name is persisted and returned in session details."""
        # Create session with readonly agent
        create_resp = await api_client.post(
            "/sessions",
            json={"title": "Persisted Agent Session", "agent_name": "readonly"},
        )
        assert create_resp.status_code == 201
        session_id = create_resp.json()["id"]

        # Get session and verify agent_name persisted
        get_resp = await api_client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 200
        data = get_resp.json()

        assert data["agent_name"] == "readonly"

    async def test_create_session_no_agent_name_defaults_to_default(self, api_client) -> None:
        """Create session without agent_name defaults to 'default'."""
        response = await api_client.post(
            "/sessions",
            json={"title": "Defaulted Agent Session"},
        )

        assert response.status_code == 201
        data = response.json()
        assert data["agent_name"] == "default"
