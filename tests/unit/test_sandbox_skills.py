"""Native Deep Agents Skills discovery through the sandbox workspace contract."""

from __future__ import annotations

import pytest
from deepagents.middleware.skills import _alist_skills_with_errors

from server.app.agent.sandbox_backend import CognitionLocalSandboxBackend


@pytest.mark.asyncio
async def test_deep_agents_discovers_builder_mounted_workspace_skills(tmp_path) -> None:
    """A standard Skill bundle is discovered without a Cognition-specific backend."""
    skill = tmp_path / "skills" / "review"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: review\ndescription: Review code changes.\n---\n# Review\n",
        encoding="utf-8",
    )
    sandbox = CognitionLocalSandboxBackend(root_dir=tmp_path)

    discovered, source_error = await _alist_skills_with_errors(sandbox, sandbox.skills_root)

    assert source_error is None
    assert [item["name"] for item in discovered] == ["review"]


def test_local_sandbox_derives_skills_root_from_workspace(tmp_path) -> None:
    sandbox = CognitionLocalSandboxBackend(root_dir=tmp_path)

    assert sandbox.workspace_root == str(tmp_path.resolve())
    assert sandbox.skills_root == str(tmp_path.resolve() / "skills")
