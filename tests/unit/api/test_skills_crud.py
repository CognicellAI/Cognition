"""Unit tests for /skills API endpoints (CRUD)."""

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

    from server.app.storage.config_registry import MemoryConfigRegistry

    def_registry = initialize_agent_definition_registry(Path(tmpdir))
    config_registry = MemoryConfigRegistry()
    config_store = DefaultConfigStore(
        config_registry=config_registry,
        agent_definition_registry=def_registry,
    )
    set_config_store(config_store)
    yield


class TestListSkills:
    def test_list_returns_200(self):
        response = client.get("/skills")
        assert response.status_code == 200
        data = response.json()
        assert "skills" in data
        assert "count" in data

    def test_count_matches_skills_length(self):
        response = client.get("/skills")
        data = response.json()
        assert data["count"] == len(data["skills"])


class TestGetSkill:
    def test_get_missing_skill_returns_404(self):
        response = client.get("/skills/nonexistent-skill-xyz")
        assert response.status_code == 404

    def test_get_existing_skill(self):
        client.post(
            "/skills",
            json={"name": "test-get-skill", "path": ".cognition/skills/test.md"},
        )
        response = client.get("/skills/test-get-skill")
        assert response.status_code == 200
        data = response.json()
        assert data["name"] == "test-get-skill"


class TestCreateSkill:
    def test_create_skill_succeeds(self):
        payload = {
            "name": "test-create-skill",
            "path": ".cognition/skills/test-create.md",
            "enabled": True,
            "description": "A test skill",
        }
        response = client.post("/skills", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "test-create-skill"
        assert data["source"] == "api"

    def test_create_skill_minimal_payload(self):
        """Only name and path are required."""
        response = client.post(
            "/skills",
            json={"name": "minimal-skill", "path": "some/path.md"},
        )
        assert response.status_code == 201

    def test_create_skill_missing_name_returns_422(self):
        response = client.post("/skills", json={"path": "some/path.md"})
        assert response.status_code == 422

    def test_create_skill_missing_path_and_content_returns_400(self):
        """Without content, path is required."""
        response = client.post("/skills", json={"name": "no-path-skill"})
        assert response.status_code == 400

    def test_create_skill_with_content_auto_generates_path(self):
        """When content is provided, path is auto-generated."""
        response = client.post(
            "/skills",
            json={"name": "content-skill", "content": "# My Skill\n\nInstructions here."},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["path"] == "/skills/api/content-skill/SKILL.md"
        assert data["content"] == "# My Skill\n\nInstructions here."


class TestUpdateSkill:
    def test_put_skill_replaces_definition(self):
        client.post(
            "/skills",
            json={"name": "test-put-skill", "path": "original.md"},
        )
        response = client.put(
            "/skills/test-put-skill",
            json={"name": "test-put-skill", "path": "replaced.md"},
        )
        assert response.status_code == 200
        assert response.json()["path"] == "replaced.md"

    def test_put_missing_skill_returns_200(self):
        """PUT on a skill is upsert — creates it if it doesn't exist."""
        response = client.put(
            "/skills/ghost-skill",
            json={"name": "ghost-skill", "path": "x.md"},
        )
        # PUT is upsert for skills — creates-or-replaces, returns 200
        assert response.status_code == 200
        assert response.json()["name"] == "ghost-skill"

    def test_patch_skill_partial_update(self):
        client.post(
            "/skills",
            json={"name": "test-patch-skill", "path": "orig.md", "enabled": True},
        )
        response = client.patch("/skills/test-patch-skill", json={"enabled": False})
        assert response.status_code == 200
        assert response.json()["enabled"] is False

    def test_patch_missing_skill_returns_404(self):
        response = client.patch("/skills/no-such-skill-patch", json={"enabled": False})
        assert response.status_code == 404


class TestDeleteSkill:
    def test_delete_existing_skill(self):
        client.post(
            "/skills",
            json={"name": "test-delete-skill", "path": "delete.md"},
        )
        response = client.delete("/skills/test-delete-skill")
        assert response.status_code == 204

    def test_delete_missing_skill_returns_404(self):
        response = client.delete("/skills/ghost-skill-delete")
        assert response.status_code == 404
