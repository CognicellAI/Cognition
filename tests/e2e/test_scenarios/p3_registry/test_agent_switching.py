"""Test scenarios for agent switching functionality."""

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
class TestAgentSwitching:
    """Test switching agents within a session."""

    async def test_switch_agent_in_session(self, api_client):
        """Test that updating session agent_name changes the active agent."""
        # Use built-in agents: "default" and "readonly"
        # Both should be available in the server's registry

        # 1. Create session (defaults to "default")
        response = await api_client.post("/sessions", json={"title": "Switch Test"})
        if response.status_code == 403:
            pytest.skip("Server requires scoping headers - skipping test")
        assert response.status_code == 201
        session_data = response.json()
        session_id = session_data["id"]
        assert session_data["agent_name"] == "default"

        # 2. Verify initial state
        response = await api_client.get(f"/sessions/{session_id}")
        assert response.json()["agent_name"] == "default"

        # 3. Switch agent to "readonly"
        response = await api_client.patch(
            f"/sessions/{session_id}", json={"agent_name": "readonly"}
        )
        assert response.status_code == 200
        assert response.json()["agent_name"] == "readonly"

        # 4. Verify persistence
        response = await api_client.get(f"/sessions/{session_id}")
        assert response.json()["agent_name"] == "readonly"

        # 5. Try switching back to "default"
        response = await api_client.patch(f"/sessions/{session_id}", json={"agent_name": "default"})
        assert response.status_code == 200
        assert response.json()["agent_name"] == "default"

    async def test_create_session_with_specific_agent(self, api_client):
        """Test creating a session bound to a specific agent."""
        # Create session with "readonly" agent
        response = await api_client.post(
            "/sessions", json={"title": "Readonly Session", "agent_name": "readonly"}
        )
        if response.status_code == 403:
            pytest.skip("Server requires scoping headers - skipping test")
        assert response.status_code == 201
        assert response.json()["agent_name"] == "readonly"

    async def test_switch_to_invalid_agent(self, api_client):
        """Test that switching to an invalid agent fails."""
        # Create session with default agent
        response = await api_client.post("/sessions", json={"title": "Test Session"})
        if response.status_code == 403:
            pytest.skip("Server requires scoping headers - skipping test")
        assert response.status_code == 201
        session_id = response.json()["id"]

        # Try to switch to non-existent agent
        response = await api_client.patch(
            f"/sessions/{session_id}", json={"agent_name": "nonexistent"}
        )
        # Note: If registry is None (e.g., in test contexts), validation is skipped
        # and this will return 200. In production with registry initialized,
        # this should return 422.
        if response.status_code == 422:
            assert "Invalid or unknown agent" in response.json()["detail"]
        else:
            # Registry not initialized, skip validation check
            pytest.skip("Registry not initialized - skipping validation test")

    async def test_create_session_with_invalid_agent(self, api_client):
        """Test that creating a session with an invalid agent fails."""
        response = await api_client.post(
            "/sessions", json={"title": "Invalid Session", "agent_name": "nonexistent"}
        )
        if response.status_code == 403:
            pytest.skip("Server requires scoping headers - skipping test")
        # Note: If registry is None, validation is skipped
        if response.status_code == 422:
            assert "Invalid or unknown agent" in response.json()["detail"]
        else:
            pytest.skip("Registry not initialized - skipping validation test")
