"""Unit tests for ConfigRegistrySkillsBackend."""

from __future__ import annotations

import pytest

from server.app.agent.skills_backend import ConfigRegistrySkillsBackend
from server.app.storage.config_dispatcher import InProcessDispatcher
from server.app.storage.config_models import SkillDefinition
from server.app.storage.config_registry import MemoryConfigRegistry


@pytest.fixture
def registry():
    """Create a fresh in-memory registry for each test."""
    return MemoryConfigRegistry()


@pytest.fixture
def backend(registry):
    """Create a ConfigRegistrySkillsBackend with empty scope."""
    return ConfigRegistrySkillsBackend(registry=registry, scope={})


class TestAlsInfo:
    """Tests for the als_info method."""

    async def test_returns_empty_list_when_no_skills(self, backend):
        """Should return empty list when no skills exist."""
        result = await backend.als_info("/")
        assert result == []

    async def test_returns_skill_directories(self, registry, backend):
        """Should return skill directories for enabled skills."""
        skill = SkillDefinition(
            name="test-skill",
            path="/skills/api/test-skill/SKILL.md",
            enabled=True,
            content="# Test Skill",
        )
        await registry.upsert_skill(skill)

        result = await backend.als_info("/")
        assert len(result) == 1
        assert result[0]["path"] == "/test-skill/"
        assert result[0]["is_dir"] is True

    async def test_excludes_disabled_skills(self, registry, backend):
        """Should not return disabled skills."""
        enabled_skill = SkillDefinition(
            name="enabled-skill",
            path="/skills/api/enabled-skill/SKILL.md",
            enabled=True,
            content="# Enabled",
        )
        disabled_skill = SkillDefinition(
            name="disabled-skill",
            path="/skills/api/disabled-skill/SKILL.md",
            enabled=False,
            content="# Disabled",
        )
        await registry.upsert_skill(enabled_skill)
        await registry.upsert_skill(disabled_skill)

        result = await backend.als_info("/")
        assert len(result) == 1
        assert result[0]["path"] == "/enabled-skill/"

    async def test_returns_multiple_skills(self, registry, backend):
        """Should return all enabled skills."""
        for i in range(3):
            skill = SkillDefinition(
                name=f"skill-{i}",
                path=f"/skills/api/skill-{i}/SKILL.md",
                enabled=True,
                content=f"# Skill {i}",
            )
            await registry.upsert_skill(skill)

        result = await backend.als_info("/")
        assert len(result) == 3
        paths = {r["path"] for r in result}
        assert paths == {
            "/skill-0/",
            "/skill-1/",
            "/skill-2/",
        }


class TestAdownloadFiles:
    """Tests for the adownload_files method."""

    async def test_downloads_skill_content(self, registry, backend):
        """Should return skill content as bytes."""
        content = "---\nname: test-skill\n---\n\n# Test Skill\n\nInstructions here."
        skill = SkillDefinition(
            name="test-skill",
            path="/skills/api/test-skill/SKILL.md",
            enabled=True,
            content=content,
        )
        await registry.upsert_skill(skill)

        result = await backend.adownload_files(["/test-skill/SKILL.md"])
        assert len(result) == 1
        assert result[0].path == "/test-skill/SKILL.md"
        assert result[0].content == content.encode("utf-8")
        assert result[0].error is None

    async def test_returns_file_not_found_for_missing_skill(self, backend):
        """Should return file_not_found error for non-existent skill."""
        result = await backend.adownload_files(["/missing-skill/SKILL.md"])
        assert len(result) == 1
        assert result[0].path == "/missing-skill/SKILL.md"
        assert result[0].content is None
        assert result[0].error == "file_not_found"

    async def test_returns_file_not_found_for_disabled_skill(self, registry, backend):
        """Should return file_not_found for disabled skill."""
        skill = SkillDefinition(
            name="disabled-skill",
            path="/skills/api/disabled-skill/SKILL.md",
            enabled=False,
            content="# Disabled",
        )
        await registry.upsert_skill(skill)

        result = await backend.adownload_files(["/disabled-skill/SKILL.md"])
        assert result[0].error == "file_not_found"

    async def test_returns_invalid_path_for_malformed_paths(self, backend):
        """Should return invalid_path for non-SKILL.md paths."""
        result = await backend.adownload_files(["/some/other/file.txt"])
        assert result[0].error == "invalid_path"

    async def test_handles_empty_content(self, registry, backend):
        """Should handle skills with no content."""
        skill = SkillDefinition(
            name="empty-skill",
            path="/skills/api/empty-skill/SKILL.md",
            enabled=True,
            content=None,
        )
        await registry.upsert_skill(skill)

        result = await backend.adownload_files(["/empty-skill/SKILL.md"])
        assert result[0].content == b""
        assert result[0].error is None

    async def test_downloads_multiple_files(self, registry, backend):
        """Should handle multiple files in one call."""
        for name in ["skill-a", "skill-b"]:
            skill = SkillDefinition(
                name=name,
                path=f"/skills/api/{name}/SKILL.md",
                enabled=True,
                content=f"# {name}",
            )
            await registry.upsert_skill(skill)

        result = await backend.adownload_files(
            [
                "/skill-a/SKILL.md",
                "/skill-b/SKILL.md",
                "/missing/SKILL.md",
            ]
        )
        assert len(result) == 3
        assert result[0].content == b"# skill-a"
        assert result[1].content == b"# skill-b"
        assert result[2].error == "file_not_found"


class TestAread:
    """Tests for the aread method."""

    async def test_reads_skill_content_with_line_numbers(self, registry, backend):
        """Should return content with line numbers."""
        content = "Line 1\nLine 2\nLine 3"
        skill = SkillDefinition(
            name="test-skill",
            path="/skills/api/test-skill/SKILL.md",
            enabled=True,
            content=content,
        )
        await registry.upsert_skill(skill)

        result = await backend.aread("/test-skill/SKILL.md")
        lines = result.split("\n")
        assert len(lines) == 3
        assert lines[0].endswith("\tLine 1")
        assert lines[1].endswith("\tLine 2")
        assert lines[2].endswith("\tLine 3")
        # Check line numbers
        assert lines[0].startswith("     1")
        assert lines[1].startswith("     2")
        assert lines[2].startswith("     3")

    async def test_respects_offset_and_limit(self, registry, backend):
        """Should respect offset and limit parameters."""
        content = "Line 1\nLine 2\nLine 3\nLine 4\nLine 5"
        skill = SkillDefinition(
            name="test-skill",
            path="/skills/api/test-skill/SKILL.md",
            enabled=True,
            content=content,
        )
        await registry.upsert_skill(skill)

        result = await backend.aread("/test-skill/SKILL.md", offset=1, limit=2)
        lines = result.split("\n")
        assert len(lines) == 2
        assert "Line 2" in lines[0]
        assert "Line 3" in lines[1]
        # Line numbers should be 1-indexed
        assert lines[0].startswith("     2")
        assert lines[1].startswith("     3")

    async def test_returns_error_for_missing_skill(self, backend):
        """Should return error for non-existent skill."""
        result = await backend.aread("/missing/SKILL.md")
        assert "Error: Skill not found" in result

    async def test_returns_error_for_disabled_skill(self, registry, backend):
        """Should return error for disabled skill."""
        skill = SkillDefinition(
            name="disabled",
            path="/skills/api/disabled/SKILL.md",
            enabled=False,
            content="# Disabled",
        )
        await registry.upsert_skill(skill)

        result = await backend.aread("/disabled/SKILL.md")
        assert "Error: Skill not found" in result

    async def test_returns_error_for_invalid_path(self, backend):
        """Should return error for malformed path."""
        result = await backend.aread("/invalid/path.txt")
        assert "Error: Invalid path" in result


class TestScopeFiltering:
    """Tests for scope-aware skill filtering."""

    async def test_returns_only_skills_matching_scope(self, registry):
        """Should only return skills matching the backend's scope."""
        # Create global skill
        global_skill = SkillDefinition(
            name="global-skill",
            path="/skills/api/global-skill/SKILL.md",
            enabled=True,
            content="# Global",
            scope={},
        )
        await registry.upsert_skill(global_skill)

        # Create user-scoped skill
        user_skill = SkillDefinition(
            name="user-skill",
            path="/skills/api/user-skill/SKILL.md",
            enabled=True,
            content="# User",
            scope={"user": "alice"},
        )
        await registry.upsert_skill(user_skill)

        # Backend with alice's scope
        alice_backend = ConfigRegistrySkillsBackend(registry=registry, scope={"user": "alice"})

        result = await alice_backend.als_info("/")
        paths = {r["path"] for r in result}
        # Should see both global and alice's skill
        assert "/global-skill/" in paths
        assert "/user-skill/" in paths

        # Backend with bob's scope
        bob_backend = ConfigRegistrySkillsBackend(registry=registry, scope={"user": "bob"})

        result = await bob_backend.als_info("/")
        paths = {r["path"] for r in result}
        # Should only see global skill, not alice's
        assert "/global-skill/" in paths
        assert "/user-skill/" not in paths


class TestReadSync:
    """Tests for the synchronous read method."""

    def test_returns_error_message(self, backend):
        """Should return error directing to use async version."""
        result = backend.read("/test/SKILL.md")
        assert "Use aread()" in result
