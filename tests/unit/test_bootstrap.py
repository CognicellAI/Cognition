"""Unit tests for server.app.bootstrap config seeding helpers.

Tests the config.yaml -> ConfigStore provider seeding logic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from server.app.bootstrap import (
    _infer_api_key_env,
    seed_providers_from_config,
    seed_skills_from_sources,
    seed_tools_from_sources,
)
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore


class TestInferApiKeyEnv:
    """Tests for the static api_key_env mapping."""

    def test_openai(self) -> None:
        assert _infer_api_key_env("openai") == "OPENAI_API_KEY"

    def test_anthropic(self) -> None:
        assert _infer_api_key_env("anthropic") == "ANTHROPIC_API_KEY"

    def test_openai_compatible(self) -> None:
        assert _infer_api_key_env("openai_compatible") == "COGNITION_OPENAI_COMPATIBLE_API_KEY"

    def test_google_genai(self) -> None:
        assert _infer_api_key_env("google_genai") == "GOOGLE_API_KEY"

    def test_bedrock_returns_none(self) -> None:
        assert _infer_api_key_env("bedrock") is None

    def test_google_vertexai_returns_none(self) -> None:
        assert _infer_api_key_env("google_vertexai") is None

    def test_unknown_returns_none(self) -> None:
        assert _infer_api_key_env("totally_unknown") is None


class TestSeedProvidersFromConfig:
    """Tests for the main bootstrap function."""

    @staticmethod
    def _store(inserted: bool = True) -> AsyncMock:
        store = AsyncMock()
        store.seed_if_absent = AsyncMock(return_value=inserted)
        return store

    @pytest.mark.asyncio
    async def test_seeds_openai_compatible_provider(self) -> None:
        config = {
            "llm": {
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
                "base_url": "https://openrouter.ai/api/v1",
            }
        }

        store = self._store()
        result = await seed_providers_from_config(config, store)

        assert result is True
        store.seed_if_absent.assert_called_once()
        call_kwargs = store.seed_if_absent.call_args.kwargs
        assert call_kwargs["entity_type"] == "provider"
        assert call_kwargs["name"] == "default"
        assert call_kwargs["scope"] == {}
        assert call_kwargs["source"] == "file"

        definition = call_kwargs["definition"]
        assert definition["id"] == "default"
        assert definition["provider"] == "openai_compatible"
        assert definition["model"] == "google/gemini-3-flash-preview"
        assert definition["base_url"] == "https://openrouter.ai/api/v1"
        assert definition["api_key_env"] == "COGNITION_OPENAI_COMPATIBLE_API_KEY"
        assert definition["enabled"] is True
        assert definition["priority"] == 0

    @pytest.mark.asyncio
    async def test_seeds_anthropic_provider(self) -> None:
        config = {"llm": {"provider": "anthropic", "model": "claude-sonnet-4-6"}}

        store = self._store()
        result = await seed_providers_from_config(config, store)

        assert result is True
        definition = store.seed_if_absent.call_args.kwargs["definition"]
        assert definition["provider"] == "anthropic"
        assert definition["api_key_env"] == "ANTHROPIC_API_KEY"

    @pytest.mark.asyncio
    async def test_seeds_bedrock_with_region_and_role(self) -> None:
        config = {
            "llm": {
                "provider": "bedrock",
                "model": "anthropic.claude-3-sonnet-20240229-v1:0",
                "region": "us-west-2",
                "role_arn": "arn:aws:iam::123456789012:role/bedrock-access",
            }
        }

        store = self._store()
        result = await seed_providers_from_config(config, store)

        assert result is True
        definition = store.seed_if_absent.call_args.kwargs["definition"]
        assert definition["provider"] == "bedrock"
        assert definition["region"] == "us-west-2"
        assert definition["role_arn"] == "arn:aws:iam::123456789012:role/bedrock-access"
        assert "api_key_env" not in definition

    @pytest.mark.asyncio
    async def test_custom_api_key_env_overrides_default(self) -> None:
        config = {
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key_env": "MY_CUSTOM_OPENAI_KEY",
            }
        }

        store = self._store()
        await seed_providers_from_config(config, store)

        definition = store.seed_if_absent.call_args.kwargs["definition"]
        assert definition["api_key_env"] == "MY_CUSTOM_OPENAI_KEY"

    @pytest.mark.asyncio
    async def test_skips_when_no_llm_section(self) -> None:
        assert await seed_providers_from_config({"server": {"port": 8000}}, self._store()) is False

    @pytest.mark.asyncio
    async def test_skips_when_llm_is_not_dict(self) -> None:
        assert await seed_providers_from_config({"llm": "invalid"}, self._store()) is False

    @pytest.mark.asyncio
    async def test_skips_when_provider_missing(self) -> None:
        assert (
            await seed_providers_from_config({"llm": {"model": "gpt-4o"}}, self._store()) is False
        )

    @pytest.mark.asyncio
    async def test_skips_when_model_missing(self) -> None:
        assert (
            await seed_providers_from_config({"llm": {"provider": "openai"}}, self._store())
            is False
        )

    @pytest.mark.asyncio
    async def test_skips_mock_provider(self) -> None:
        assert (
            await seed_providers_from_config(
                {"llm": {"provider": "mock", "model": "gpt-4o"}},
                self._store(),
            )
            is False
        )

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_api_row(self) -> None:
        config = {"llm": {"provider": "openai", "model": "gpt-4o"}}
        store = self._store(inserted=False)

        result = await seed_providers_from_config(config, store)

        assert result is False
        store.seed_if_absent.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_registry_error_gracefully(self) -> None:
        config = {"llm": {"provider": "openai", "model": "gpt-4o"}}
        store = self._store()
        store.seed_if_absent = AsyncMock(side_effect=RuntimeError("store down"))

        result = await seed_providers_from_config(config, store)

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_config(self) -> None:
        assert await seed_providers_from_config({}, self._store()) is False


class TestSeedSkillsFromSources:
    @pytest.mark.asyncio
    async def test_seeds_skill_from_configured_source(self, tmp_path: Path) -> None:
        source_dir = tmp_path / ".cognition" / "skills" / "clean-code"
        source_dir.mkdir(parents=True)
        (source_dir / "SKILL.md").write_text(
            "---\nname: clean-code\ndescription: Use this skill for clean code.\n---\n\n# Clean Code\n",
            encoding="utf-8",
        )

        store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
        inserted = await seed_skills_from_sources(
            {"skill_sources": [".cognition/skills/"]},
            store,
            tmp_path,
        )

        assert inserted == 1
        skill = await store.get_skill("clean-code", scope={})
        assert skill is not None
        assert skill.source == "file"
        assert skill.description == "Use this skill for clean code."

    @pytest.mark.asyncio
    async def test_does_not_override_api_skill(self, tmp_path: Path) -> None:
        source_dir = tmp_path / ".cognition" / "skills" / "clean-code"
        source_dir.mkdir(parents=True)
        (source_dir / "SKILL.md").write_text(
            "---\nname: clean-code\ndescription: File description\n---\n\n# Clean Code\n",
            encoding="utf-8",
        )

        store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
        await store.upsert_skill_from_dict(
            {
                "name": "clean-code",
                "path": "/skills/api/clean-code/SKILL.md",
                "enabled": True,
                "description": "API description",
                "content": "# API skill",
                "scope": {},
                "source": "api",
            }
        )

        inserted = await seed_skills_from_sources(
            {"skill_sources": [".cognition/skills/"]},
            store,
            tmp_path,
        )

        assert inserted == 0
        skill = await store.get_skill("clean-code", scope={})
        assert skill is not None
        assert skill.source == "api"
        assert skill.description == "API description"


class TestSeedToolsFromSources:
    @pytest.mark.asyncio
    async def test_seeds_tool_from_configured_source(self, tmp_path: Path) -> None:
        source_dir = tmp_path / ".cognition" / "tools"
        source_dir.mkdir(parents=True)
        (source_dir / "directorate.py").write_text(
            "from langchain_core.tools import tool\n\n@tool\ndef directorate_get_change_set_context() -> str:\n    \"\"\"Get change set context.\"\"\"\n    return \"ok\"\n",
            encoding="utf-8",
        )

        store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
        inserted = await seed_tools_from_sources(
            {"tool_sources": [".cognition/tools/"]},
            store,
            tmp_path,
        )

        assert inserted == 1
        tool = await store.get_tool("directorate_get_change_set_context", scope={})
        assert tool is not None
        assert tool.source == "file"

    @pytest.mark.asyncio
    async def test_does_not_override_api_tool(self, tmp_path: Path) -> None:
        source_dir = tmp_path / ".cognition" / "tools"
        source_dir.mkdir(parents=True)
        (source_dir / "directorate.py").write_text(
            "from langchain_core.tools import tool\n\n@tool\ndef directorate_get_change_set_context() -> str:\n    \"\"\"File tool\"\"\"\n    return \"ok\"\n",
            encoding="utf-8",
        )

        store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
        await store.upsert_tool_from_dict(
            {
                "name": "directorate_get_change_set_context",
                "path": "server.app.tools.test_tool",
                "code": None,
                "enabled": True,
                "description": "API tool",
                "interrupt_on": False,
                "scope": {},
                "source": "api",
            }
        )

        inserted = await seed_tools_from_sources(
            {"tool_sources": [".cognition/tools/"]},
            store,
            tmp_path,
        )

        assert inserted == 0
        tool = await store.get_tool("directorate_get_change_set_context", scope={})
        assert tool is not None
        assert tool.source == "api"
