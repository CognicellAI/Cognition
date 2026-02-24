"""P3 Business Scenario: Workspace Custom Agents.

As a developer on a Python data engineering team, I want to drop a data-reviewer.md
file into .cognition/agents/ so that every developer on my team automatically gets
a specialized agent that understands our conventions.

Business Value:
- Workspace-specific agents without server changes
- Team conventions encoded in agent definitions
- Agents committed to version control alongside code
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestWorkspaceCustomAgents:
    """Test workspace author experience with custom agents."""

    async def test_custom_researcher_agent_in_list(self, api_client) -> None:
        """Custom researcher agent appears in agent list."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()
        agent_names = [a["name"] for a in data["agents"]]

        # The researcher.md file in .cognition/agents/ should be loaded
        assert "researcher" in agent_names, "Custom researcher agent should be in list"

    async def test_researcher_agent_is_subagent_mode(self, api_client) -> None:
        """Researcher agent has mode='subagent' for delegation."""
        response = await api_client.get("/agents/researcher")

        assert response.status_code == 200
        data = response.json()

        assert data["mode"] == "subagent"

    async def test_researcher_has_correct_description(self, api_client) -> None:
        """Researcher agent has description from frontmatter."""
        response = await api_client.get("/agents/researcher")

        assert response.status_code == 200
        data = response.json()

        assert data["description"] is not None
        assert "research" in data["description"].lower()

    async def test_researcher_is_not_native(self, api_client) -> None:
        """Researcher agent is not a built-in (native=false)."""
        response = await api_client.get("/agents/researcher")

        assert response.status_code == 200
        data = response.json()

        assert data["native"] is False

    async def test_create_session_with_subagent_mode_rejected(self, api_client) -> None:
        """Creating session with subagent-mode agent returns 422."""
        response = await api_client.post(
            "/sessions",
            json={"title": "Research Session", "agent_name": "researcher"},
        )

        assert response.status_code == 422, (
            "Subagent-mode agents should be rejected for primary sessions"
        )

    async def test_get_researcher_agent_detail(self, api_client) -> None:
        """GET /agents/researcher returns full agent details."""
        response = await api_client.get("/agents/researcher")

        assert response.status_code == 200
        data = response.json()

        # Verify all expected fields
        assert data["name"] == "researcher"
        assert data["description"] is not None
        assert data["mode"] == "subagent"
        assert data["hidden"] is False
        assert data["native"] is False

    async def test_builtin_agents_still_present(self, api_client) -> None:
        """Built-in agents remain available alongside custom agents."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()
        agent_names = [a["name"] for a in data["agents"]]

        # Built-ins should still be there
        assert "default" in agent_names
        assert "readonly" in agent_names
        # Plus our custom agent
        assert "researcher" in agent_names

    async def test_agent_count_increased(self, api_client) -> None:
        """Total agent count is 2 built-ins + 1 custom = 3."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()

        # Should have default, readonly, and researcher
        assert len(data["agents"]) >= 3
