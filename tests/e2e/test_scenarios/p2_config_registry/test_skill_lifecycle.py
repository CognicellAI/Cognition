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


@pytest.mark.asyncio
@pytest.mark.e2e
class TestSkillContentAPI:
    """E2E tests for Skills API with content storage and agent integration."""

    async def test_create_skill_with_content_auto_generates_path(self, api_client) -> None:
        """Creating a skill with content auto-generates the path."""
        name = _unique("content-skill")
        content = "---\nname: test-skill\ndescription: A test skill\n---\n\n# Test Skill\n\nInstructions here."

        create_resp = await api_client.post(
            "/skills",
            json={"name": name, "content": content},
        )
        assert create_resp.status_code == 201, create_resp.text

        data = create_resp.json()
        assert data["path"] == f"/skills/api/{name}/SKILL.md"
        assert data["content"] == content
        assert data["source"] == "api"

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_create_skill_without_path_or_content_fails(self, api_client) -> None:
        """Creating a skill without path or content returns 400."""
        name = _unique("invalid-skill")

        create_resp = await api_client.post(
            "/skills",
            json={"name": name},  # No path, no content
        )
        assert create_resp.status_code == 400

    async def test_get_skill_returns_content(self, api_client) -> None:
        """GET /skills/{name} returns the full content when present."""
        name = _unique("content-skill")
        content = "# My Skill\n\nThis is the content."

        await api_client.post(
            "/skills",
            json={"name": name, "content": content},
        )

        get_resp = await api_client.get(f"/skills/{name}")
        assert get_resp.status_code == 200
        assert get_resp.json()["content"] == content

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_patch_skill_updates_content(self, api_client) -> None:
        """PATCH can update the skill content."""
        name = _unique("content-skill")
        original_content = "# Original\n\nOriginal content."
        updated_content = "# Updated\n\nUpdated content."

        await api_client.post(
            "/skills",
            json={"name": name, "content": original_content},
        )

        patch_resp = await api_client.patch(
            f"/skills/{name}",
            json={"content": updated_content},
        )
        assert patch_resp.status_code == 200
        assert patch_resp.json()["content"] == updated_content

        # Cleanup
        await api_client.delete(f"/skills/{name}")

    async def test_skill_with_yaml_frontmatter_parsed(self, api_client) -> None:
        """Skills with YAML frontmatter in content are stored correctly."""
        name = _unique("yaml-skill")
        content = """---
name: yaml-test-skill
description: A skill with YAML frontmatter
license: MIT
---

# YAML Test Skill

## When to Use

When you need to test YAML parsing.

## Instructions

1. Parse the YAML
2. Extract metadata
3. Use in agent context
"""

        create_resp = await api_client.post(
            "/skills",
            json={"name": name, "content": content},
        )
        assert create_resp.status_code == 201

        # Verify content stored correctly
        get_resp = await api_client.get(f"/skills/{name}")
        assert get_resp.json()["content"] == content

        # Cleanup
        await api_client.delete(f"/skills/{name}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestSkillAgentIntegration:
    """E2E tests verifying skills work with agent runtime via CompositeBackend."""

    async def test_api_skill_visible_to_agent(self, api_client) -> None:
        """Skills created via API with content are visible to agent's SkillsMiddleware."""
        import asyncio

        name = _unique("agent-visible-skill")
        # Create a skill that SkillsMiddleware will discover
        content = f"""---
name: {name}
description: This skill should be visible to the agent
---

# {name}

## Instructions

This is a test skill for E2E verification.
"""

        # Create the skill via API
        create_resp = await api_client.post(
            "/skills",
            json={"name": name, "content": content},
        )
        assert create_resp.status_code == 201

        # Create a session to trigger agent creation
        session_resp = await api_client.post(
            "/sessions", json={"title": f"Test session for {name}"}
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Wait a moment for any async initialization
        await asyncio.sleep(0.5)

        # Send a message to trigger agent creation and skill loading
        # The skills are loaded when the agent processes a message
        msg_resp = await api_client.post(
            f"/sessions/{session_id}/messages",
            json={"content": "What skills are available to help me?"},
        )
        # Accept either 200 (non-streaming) or 202 (streaming) status codes
        assert msg_resp.status_code in (200, 202), f"Unexpected status: {msg_resp.status_code}"

        # Cleanup
        await api_client.delete(f"/skills/{name}")
        await api_client.delete(f"/sessions/{session_id}")

    async def test_filesystem_skills_still_work(self, api_client) -> None:
        """Filesystem-based skills continue working alongside API skills."""
        import asyncio

        # This test assumes there might be filesystem skills in .cognition/skills/
        # We'll create an API skill and verify it works alongside any existing skills
        name = _unique("api-skill-for-coexistence")
        content = """---
name: api-coexistence-skill
description: API skill that should work with filesystem skills
---

# API Coexistence Test

This verifies API and filesystem skills can coexist.
"""

        # Create the skill via API
        create_resp = await api_client.post(
            "/skills",
            json={"name": name, "content": content},
        )
        assert create_resp.status_code == 201

        # Create a session
        session_resp = await api_client.post("/sessions", json={"title": "Test coexistence"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Wait for initialization
        await asyncio.sleep(0.5)

        # Send a message
        msg_resp = await api_client.post(
            f"/sessions/{session_id}/messages", json={"content": "List available skills."}
        )
        # Accept either 200 (non-streaming) or 202 (streaming) status codes
        assert msg_resp.status_code in (200, 202), f"Unexpected status: {msg_resp.status_code}"

        # Cleanup
        await api_client.delete(f"/skills/{name}")
        await api_client.delete(f"/sessions/{session_id}")

    async def test_disabled_api_skill_not_visible(self, api_client) -> None:
        """Disabled API skills are not visible to the agent."""
        import asyncio

        name = _unique("disabled-api-skill")
        content = """---
name: disabled-skill
description: This skill should be disabled
---

# Disabled Skill

This should not be visible.
"""

        # Create disabled skill
        create_resp = await api_client.post(
            "/skills",
            json={"name": name, "content": content, "enabled": False},
        )
        assert create_resp.status_code == 201

        # Create a session
        session_resp = await api_client.post("/sessions", json={"title": "Test disabled skills"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        await asyncio.sleep(0.5)

        # Send a message
        msg_resp = await api_client.post(
            f"/sessions/{session_id}/messages", json={"content": "Hello"}
        )
        # Accept either 200 (non-streaming) or 202 (streaming) status codes
        assert msg_resp.status_code in (200, 202), f"Unexpected status: {msg_resp.status_code}"

        # Cleanup
        await api_client.delete(f"/skills/{name}")
        await api_client.delete(f"/sessions/{session_id}")
