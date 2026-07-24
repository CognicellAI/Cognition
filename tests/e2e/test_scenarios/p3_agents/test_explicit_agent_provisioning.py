"""P3 business scenarios for explicit builder Agent provisioning."""

from __future__ import annotations

import uuid

import pytest


def _unique_agent_name() -> str:
    return f"builder-agent-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.e2e
class TestExplicitAgentProvisioning:
    """Cognition exposes Agent CRUD without creating platform Agents."""

    async def test_agents_endpoint_has_no_native_agents(self, api_client) -> None:
        response = await api_client.get("/agents")

        assert response.status_code == 200
        agents = response.json()["agents"]
        assert all(agent["native"] is False for agent in agents)

    async def test_session_requires_explicit_agent_name(self, api_client) -> None:
        response = await api_client.client.post(
            f"{api_client.base_url}/sessions",
            json={"title": "Missing Agent Binding"},
            headers=api_client.scope_header,
        )

        assert response.status_code == 422

    async def test_builder_agent_can_be_bound_and_persisted(self, api_client) -> None:
        name = _unique_agent_name()
        create_agent = await api_client.post(
            "/agents",
            json={
                "name": name,
                "description": "Builder-provisioned E2E Agent",
                "system_prompt": "You are an explicitly provisioned test Agent.",
                "mode": "primary",
            },
        )
        assert create_agent.status_code == 201, create_agent.text
        assert create_agent.json()["native"] is False

        try:
            create_session = await api_client.post(
                "/sessions",
                json={"title": "Explicit Agent Session", "agent_name": name},
            )
            assert create_session.status_code == 201, create_session.text
            session_id = create_session.json()["id"]

            get_session = await api_client.get(f"/sessions/{session_id}")
            assert get_session.status_code == 200
            assert get_session.json()["agent_name"] == name
        finally:
            await api_client.delete(f"/agents/{name}")
