"""Unit tests for server.app.bootstrap.seed_providers_from_config().

Tests the config.yaml → ConfigRegistry provider seeding logic.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from server.app.bootstrap import (
    _infer_api_key_env,
    seed_providers_from_config,
)

# ---------------------------------------------------------------------------
# _infer_api_key_env tests
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# seed_providers_from_config tests
# ---------------------------------------------------------------------------


class TestSeedProvidersFromConfig:
    """Tests for the main bootstrap function."""

    @pytest.mark.asyncio
    async def test_seeds_openai_compatible_provider(self) -> None:
        """A well-formed llm: section seeds a ProviderConfig with id='default'."""
        config = {
            "llm": {
                "provider": "openai_compatible",
                "model": "google/gemini-3-flash-preview",
                "base_url": "https://openrouter.ai/api/v1",
            }
        }

        mock_reg = AsyncMock()
        mock_reg.seed_if_absent = AsyncMock(return_value=True)

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            result = await seed_providers_from_config(config)

        assert result is True
        mock_reg.seed_if_absent.assert_called_once()

        call_kwargs = mock_reg.seed_if_absent.call_args
        assert call_kwargs.kwargs["entity_type"] == "provider"
        assert call_kwargs.kwargs["name"] == "default"
        assert call_kwargs.kwargs["scope"] == {}
        assert call_kwargs.kwargs["source"] == "file"

        definition = call_kwargs.kwargs["definition"]
        assert definition["id"] == "default"
        assert definition["provider"] == "openai_compatible"
        assert definition["model"] == "google/gemini-3-flash-preview"
        assert definition["base_url"] == "https://openrouter.ai/api/v1"
        assert definition["api_key_env"] == "COGNITION_OPENAI_COMPATIBLE_API_KEY"
        assert definition["enabled"] is True
        assert definition["priority"] == 0

    @pytest.mark.asyncio
    async def test_seeds_anthropic_provider(self) -> None:
        """Anthropic provider type infers ANTHROPIC_API_KEY."""
        config = {
            "llm": {
                "provider": "anthropic",
                "model": "claude-sonnet-4-6",
            }
        }

        mock_reg = AsyncMock()
        mock_reg.seed_if_absent = AsyncMock(return_value=True)

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            result = await seed_providers_from_config(config)

        assert result is True
        definition = mock_reg.seed_if_absent.call_args.kwargs["definition"]
        assert definition["provider"] == "anthropic"
        assert definition["api_key_env"] == "ANTHROPIC_API_KEY"

    @pytest.mark.asyncio
    async def test_seeds_bedrock_with_region_and_role(self) -> None:
        """Bedrock provider includes region and role_arn fields."""
        config = {
            "llm": {
                "provider": "bedrock",
                "model": "anthropic.claude-3-sonnet-20240229-v1:0",
                "region": "us-west-2",
                "role_arn": "arn:aws:iam::123456789012:role/bedrock-access",
            }
        }

        mock_reg = AsyncMock()
        mock_reg.seed_if_absent = AsyncMock(return_value=True)

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            result = await seed_providers_from_config(config)

        assert result is True
        definition = mock_reg.seed_if_absent.call_args.kwargs["definition"]
        assert definition["provider"] == "bedrock"
        assert definition["region"] == "us-west-2"
        assert definition["role_arn"] == "arn:aws:iam::123456789012:role/bedrock-access"
        assert "api_key_env" not in definition  # Bedrock uses IAM, no key

    @pytest.mark.asyncio
    async def test_custom_api_key_env_overrides_default(self) -> None:
        """Explicit api_key_env in config.yaml overrides the inferred default."""
        config = {
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
                "api_key_env": "MY_CUSTOM_OPENAI_KEY",
            }
        }

        mock_reg = AsyncMock()
        mock_reg.seed_if_absent = AsyncMock(return_value=True)

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            await seed_providers_from_config(config)

        definition = mock_reg.seed_if_absent.call_args.kwargs["definition"]
        assert definition["api_key_env"] == "MY_CUSTOM_OPENAI_KEY"

    @pytest.mark.asyncio
    async def test_skips_when_no_llm_section(self) -> None:
        """Returns False when config has no llm: section."""
        config = {"server": {"port": 8000}}

        result = await seed_providers_from_config(config)
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_when_llm_is_not_dict(self) -> None:
        """Returns False when llm: is a scalar instead of dict."""
        config = {"llm": "invalid"}

        result = await seed_providers_from_config(config)
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_when_provider_missing(self) -> None:
        """Returns False when llm.provider is missing."""
        config = {"llm": {"model": "gpt-4o"}}

        result = await seed_providers_from_config(config)
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_when_model_missing(self) -> None:
        """Returns False when llm.model is missing."""
        config = {"llm": {"provider": "openai"}}

        result = await seed_providers_from_config(config)
        assert result is False

    @pytest.mark.asyncio
    async def test_skips_mock_provider(self) -> None:
        """Returns False when provider is 'mock' — test-only provider."""
        config = {"llm": {"provider": "mock", "model": "gpt-4o"}}

        result = await seed_providers_from_config(config)
        assert result is False

    @pytest.mark.asyncio
    async def test_does_not_overwrite_existing_api_row(self) -> None:
        """seed_if_absent returns False → bootstrap was skipped (API row exists)."""
        config = {
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
            }
        }

        mock_reg = AsyncMock()
        mock_reg.seed_if_absent = AsyncMock(return_value=False)  # Already exists

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            result = await seed_providers_from_config(config)

        assert result is False
        mock_reg.seed_if_absent.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_registry_error_gracefully(self) -> None:
        """Returns False and logs warning when registry raises."""
        config = {
            "llm": {
                "provider": "openai",
                "model": "gpt-4o",
            }
        }

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            side_effect=RuntimeError("Registry not initialized"),
        ):
            result = await seed_providers_from_config(config)

        assert result is False

    @pytest.mark.asyncio
    async def test_empty_config(self) -> None:
        """Returns False on empty config dict."""
        result = await seed_providers_from_config({})
        assert result is False
