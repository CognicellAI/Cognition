"""Tests for the Agent-owned Deep Agents skills backend."""

from __future__ import annotations

from typing import get_args

import pytest
from deepagents.backends.composite import CompositeBackend
from deepagents.middleware.skills import _alist_skills_with_errors

from server.app.agent.definition import AgentSkillBundle
from server.app.agent.skills_backend import AgentSkillsBackend
from server.app.storage.config_models import EntityType
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore


@pytest.fixture
def backend() -> AgentSkillsBackend:
    return AgentSkillsBackend(
        [
            AgentSkillBundle(
                name="review",
                content="---\nname: review\ndescription: Review code\n---\n\n# Review",
                files={"references/guide.md": "Supporting guidance"},
            )
        ]
    )


def test_lists_only_agent_owned_skill_directories(backend: AgentSkillsBackend) -> None:
    result = backend.ls("/")
    assert result.entries == [{"path": "/review/", "is_dir": True, "size": 0, "modified_at": ""}]


@pytest.mark.asyncio
async def test_downloads_primary_and_supporting_files(backend: AgentSkillsBackend) -> None:
    result = await backend.adownload_files(["/review/SKILL.md", "/review/references/guide.md"])
    assert result[0].content is not None
    assert b"# Review" in result[0].content
    assert result[1].content == b"Supporting guidance"


@pytest.mark.asyncio
async def test_missing_and_traversal_paths_are_redacted(backend: AgentSkillsBackend) -> None:
    result = await backend.adownload_files(["/missing/SKILL.md", "/review/../secret.txt"])
    assert result[0].error == "file_not_found"
    assert result[1].error == "invalid_path"


@pytest.mark.asyncio
async def test_deep_agents_discovers_skill_metadata_through_composite_backend(
    backend: AgentSkillsBackend,
) -> None:
    composite = CompositeBackend(default=backend, routes={"/skills/api/": backend})

    skills, source_error = await _alist_skills_with_errors(composite, "/skills/api/")

    assert source_error is None
    assert [skill["name"] for skill in skills] == ["review"]
    assert skills[0]["description"] == "Review code"


def test_reads_raw_content_with_bounds(backend: AgentSkillsBackend) -> None:
    result = backend.read("/review/SKILL.md", offset=1, limit=2)
    assert result.error is None
    assert result.file_data is not None
    assert result.file_data["content"].splitlines()[0] == "name: review"
    assert result.total_lines == 6
    assert result.next_offset == 3


def test_backend_copies_pinned_bundles() -> None:
    files = {"references/guide.md": "original"}
    skill = AgentSkillBundle(name="review", content="# Review", files=files)
    backend = AgentSkillsBackend([skill])
    files["references/guide.md"] = "mutated"

    result = backend.read("/review/references/guide.md")

    assert result.file_data is not None
    assert "original" in result.file_data["content"]


def test_standalone_skill_registry_and_api_are_removed() -> None:
    from server.app.main import app

    paths = {str(getattr(route, "path", "")) for route in app.routes}
    assert "/skills" not in paths
    assert "skill" not in get_args(EntityType)
    for target in (MemoryConfigRegistry, DefaultConfigStore):
        assert not hasattr(target, "get_skill")
        assert not hasattr(target, "list_skills")
        assert not hasattr(target, "upsert_skill")
        assert not hasattr(target, "delete_skill")
