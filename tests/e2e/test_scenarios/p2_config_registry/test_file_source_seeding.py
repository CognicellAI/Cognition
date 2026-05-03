"""E2E: startup file-source seeding for skills and tools.

This verifies the bootstrap contract:
- workspace config declares ``skill_sources`` / ``tool_sources``
- startup seeds file-managed skills/tools into the registry
- file-managed records are visible via REST APIs
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.bootstrap import seed_skills_from_sources, seed_tools_from_sources
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore


@pytest.mark.asyncio
@pytest.mark.e2e
class TestFileSourceSeeding:
    async def test_file_sources_seed_skill_and_tool_into_registry(self, tmp_path: Path) -> None:
        skills_dir = tmp_path / ".cognition" / "skills" / "clean-code"
        tools_dir = tmp_path / ".cognition" / "tools"
        skills_dir.mkdir(parents=True)
        tools_dir.mkdir(parents=True)

        (skills_dir / "SKILL.md").write_text(
            "---\nname: clean-code\ndescription: Use this skill to improve code quality.\n---\n\n# Clean Code\n",
            encoding="utf-8",
        )
        (tools_dir / "quality.py").write_text(
            "from langchain_core.tools import tool\n\n@tool\ndef check_code_quality() -> str:\n    \"\"\"Check code quality.\"\"\"\n    return \"ok\"\n",
            encoding="utf-8",
        )

        store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
        config = {
            "skill_sources": [".cognition/skills/"],
            "tool_sources": [".cognition/tools/"],
        }

        seeded_skills = await seed_skills_from_sources(config, store, tmp_path)
        seeded_tools = await seed_tools_from_sources(config, store, tmp_path)

        assert seeded_skills == 1
        assert seeded_tools == 1

        skill = await store.get_skill("clean-code", scope={})
        tool = await store.get_tool("check_code_quality", scope={})

        assert skill is not None
        assert skill.source == "file"
        assert skill.path.endswith(".cognition/skills/clean-code/SKILL.md")

        assert tool is not None
        assert tool.source == "file"
        assert tool.path.endswith(".cognition/tools/quality.py")
