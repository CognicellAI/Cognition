"""Unit tests for /models/providers API endpoints (CRUD)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.agent.agent_definition_registry import initialize_agent_definition_registry
from server.app.api.dependencies import set_config_store
from server.app.main import app
from server.app.storage.config_store import DefaultConfigStore

client = TestClient(app)


@pytest.fixture(scope="module", autouse=True)
def setup_registry(tmp_path_factory):
    tmpdir = tmp_path_factory.mktemp("workspace")
    from pathlib import Path

    from server.app.storage.config_registry import MemoryConfigRegistry, set_config_registry

    def_registry = initialize_agent_definition_registry(Path(tmpdir))
    config_registry = MemoryConfigRegistry()
    set_config_registry(config_registry)
    config_store = DefaultConfigStore(
        config_registry=config_registry,
        agent_definition_registry=def_registry,
    )
    set_config_store(config_store)
    yield


class TestListProviders:
    def test_list_returns_200(self):
        response = client.get("/models/providers")
        assert response.status_code == 200

    def test_list_response_shape(self):
        response = client.get("/models/providers")
        data = response.json()
        assert "providers" in data
        assert "count" in data
        assert isinstance(data["providers"], list)

    def test_count_matches_providers_length(self):
        response = client.get("/models/providers")
        data = response.json()
        assert data["count"] == len(data["providers"])


class TestCreateProvider:
    def test_create_provider_succeeds(self):
        payload = {
            "id": "test-provider-create",
            "provider": "openai",
            "model": "gpt-4o",
        }
        response = client.post("/models/providers", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["id"] == "test-provider-create"
        assert data["provider"] == "openai"
        assert data["model"] == "gpt-4o"

    def test_create_provider_with_all_fields(self):
        payload = {
            "id": "test-provider-full",
            "provider": "bedrock",
            "model": "claude-3-sonnet",
            "display_name": "Claude 3 Sonnet",
            "enabled": True,
            "priority": 1,
            "max_retries": 3,
            "timeout": 30,
            "api_key_env": "AWS_ACCESS_KEY_ID",
            "region": "us-east-1",
            "role_arn": "arn:aws:iam::123456789:role/TestRole",
            "extra": {},
            "scope": {},
        }
        response = client.post("/models/providers", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["model"] == "claude-3-sonnet"
        assert data["display_name"] == "Claude 3 Sonnet"
        assert data["region"] == "us-east-1"
        assert data["timeout"] == 30

    def test_create_provider_upserts_on_duplicate(self):
        """Creating a provider that already exists overwrites it (upsert semantics)."""
        payload = {"id": "test-provider-dup", "provider": "openai", "model": "gpt-4"}
        client.post("/models/providers", json=payload)
        payload["model"] = "gpt-4o"
        response = client.post("/models/providers", json=payload)
        assert response.status_code == 201
        assert response.json()["model"] == "gpt-4o"

    def test_create_provider_missing_id_returns_422(self):
        response = client.post("/models/providers", json={"provider": "openai", "model": "gpt-4"})
        assert response.status_code == 422

    def test_create_provider_missing_provider_returns_422(self):
        response = client.post("/models/providers", json={"id": "x", "model": "gpt-4"})
        assert response.status_code == 422

    def test_create_provider_missing_model_returns_422(self):
        response = client.post("/models/providers", json={"id": "x", "provider": "openai"})
        assert response.status_code == 422


class TestUpdateProvider:
    def test_patch_provider_partial_update(self):
        """PATCH should update only the supplied fields."""
        client.post(
            "/models/providers",
            json={"id": "test-provider-patch", "provider": "openai", "model": "gpt-3.5-turbo"},
        )
        response = client.patch(
            "/models/providers/test-provider-patch",
            json={"model": "gpt-4o", "enabled": False},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["model"] == "gpt-4o"
        assert data["enabled"] is False
        # Unchanged fields preserved
        assert data["provider"] == "openai"

    def test_patch_missing_provider_returns_404(self):
        response = client.patch(
            "/models/providers/no-such-provider-xyz",
            json={"model": "gpt-4"},
        )
        assert response.status_code == 404

    def test_patch_provider_priority(self):
        client.post(
            "/models/providers",
            json={"id": "test-provider-priority", "provider": "openai", "model": "gpt-4"},
        )
        response = client.patch(
            "/models/providers/test-provider-priority",
            json={"priority": 5},
        )
        assert response.status_code == 200
        assert response.json()["priority"] == 5


class TestDeleteProvider:
    def test_delete_existing_provider(self):
        client.post(
            "/models/providers",
            json={"id": "test-provider-delete", "provider": "openai", "model": "gpt-4"},
        )
        response = client.delete("/models/providers/test-provider-delete")
        assert response.status_code == 204

    def test_delete_missing_provider_returns_404(self):
        response = client.delete("/models/providers/no-such-provider-delete-xyz")
        assert response.status_code == 404

    def test_deleted_provider_not_in_list(self):
        """After deletion the provider should not appear in list."""
        client.post(
            "/models/providers",
            json={"id": "test-provider-gone", "provider": "openai", "model": "gpt-4"},
        )
        client.delete("/models/providers/test-provider-gone")
        response = client.get("/models/providers")
        ids = [p["id"] for p in response.json()["providers"]]
        assert "test-provider-gone" not in ids
