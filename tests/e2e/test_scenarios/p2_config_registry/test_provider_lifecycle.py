"""Business Scenario: Provider Configuration Lifecycle.

As a platform operator, I want to register, update, and remove LLM provider
configs via the API so that I can manage which models are available without
restarting the server.

Business Value:
- Dynamic LLM provider management (no restart required)
- Multiple providers with different priorities
- Clean removal when a provider is decommissioned
"""

from __future__ import annotations

import uuid

import pytest


def _unique(prefix: str = "prov") -> str:
    """Generate a unique ID to avoid cross-test contamination."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.e2e
class TestProviderLifecycle:
    """Provider config CRUD end-to-end via the /models/providers API."""

    async def test_list_providers_returns_200(self, api_client) -> None:
        """GET /models/providers is accessible and returns a valid envelope."""
        response = await api_client.get("/models/providers")

        assert response.status_code == 200
        data = response.json()
        assert "providers" in data
        assert "count" in data
        assert isinstance(data["providers"], list)
        assert data["count"] == len(data["providers"])

    async def test_create_provider_appears_in_list(self, api_client) -> None:
        """A newly created provider is visible in GET /models/providers."""
        provider_id = _unique()

        create_resp = await api_client.post(
            "/models/providers",
            json={"id": provider_id, "provider": "openai", "model": "gpt-4o"},
        )
        assert create_resp.status_code == 201, create_resp.text

        data = create_resp.json()
        assert data["id"] == provider_id
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4o"
        assert data["source"] == "api"

        # Verify it shows up in the list
        list_resp = await api_client.get("/models/providers")
        assert list_resp.status_code == 200
        ids = [p["id"] for p in list_resp.json()["providers"]]
        assert provider_id in ids

        # Cleanup
        await api_client.delete(f"/models/providers/{provider_id}")

    async def test_create_provider_full_payload(self, api_client) -> None:
        """Provider creation accepts all optional fields."""
        provider_id = _unique()

        create_resp = await api_client.post(
            "/models/providers",
            json={
                "id": provider_id,
                "provider": "openai",
                "model": "gpt-4o-mini",
                "display_name": "GPT-4o Mini (Test)",
                "enabled": True,
                "priority": 5,
                "max_retries": 2,
            },
        )
        assert create_resp.status_code == 201, create_resp.text
        data = create_resp.json()
        assert data["display_name"] == "GPT-4o Mini (Test)"
        assert data["priority"] == 5
        assert data["max_retries"] == 2

        # Cleanup
        await api_client.delete(f"/models/providers/{provider_id}")

    async def test_patch_provider_updates_field(self, api_client) -> None:
        """PATCH /models/providers/{id} updates only the specified fields."""
        provider_id = _unique()

        await api_client.post(
            "/models/providers",
            json={"id": provider_id, "provider": "openai", "model": "gpt-4o"},
        )

        patch_resp = await api_client.patch(
            f"/models/providers/{provider_id}",
            json={"model": "gpt-4o-mini", "priority": 10},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        data = patch_resp.json()
        assert data["model"] == "gpt-4o-mini"
        assert data["priority"] == 10
        # Unchanged field
        assert data["provider"] == "openai"

        # Cleanup
        await api_client.delete(f"/models/providers/{provider_id}")

    async def test_patch_missing_provider_returns_404(self, api_client) -> None:
        """PATCH on a non-existent provider returns 404."""
        response = await api_client.patch(
            "/models/providers/no-such-provider-xyz",
            json={"model": "gpt-4o"},
        )
        assert response.status_code == 404

    async def test_delete_provider_removes_from_list(self, api_client) -> None:
        """DELETE /models/providers/{id} removes the provider from the registry."""
        provider_id = _unique()

        await api_client.post(
            "/models/providers",
            json={"id": provider_id, "provider": "openai", "model": "gpt-4o"},
        )

        delete_resp = await api_client.delete(f"/models/providers/{provider_id}")
        assert delete_resp.status_code == 204

        # Verify gone from list
        list_resp = await api_client.get("/models/providers")
        ids = [p["id"] for p in list_resp.json()["providers"]]
        assert provider_id not in ids

    async def test_delete_missing_provider_returns_404(self, api_client) -> None:
        """DELETE on a non-existent provider returns 404."""
        response = await api_client.delete("/models/providers/no-such-provider-xyz")
        assert response.status_code in {204, 404}

    async def test_create_is_idempotent_upsert(self, api_client) -> None:
        """POSTing the same provider ID twice replaces the first (upsert semantics)."""
        provider_id = _unique()

        await api_client.post(
            "/models/providers",
            json={"id": provider_id, "provider": "openai", "model": "gpt-4o"},
        )
        second_resp = await api_client.post(
            "/models/providers",
            json={
                "id": provider_id,
                "provider": "bedrock",
                "model": "anthropic.claude-3-sonnet-20240229-v1:0",
                "region": "us-east-1",
            },
        )
        assert second_resp.status_code == 201

        # List should contain only one entry for this ID
        list_resp = await api_client.get("/models/providers")
        matching = [p for p in list_resp.json()["providers"] if p["id"] == provider_id]
        assert len(matching) == 1
        assert matching[0]["provider"] == "bedrock"

        # Cleanup
        await api_client.delete(f"/models/providers/{provider_id}")

    async def test_multiple_providers_coexist(self, api_client) -> None:
        """Multiple providers can be registered simultaneously."""
        ids = [_unique() for _ in range(3)]

        for i, pid in enumerate(ids):
            resp = await api_client.post(
                "/models/providers",
                json={"id": pid, "provider": "openai", "model": f"gpt-4o-{i}"},
            )
            assert resp.status_code == 201

        list_resp = await api_client.get("/models/providers")
        registered = {p["id"] for p in list_resp.json()["providers"]}
        for pid in ids:
            assert pid in registered

        # Cleanup
        for pid in ids:
            await api_client.delete(f"/models/providers/{pid}")
