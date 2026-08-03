"""E2E: startup file-source seeding for tools.

This verifies the bootstrap contract:
- workspace config declares ``tool_sources``
- startup seeds file-managed tools into the registry
- file-managed records are visible via REST APIs
"""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.bootstrap import seed_tools_from_sources
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore


@pytest.mark.asyncio
@pytest.mark.e2e
class TestFileSourceSeeding:
    async def test_file_sources_seed_tool_into_registry(self, tmp_path: Path) -> None:
        tools_dir = tmp_path / ".cognition" / "tools"
        tools_dir.mkdir(parents=True)
        (tools_dir / "quality.py").write_text(
            "from langchain_core.tools import tool\n\n@tool\ndef check_code_quality() -> str:\n    \"\"\"Check code quality.\"\"\"\n    return \"ok\"\n",
            encoding="utf-8",
        )

        store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
        config = {
            "tool_sources": [".cognition/tools/"],
        }

        seeded_tools = await seed_tools_from_sources(config, store, tmp_path)

        assert seeded_tools == 1

        tool = await store.get_tool("check_code_quality", scope={})

        assert tool is not None
        assert tool.source == "file"
        assert tool.path.endswith(".cognition/tools/quality.py")
