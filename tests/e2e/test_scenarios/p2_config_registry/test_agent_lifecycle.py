"""Business Scenario: Agent Definition Lifecycle.

As a platform engineer, I want to create, modify, and remove custom agent
definitions via the API so that I can ship specialised agents to users without
redeploying the server.

Business Value:
- Custom agents tailored to specific workflows (e.g. "security-reviewer")
- Live updates to agent prompts and tooling
- Clean decommissioning of agents no longer needed
- Native built-in agents remain protected from accidental modification
"""

from __future__ import annotations

import uuid

import pytest


def _unique(prefix: str = "agent") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.e2e
class TestAgentLifecycle:
    """Agent CRUD end-to-end via the /agents API."""

    async def test_list_agents_returns_200(self, api_client) -> None:
        """GET /agents is accessible and returns a valid envelope."""
        response = await api_client.get("/agents")

        assert response.status_code == 200
        data = response.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)

    async def test_list_agents_includes_built_in_default(self, api_client) -> None:
        """The built-in 'default' agent always appears in the list."""
        response = await api_client.get("/agents")
        assert response.status_code == 200

        names = [a["name"] for a in response.json()["agents"]]
        assert "default" in names

    async def test_get_default_agent_structure(self, api_client) -> None:
        """GET /agents/default returns the expected fields."""
        response = await api_client.get("/agents/default")

        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "default"
        assert "description" in data
        assert "mode" in data
        assert "hidden" in data
        assert "native" in data
        assert data["native"] is True

    async def test_get_missing_agent_returns_404(self, api_client) -> None:
        """GET /agents/{name} for an unknown agent returns 404."""
        response = await api_client.get("/agents/no-such-agent-xyz123")
        assert response.status_code == 404

    async def test_create_custom_agent_appears_in_list(self, api_client) -> None:
        """A newly created custom agent is visible via GET /agents."""
        name = _unique()

        create_resp = await api_client.post(
            "/agents",
            json={
                "name": name,
                "description": "E2E test agent",
                "system_prompt": "You are a test agent.",
                "mode": "primary",
            },
        )
        assert create_resp.status_code == 201, create_resp.text

        data = create_resp.json()
        assert data["name"] == name
        assert data["description"] == "E2E test agent"
        assert data["native"] is False

        # Verify visible in list
        list_resp = await api_client.get("/agents")
        names = [a["name"] for a in list_resp.json()["agents"]]
        assert name in names

        # Cleanup
        await api_client.delete(f"/agents/{name}")

    async def test_get_custom_agent_by_name(self, api_client) -> None:
        """GET /agents/{name} returns the custom agent after creation."""
        name = _unique()

        await api_client.post(
            "/agents",
            json={
                "name": name,
                "description": "Lookup test",
                "system_prompt": "Test.",
            },
        )

        get_resp = await api_client.get(f"/agents/{name}")
        assert get_resp.status_code == 200
        assert get_resp.json()["name"] == name

        # Cleanup
        await api_client.delete(f"/agents/{name}")

    async def test_patch_agent_updates_description(self, api_client) -> None:
        """PATCH /agents/{name} updates only the specified fields."""
        name = _unique()

        await api_client.post(
            "/agents",
            json={
                "name": name,
                "description": "Original description",
                "system_prompt": "Original prompt.",
            },
        )

        patch_resp = await api_client.patch(
            f"/agents/{name}",
            json={"description": "Updated description"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        data = patch_resp.json()
        assert data["description"] == "Updated description"

        # Cleanup
        await api_client.delete(f"/agents/{name}")

    async def test_patch_missing_agent_returns_404(self, api_client) -> None:
        """PATCH on a non-existent agent returns 404."""
        response = await api_client.patch(
            "/agents/no-such-agent-xyz123",
            json={"description": "update"},
        )
        assert response.status_code == 404

    async def test_delete_custom_agent_removes_from_list(self, api_client) -> None:
        """DELETE /agents/{name} removes the custom agent."""
        name = _unique()

        await api_client.post(
            "/agents",
            json={"name": name, "description": "To be deleted", "system_prompt": "Test."},
        )

        delete_resp = await api_client.delete(f"/agents/{name}")
        assert delete_resp.status_code == 204

        # Verify gone from list and direct get
        list_resp = await api_client.get("/agents")
        names = [a["name"] for a in list_resp.json()["agents"]]
        assert name not in names

        get_resp = await api_client.get(f"/agents/{name}")
        assert get_resp.status_code == 404

    async def test_cannot_delete_native_agent(self, api_client) -> None:
        """DELETE on a built-in native agent returns 409."""
        response = await api_client.delete("/agents/default")
        assert response.status_code == 409

    async def test_cannot_overwrite_native_agent_via_post(self, api_client) -> None:
        """POST /agents with a native agent name returns 409."""
        response = await api_client.post(
            "/agents",
            json={"name": "default", "description": "Override attempt", "system_prompt": "Test."},
        )
        assert response.status_code == 409

    async def test_cannot_patch_native_agent(self, api_client) -> None:
        """PATCH on a built-in native agent returns 409."""
        response = await api_client.patch(
            "/agents/default",
            json={"description": "Override attempt"},
        )
        assert response.status_code == 409

    async def test_custom_agent_usable_for_session(self, api_client) -> None:
        """A custom agent created via API can be used to create a session."""
        name = _unique()

        await api_client.post(
            "/agents",
            json={
                "name": name,
                "description": "Session test agent",
                "system_prompt": "You are a helpful test agent.",
                "mode": "primary",
            },
        )

        session_resp = await api_client.post(
            "/sessions",
            json={"title": "Custom Agent Session", "agent_name": name},
        )
        assert session_resp.status_code == 201, session_resp.text
        assert session_resp.json()["agent_name"] == name

        # Cleanup
        await api_client.delete(f"/agents/{name}")
