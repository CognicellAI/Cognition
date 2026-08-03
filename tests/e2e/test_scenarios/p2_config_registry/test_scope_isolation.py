"""Business Scenario: Scope Isolation in ConfigRegistry.

As a SaaS operator running Cognition for multiple tenants, I want config
entities (skills, providers) created under one user's scope to be invisible
to other users, so that tenant configurations stay isolated.

Business Value:
- Multi-tenant skill customisation without interference
- Per-user provider overrides that don't affect other users
- Global defaults visible to everyone while user overrides stay private

Scope is passed via the X-Cognition-Scope-User request header.
Tests are skipped if the server does not have scoping enabled.
"""

from __future__ import annotations

import uuid

import pytest


def _unique(prefix: str = "scoped") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _scoping_enabled(api_client) -> bool:
    """Return True if the live server has scoping enabled."""
    resp = await api_client.get("/config")
    if resp.status_code != 200:
        return False
    return resp.json().get("server", {}).get("scoping_enabled", False)


@pytest.mark.asyncio
@pytest.mark.e2e
class TestScopeIsolation:
    """Verify that scope headers isolate config entities per user."""

    async def test_unscoped_agent_create_rejected_when_scoping_enabled(self, api_client) -> None:
        """An Agent cannot be created without scope headers when scoping is enabled."""
        if not await _scoping_enabled(api_client):
            pytest.skip("Scoping not enabled on this server")

        name = _unique("global")

        # Create WITHOUT scope header — use raw client to bypass the
        # api_client's default scope_header set by setup_scoping().
        create_resp = await api_client.client.post(
            f"{api_client.base_url}/agents",
            json={"name": name, "system_prompt": "Scoped."},
            headers={"Content-Type": "application/json"},
        )
        assert create_resp.status_code == 403

    async def test_user_scoped_agent_invisible_to_other_user(self, api_client) -> None:
        """An Agent created under Alice's scope is not visible to Bob."""
        if not await _scoping_enabled(api_client):
            pytest.skip("Scoping not enabled on this server")

        name = _unique("alice-private")

        # Create as Alice
        alice_create = await api_client.client.post(
            f"{api_client.base_url}/agents",
            json={"name": name, "system_prompt": "Alice only."},
            headers={
                "X-Cognition-Scope-User": "alice-scope-test",
                "Content-Type": "application/json",
            },
        )
        assert alice_create.status_code == 201

        # Bob should NOT see it
        bob_get = await api_client.client.get(
            f"{api_client.base_url}/agents/{name}",
            headers={"X-Cognition-Scope-User": "bob-scope-test"},
        )
        assert bob_get.status_code == 404

        # Alice can see her own
        alice_get = await api_client.client.get(
            f"{api_client.base_url}/agents/{name}",
            headers={"X-Cognition-Scope-User": "alice-scope-test"},
        )
        assert alice_get.status_code == 200

        # Cleanup — delete as Alice
        await api_client.client.delete(
            f"{api_client.base_url}/agents/{name}",
            headers={"X-Cognition-Scope-User": "alice-scope-test"},
        )

    async def test_user_scoped_provider_invisible_to_other_user(self, api_client) -> None:
        """A provider created under Alice's scope is not visible when listed as Bob."""
        if not await _scoping_enabled(api_client):
            pytest.skip("Scoping not enabled on this server")

        provider_id = _unique("alice-prov")

        # Create as Alice
        alice_create = await api_client.client.post(
            f"{api_client.base_url}/models/providers",
            json={"id": provider_id, "provider": "openai", "model": "gpt-4o"},
            headers={
                "X-Cognition-Scope-User": "alice-scope-test",
                "Content-Type": "application/json",
            },
        )
        assert alice_create.status_code == 201

        # Bob's provider list should NOT include it
        bob_list = await api_client.client.get(
            f"{api_client.base_url}/models/providers",
            headers={"X-Cognition-Scope-User": "bob-scope-test"},
        )
        assert bob_list.status_code == 200
        bob_ids = [p["id"] for p in bob_list.json()["providers"]]
        assert provider_id not in bob_ids

        # Alice's provider list SHOULD include it
        alice_list = await api_client.client.get(
            f"{api_client.base_url}/models/providers",
            headers={"X-Cognition-Scope-User": "alice-scope-test"},
        )
        assert alice_list.status_code == 200
        alice_ids = [p["id"] for p in alice_list.json()["providers"]]
        assert provider_id in alice_ids

        # Cleanup
        await api_client.client.delete(
            f"{api_client.base_url}/models/providers/{provider_id}",
            headers={"X-Cognition-Scope-User": "alice-scope-test"},
        )

    async def test_scoped_entity_does_not_pollute_global_list(self, api_client) -> None:
        """User-scoped entities do not appear in the global (no-scope) list."""
        if not await _scoping_enabled(api_client):
            pytest.skip("Scoping not enabled on this server")

        name = _unique("scoped-agent")

        # Create as Alice
        await api_client.client.post(
            f"{api_client.base_url}/agents",
            json={"name": name, "system_prompt": "Scoped."},
            headers={
                "X-Cognition-Scope-User": "alice-scope-test",
                "Content-Type": "application/json",
            },
        )

        # Global list (no scope header) should not show it
        global_list = await api_client.get("/agents")
        global_names = [item["name"] for item in global_list.json()["agents"]]
        assert name not in global_names

        # Cleanup
        await api_client.client.delete(
            f"{api_client.base_url}/agents/{name}",
            headers={"X-Cognition-Scope-User": "alice-scope-test"},
        )
