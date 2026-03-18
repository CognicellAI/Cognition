"""Business Scenario: Skill Configuration Lifecycle.

As a platform engineer, I want to register, update, and remove agent skills
via the API so that I can extend agent capabilities without redeploying code.

Business Value:
- Extend agent knowledge domains without code changes
- Per-deployment skill customisation via API
- Clean removal of skills that are no longer relevant
"""

from __future__ import annotations

import uuid

import pytest


def _unique(prefix: str = "skill") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.e2e
class TestSkillLifecycle:
    """Skill CRUD end-to-end via the /skills API."""

    async def test_list_skills_returns_200(self, api_client) -> None:
        """GET /skills is accessible and returns a valid envelope."""
        response = await api_client.get("/skills")

        assert response.status_code == 200
        data = response.json()
        assert "skills" in data
        assert "count" in data
        assert isinstance(data["skills"], list)
        assert data["count"] == len(data["skills"])

    async def test_create_skill_appears_in_list(self, api_client) -> None:
        """A newly created skill is visible in GET /skills."""
        name = _unique()

        create_resp = await api_client.post(
            "/skills",
            json={"name": name, "path": f".cognition/skills/{name}.md"},
        )
        assert create_resp.status_code == 201, create_resp.text

        data = create_resp.json()
        assert data["name"] == name
        assert data["source"] == "api"

        # Verify it shows up in the list
        list_resp = await api_client.get("/skills")
        names = [s["name"] for s in list_resp.json()["skills"]]
        assert name in names

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_get_skill_by_name(self, api_client) -> None:
        """GET /skills/{name} returns the registered skill."""
        name = _unique()

        await api_client.post(
            "/skills",
            json={
                "name": name,
                "path": f".cognition/skills/{name}.md",
                "description": "Test skill for E2E",
            },
        )

        get_resp = await api_client.get(f"/skills/{name}")
        assert get_resp.status_code == 200, get_resp.text
        data = get_resp.json()
        assert data["name"] == name
        assert data["description"] == "Test skill for E2E"

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_get_missing_skill_returns_404(self, api_client) -> None:
        """GET /skills/{name} for an unknown skill returns 404."""
        response = await api_client.get("/skills/no-such-skill-xyz123")
        assert response.status_code == 404

    async def test_put_skill_replaces_definition(self, api_client) -> None:
        """PUT /skills/{name} does a full replace of the skill."""
        name = _unique()

        await api_client.post(
            "/skills",
            json={"name": name, "path": "original.md", "description": "Original"},
        )

        put_resp = await api_client.put(
            f"/skills/{name}",
            json={"name": name, "path": "replaced.md", "description": "Replaced"},
        )
        # PUT is upsert — returns 200 whether new or replaced
        assert put_resp.status_code == 200, put_resp.text
        data = put_resp.json()
        assert data["path"] == "replaced.md"
        assert data["description"] == "Replaced"

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_patch_skill_updates_description(self, api_client) -> None:
        """PATCH /skills/{name} updates only the specified fields."""
        name = _unique()

        await api_client.post(
            "/skills",
            json={"name": name, "path": "skill.md", "description": "Original description"},
        )

        patch_resp = await api_client.patch(
            f"/skills/{name}",
            json={"description": "Updated description"},
        )
        assert patch_resp.status_code == 200, patch_resp.text
        data = patch_resp.json()
        assert data["description"] == "Updated description"
        # Path unchanged
        assert data["path"] == "skill.md"

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_patch_missing_skill_returns_404(self, api_client) -> None:
        """PATCH on a non-existent skill returns 404."""
        response = await api_client.patch(
            "/skills/no-such-skill-xyz123",
            json={"description": "update"},
        )
        assert response.status_code == 404

    async def test_delete_skill_removes_from_list(self, api_client) -> None:
        """DELETE /skills/{name} removes the skill from the registry."""
        name = _unique()

        await api_client.post(
            "/skills",
            json={"name": name, "path": "skill.md"},
        )

        delete_resp = await api_client.delete(f"/skills/{name}")
        assert delete_resp.status_code == 204

        # Verify gone
        get_resp = await api_client.get(f"/skills/{name}")
        assert get_resp.status_code == 404

    async def test_delete_missing_skill_returns_404(self, api_client) -> None:
        """DELETE on a non-existent skill returns 404."""
        response = await api_client.delete("/skills/no-such-skill-xyz123")
        assert response.status_code == 404

    async def test_disabled_skill_is_persisted(self, api_client) -> None:
        """Creating a skill with enabled=False is persisted correctly."""
        name = _unique()

        await api_client.post(
            "/skills",
            json={"name": name, "path": "skill.md", "enabled": False},
        )

        get_resp = await api_client.get(f"/skills/{name}")
        assert get_resp.status_code == 200
        assert get_resp.json()["enabled"] is False

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_multiple_skills_coexist(self, api_client) -> None:
        """Multiple skills can be registered simultaneously."""
        names = [_unique() for _ in range(3)]

        for n in names:
            resp = await api_client.post(
                "/skills",
                json={"name": n, "path": f"{n}.md"},
            )
            assert resp.status_code == 201

        list_resp = await api_client.get("/skills")
        registered = {s["name"] for s in list_resp.json()["skills"]}
        for n in names:
            assert n in registered

        # Cleanup
        for n in names:
            await api_client.delete(f"/skills/{n}")
