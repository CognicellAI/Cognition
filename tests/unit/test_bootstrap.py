"""Unit tests for server.app.bootstrap.seed_providers_from_config().

Tests the config.yaml -> ConfigStore provider seeding logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from server.app.bootstrap import _infer_api_key_env, seed_providers_from_config


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
