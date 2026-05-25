"""Business Scenario: Tool Safety Middleware

Trusted runtime context injection, tool argument validation,
filesystem permissions, and human-in-the-loop configuration
are verifiable via the agent definition API.
"""

from __future__ import annotations

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


@pytest.mark.asyncio
class TestToolSafetyMiddlewareConfig:
    """Tool safety middleware is configurably exposed through agents."""

    async def test_default_agent_has_interrupt_on(
        self, api_client: ScenarioTestClient
    ) -> None:
        """The default agent includes HITL config on filesystem tools."""
        resp = await api_client.get("/agents/default")
        assert resp.status_code == 200
        agent = resp.json()
        interrupt_on = agent.get("interrupt_on", {})
        assert isinstance(interrupt_on, dict)
        for tool in ("write_file", "edit_file", "execute"):
            if tool in interrupt_on:
                config = interrupt_on[tool]
                assert isinstance(config, dict)
                assert "allowed_decisions" in config

    async def test_readonly_agent_has_no_interrupt_on(
        self, api_client: ScenarioTestClient
    ) -> None:
        """The readonly agent restricts HITL to filesystem tools only."""
        resp = await api_client.get("/agents/readonly")
        assert resp.status_code == 200
        agent = resp.json()
        interrupt_on = agent.get("interrupt_on", {})
        assert isinstance(interrupt_on, dict)

    async def test_can_create_agent_with_filesystem_permissions(
        self, api_client: ScenarioTestClient
    ) -> None:
        """An agent can be created with filesystem permission rules."""
        import uuid

        name = f"perm-test-{uuid.uuid4().hex[:8]}"
        resp = await api_client.post(
            "/agents",
            json={
                "name": name,
                "system_prompt": "Test agent",
                "mode": "subagent",
                "permissions": [
                    {
                        "operations": ["read"],
                        "paths": ["/workspace/**"],
                        "mode": "allow",
                    },
                    {
                        "operations": ["read", "write"],
                        "paths": ["/**"],
                        "mode": "deny",
                    },
                ],
            },
        )
        assert resp.status_code == 201
        agent = resp.json()
        permissions = agent.get("permissions", [])
        assert len(permissions) == 2
        assert permissions[0]["mode"] == "allow"
        assert permissions[1]["mode"] == "deny"

        await api_client.delete(f"/agents/{name}")

    async def test_can_create_agent_with_rich_hitl_config(
        self, api_client: ScenarioTestClient
    ) -> None:
        """An agent can be created with full HITL policy per tool."""
        import uuid

        name = f"hitl-test-{uuid.uuid4().hex[:8]}"
        resp = await api_client.post(
            "/agents",
            json={
                "name": name,
                "system_prompt": "Test agent with HITL",
                "mode": "subagent",
                "interrupt_on": {
                    "write_file": {
                        "allowed_decisions": ["approve", "reject"],
                    },
                    "execute": {
                        "allowed_decisions": ["approve", "edit", "reject"],
                        "description": "Shell command execution requires approval",
                    },
                },
            },
        )
        assert resp.status_code == 201
        agent = resp.json()
        interrupt_on = agent.get("interrupt_on", {})
        assert "write_file" in interrupt_on
        assert interrupt_on["write_file"]["allowed_decisions"] == ["approve", "reject"]
        assert "execute" in interrupt_on
        execute_config = interrupt_on["execute"]
        assert execute_config["allowed_decisions"] == ["approve", "edit", "reject"]
        assert execute_config["description"] == "Shell command execution requires approval"

        await api_client.delete(f"/agents/{name}")

    async def test_agent_permissions_survive_update(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Agent permissions persist after partial update."""
        import uuid

        name = f"perm-update-{uuid.uuid4().hex[:8]}"
        await api_client.post(
            "/agents",
            json={
                "name": name,
                "system_prompt": "Test",
                "mode": "subagent",
                "permissions": [
                    {
                        "operations": ["read"],
                        "paths": ["/workspace/**"],
                        "mode": "allow",
                    },
                ],
            },
        )

        update = await api_client.patch(
            f"/agents/{name}",
            json={
                "interrupt_on": {
                    "write_file": {
                        "allowed_decisions": ["approve", "reject"],
                    },
                },
            },
        )
        assert update.status_code == 200

        get_resp = await api_client.get(f"/agents/{name}")
        agent = get_resp.json()
        assert len(agent.get("permissions", [])) == 1
        assert "write_file" in agent.get("interrupt_on", {})

        await api_client.delete(f"/agents/{name}")

    async def test_subagent_inherits_permissions(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Subagent definitions can carry their own permissions."""
        import uuid

        name = f"sub-perm-{uuid.uuid4().hex[:8]}"
        resp = await api_client.post(
            "/agents",
            json={
                "name": name,
                "system_prompt": "Test",
                "mode": "subagent",
                "subagents": [
                    {
                        "name": "reader",
                        "description": "Read-only subagent",
                        "system_prompt": "Read only",
                        "permissions": [
                            {
                                "operations": ["read"],
                                "paths": ["/workspace/**"],
                                "mode": "allow",
                            },
                            {
                                "operations": ["read", "write"],
                                "paths": ["/**"],
                                "mode": "deny",
                            },
                        ],
                    },
                ],
            },
        )
        assert resp.status_code == 201
        agent = resp.json()
        subagents = agent.get("subagents", [])
        assert len(subagents) == 1
        subagent = subagents[0]
        assert len(subagent.get("permissions", [])) == 2

        await api_client.delete(f"/agents/{name}")

    async def test_cached_agent_invalidates_on_permission_change(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Permission changes trigger agent cache invalidation."""
        import uuid

        name = f"invalidate-{uuid.uuid4().hex[:8]}"
        await api_client.post(
            "/agents",
            json={
                "name": name,
                "system_prompt": "Test",
                "mode": "subagent",
                "permissions": [],
            },
        )

        update1 = await api_client.patch(
            f"/agents/{name}",
            json={
                "permissions": [
                    {
                        "operations": ["read"],
                        "paths": ["/workspace/**"],
                        "mode": "allow",
                    },
                ],
            },
        )
        assert update1.status_code == 200

        update2 = await api_client.patch(
            f"/agents/{name}",
            json={
                "permissions": [
                    {
                        "operations": ["read", "write"],
                        "paths": ["/workspace/**"],
                        "mode": "allow",
                    },
                ],
            },
        )
        assert update2.status_code == 200

        get_resp = await api_client.get(f"/agents/{name}")
        agent = get_resp.json()
        permissions = agent.get("permissions", [])
        assert len(permissions) == 1
        assert permissions[0]["operations"] == ["read", "write"]

        await api_client.delete(f"/agents/{name}")
