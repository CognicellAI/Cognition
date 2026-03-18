"""Business Scenario: Hot-Reload of Config Without Server Restart.

As a platform operator, I want changes made via the Config API to take effect
immediately — without restarting the server — so that I can tune the system
dynamically and custom agents are usable the moment they are created.

Business Value:
- Zero-downtime configuration updates
- Immediate availability of new agents and skills
- Config changes reflected in subsequent sessions without intervention

Hot-reload verification strategy:
1. Create a resource via the API.
2. Immediately attempt to use it (e.g. create a session with the new agent).
3. Verify the resource is functional without any restart in between.
"""

from __future__ import annotations

import uuid

import pytest


def _unique(prefix: str = "hot") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.e2e
class TestHotReload:
    """Verify config changes propagate immediately without server restart."""

    async def test_new_agent_immediately_usable(self, api_client) -> None:
        """An agent created via POST /agents can be used for a session immediately."""
        name = _unique("agent")

        create_resp = await api_client.post(
            "/agents",
            json={
                "name": name,
                "description": "Hot-reload test agent",
                "system_prompt": "You are a test agent. Answer only with 'ok'.",
                "mode": "primary",
            },
        )
        assert create_resp.status_code == 201, create_resp.text

        # No restart — immediately create a session with the new agent
        session_resp = await api_client.post(
            "/sessions",
            json={"title": "Hot-reload session", "agent_name": name},
        )
        assert session_resp.status_code == 201, (
            f"New agent not immediately usable: {session_resp.text}"
        )
        assert session_resp.json()["agent_name"] == name

        # Cleanup
        await api_client.delete(f"/agents/{name}")

    async def test_updated_agent_description_reflects_immediately(self, api_client) -> None:
        """PATCH /agents/{name} is immediately reflected in GET /agents/{name}."""
        name = _unique("agent")

        await api_client.post(
            "/agents",
            json={
                "name": name,
                "description": "Before update",
                "system_prompt": "Test.",
            },
        )

        await api_client.patch(
            f"/agents/{name}",
            json={"description": "After update"},
        )

        # Immediately check — no reload needed
        get_resp = await api_client.get(f"/agents/{name}")
        assert get_resp.status_code == 200
        assert get_resp.json()["description"] == "After update"

        # Cleanup
        await api_client.delete(f"/agents/{name}")

    async def test_deleted_agent_immediately_unavailable(self, api_client) -> None:
        """An agent deleted via DELETE /agents/{name} cannot be used immediately after."""
        name = _unique("agent")

        await api_client.post(
            "/agents",
            json={"name": name, "description": "Will be deleted", "system_prompt": "Test."},
        )

        await api_client.delete(f"/agents/{name}")

        # Immediately try to create a session — should fail (agent gone)
        session_resp = await api_client.post(
            "/sessions",
            json={"title": "Post-delete session", "agent_name": name},
        )
        assert session_resp.status_code == 422, (
            f"Deleted agent still usable: {session_resp.status_code}"
        )

    async def test_new_skill_visible_immediately_in_list(self, api_client) -> None:
        """A skill registered via POST /skills appears in GET /skills immediately."""
        name = _unique("skill")

        await api_client.post(
            "/skills",
            json={"name": name, "path": f".cognition/skills/{name}.md"},
        )

        # No reload — immediately list
        list_resp = await api_client.get("/skills")
        assert list_resp.status_code == 200
        names = [s["name"] for s in list_resp.json()["skills"]]
        assert name in names, f"Skill '{name}' not immediately visible in list"

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_new_provider_visible_immediately_in_list(self, api_client) -> None:
        """A provider registered via POST /models/providers appears immediately."""
        provider_id = _unique("prov")

        await api_client.post(
            "/models/providers",
            json={"id": provider_id, "provider": "openai", "model": "gpt-4o"},
        )

        list_resp = await api_client.get("/models/providers")
        assert list_resp.status_code == 200
        ids = [p["id"] for p in list_resp.json()["providers"]]
        assert provider_id in ids, f"Provider '{provider_id}' not immediately visible"

        # Cleanup
        await api_client.delete(f"/models/providers/{provider_id}")

    async def test_config_changes_survive_multiple_reads(self, api_client) -> None:
        """Config changes remain consistent across multiple sequential reads."""
        name = _unique("skill")

        await api_client.post(
            "/skills",
            json={"name": name, "path": "consistent.md", "description": "v1"},
        )

        # Update description
        await api_client.patch(f"/skills/{name}", json={"description": "v2"})

        # Read three times — all should return v2
        for _ in range(3):
            resp = await api_client.get(f"/skills/{name}")
            assert resp.status_code == 200
            assert resp.json()["description"] == "v2", "Config not consistent across reads"

        # Cleanup
        await api_client.delete(f"/skills/{name}")
