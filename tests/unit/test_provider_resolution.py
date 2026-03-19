"""Unit tests for DeepAgentStreamingService._resolve_provider_config().

These tests verify that provider configuration is resolved correctly from
ConfigRegistry and session overrides, and that _build_model() calls
init_chat_model with the right arguments.

The fallback chain (ProviderFallbackChain) was removed. Provider resolution
now returns a single provider config — no silent fallback to the next
provider on failure. If the configured provider fails, the error surfaces
immediately to the caller.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.llm.deep_agent_service import DeepAgentStreamingService, _build_model
from server.app.models import Session, SessionConfig, SessionStatus

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _MockProviderConfig:
    """Minimal stand-in for storage.config_models.ProviderConfig."""

    def __init__(
        self,
        id: str = "prov-1",
        provider: str = "openai_compatible",
        model: str = "google/gemini-3-flash-preview",
        enabled: bool = True,
        priority: int = 0,
        api_key_env: str | None = "COGNITION_OPENAI_COMPATIBLE_API_KEY",
        base_url: str | None = "https://openrouter.ai/api/v1",
        region: str | None = None,
        role_arn: str | None = None,
    ) -> None:
        self.id = id
        self.provider = provider
        self.model = model
        self.enabled = enabled
        self.priority = priority
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.region = region
        self.role_arn = role_arn


class _MockGlobalDefaults:
    """Minimal stand-in for storage.config_models.GlobalProviderDefaults."""

    def __init__(
        self,
        provider: str = "openai_compatible",
        model: str = "google/gemini-3-flash-preview",
    ) -> None:
        self.provider = provider
        self.model = model


def _make_service() -> DeepAgentStreamingService:
    settings = MagicMock()
    settings.openai_api_key = None
    settings.openai_api_base = None
    settings.openai_compatible_api_key = MagicMock()
    settings.openai_compatible_api_key.get_secret_value.return_value = "sk-test"
    settings.openai_compatible_base_url = None
    settings.aws_region = "us-east-1"
    settings.aws_access_key_id = None
    settings.aws_secret_access_key = None
    settings.aws_session_token = None
    settings.bedrock_role_arn = None
    with patch("server.app.llm.deep_agent_service.create_storage_backend"):
        return DeepAgentStreamingService(settings=settings)


def _make_session(
    provider: str | None = None,
    model: str | None = None,
    provider_id: str | None = None,
) -> Session:
    return Session(
        id="sess-test",
        workspace_path="/tmp/ws",
        title="Test",
        thread_id="thread-test",
        status=SessionStatus.ACTIVE,
        config=SessionConfig(provider_id=provider_id, provider=provider, model=model),  # type: ignore[arg-type]
        created_at="2026-01-01T00:00:00",
        updated_at="2026-01-01T00:00:00",
    )


# ---------------------------------------------------------------------------
# _resolve_provider_config tests
# ---------------------------------------------------------------------------


class TestResolveProviderConfig:
    """_resolve_provider_config() returns the right (provider, model_id, ...) tuple."""

    @pytest.mark.asyncio
    async def test_session_override_takes_priority(self) -> None:
        """Session-level provider/model overrides bypass ConfigRegistry entirely."""
        service = _make_service()
        session = _make_session(provider="openai", model="gpt-4o")

        (
            provider,
            model_id,
            api_key,
            base_url,
            region,
            role_arn,
            _,
        ) = await service._resolve_provider_config(session=session, scope=None)

        assert provider == "openai"
        assert model_id == "gpt-4o"
        # No registry was consulted
        assert api_key is None

    @pytest.mark.asyncio
    async def test_individual_provider_from_registry(self) -> None:
        """First enabled ProviderConfig from ConfigRegistry is used."""
        service = _make_service()
        session = _make_session()  # no session-level override

        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(
            return_value=[
                _MockProviderConfig(
                    id="openrouter-1",
                    provider="openai_compatible",
                    model="google/gemini-3-flash-preview",
                    enabled=True,
                    priority=1,
                    api_key_env="COGNITION_OPENAI_COMPATIBLE_API_KEY",
                    base_url="https://openrouter.ai/api/v1",
                )
            ]
        )

        with (
            patch(
                "server.app.storage.config_registry.get_config_registry",
                return_value=mock_reg,
            ),
            patch.dict("os.environ", {"COGNITION_OPENAI_COMPATIBLE_API_KEY": "sk-test-key"}),
        ):
            (
                provider,
                model_id,
                api_key,
                base_url,
                region,
                role_arn,
                _,
            ) = await service._resolve_provider_config(session=session, scope=None)

        assert provider == "openai_compatible"
        assert model_id == "google/gemini-3-flash-preview"
        assert api_key == "sk-test-key"
        assert base_url == "https://openrouter.ai/api/v1"

    @pytest.mark.asyncio
    async def test_priority_ordering(self) -> None:
        """The provider with the lowest priority number is selected."""
        service = _make_service()
        session = _make_session()

        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(
            return_value=[
                _MockProviderConfig(id="high-cost", model="gpt-4o", priority=10),
                _MockProviderConfig(id="low-cost", model="gemini-3-flash-preview", priority=1),
                _MockProviderConfig(id="medium", model="llama-3.3-70b", priority=5),
            ]
        )

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            provider, model_id, *_ = await service._resolve_provider_config(
                session=session, scope=None
            )

        assert model_id == "gemini-3-flash-preview", (
            "Should pick the provider with priority=1 (lowest value = highest priority)"
        )

    @pytest.mark.asyncio
    async def test_disabled_providers_are_skipped(self) -> None:
        """Disabled providers are not selected even if they have the lowest priority."""
        service = _make_service()
        session = _make_session()

        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(
            return_value=[
                _MockProviderConfig(id="disabled", model="gpt-4o", priority=0, enabled=False),
                _MockProviderConfig(id="active", model="gemini-flash", priority=5, enabled=True),
            ]
        )
        mock_reg.get_global_provider_defaults = AsyncMock(
            return_value=_MockGlobalDefaults(model="global-default-model")
        )

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            provider, model_id, *_ = await service._resolve_provider_config(
                session=session, scope=None
            )

        assert model_id == "gemini-flash"

    @pytest.mark.asyncio
    async def test_raises_when_no_providers_configured(self) -> None:
        """When no individual providers are configured, LLMProviderConfigError is raised.

        GlobalProviderDefaults is no longer a fallback. Unscoped providers
        (scope={}) serve as globals — if none exist, it's a configuration error.
        """
        from server.app.exceptions import LLMProviderConfigError

        service = _make_service()
        session = _make_session()

        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(return_value=[])

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            with pytest.raises(LLMProviderConfigError, match="No provider configuration found"):
                await service._resolve_provider_config(session=session, scope=None)

    @pytest.mark.asyncio
    async def test_scope_passed_to_registry(self) -> None:
        """Scope dict is forwarded to list_providers()."""
        from server.app.exceptions import LLMProviderConfigError

        service = _make_service()
        session = _make_session()
        scope = {"user": "alice"}

        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(return_value=[])

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            with pytest.raises(LLMProviderConfigError):
                await service._resolve_provider_config(session=session, scope=scope)

        mock_reg.list_providers.assert_called_once_with(scope=scope)

    @pytest.mark.asyncio
    async def test_api_key_env_resolved_from_environment(self) -> None:
        """api_key_env is resolved from the process environment at call time."""
        service = _make_service()
        session = _make_session()

        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(
            return_value=[
                _MockProviderConfig(
                    api_key_env="MY_CUSTOM_API_KEY", base_url="https://api.example.com/v1"
                )
            ]
        )

        with (
            patch(
                "server.app.storage.config_registry.get_config_registry",
                return_value=mock_reg,
            ),
            patch.dict("os.environ", {"MY_CUSTOM_API_KEY": "resolved-key"}),
        ):
            *_, api_key, base_url, _, _, _ = await service._resolve_provider_config(
                session=session, scope=None
            )

        assert api_key == "resolved-key"

    @pytest.mark.asyncio
    async def test_error_when_no_providers_and_no_registry(self) -> None:
        """LLMProviderConfigError raised when ConfigRegistry is unavailable."""
        from server.app.exceptions import LLMProviderConfigError

        service = _make_service()
        session = _make_session()

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            side_effect=RuntimeError("Registry not initialized"),
        ):
            with pytest.raises(LLMProviderConfigError, match="No provider configuration"):
                await service._resolve_provider_config(session=session, scope=None)


# ---------------------------------------------------------------------------
# _resolve_model guard: mock provider rejected in production
# ---------------------------------------------------------------------------


class TestResolveModelMockGuard:
    """_resolve_model() must reject the mock provider in production contexts."""

    @pytest.mark.asyncio
    async def test_mock_provider_in_registry_raises_config_error(self) -> None:
        """If a ProviderConfig with provider='mock' is resolved, LLMProviderConfigError is raised."""
        from server.app.exceptions import LLMProviderConfigError

        service = _make_service()
        session = _make_session()

        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(
            return_value=[
                _MockProviderConfig(provider="mock", model="gpt-4o", enabled=True, priority=0)
            ]
        )

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            with pytest.raises(LLMProviderConfigError, match="reserved for testing"):
                await service._resolve_model(session=session, scope=None)


# ---------------------------------------------------------------------------
# _build_model tests (provider factory mapping)
# ---------------------------------------------------------------------------


class TestBuildModel:
    """_build_model() maps provider configs to the correct init_chat_model calls."""

    def _make_settings(self) -> Any:
        s = MagicMock()
        s.openai_api_key = None
        s.openai_api_base = None
        s.openai_compatible_api_key = MagicMock()
        s.openai_compatible_api_key.get_secret_value.return_value = "sk-compatible"
        s.openai_compatible_base_url = "https://openrouter.ai/api/v1"
        s.aws_region = "us-east-1"
        s.aws_access_key_id = None
        s.aws_secret_access_key = None
        s.aws_session_token = None
        s.bedrock_role_arn = None
        return s

    def test_openai_provider_calls_init_chat_model(self) -> None:
        """openai provider calls init_chat_model with model_provider='openai'."""
        settings = self._make_settings()
        settings.openai_api_key = MagicMock()
        settings.openai_api_key.get_secret_value.return_value = "sk-openai"

        with patch("server.app.llm.deep_agent_service.init_chat_model") as mock_init:
            _build_model("openai", "gpt-4o", None, None, None, None, settings)
            mock_init.assert_called_once()
            call_kwargs = mock_init.call_args
            assert call_kwargs[0][0] == "gpt-4o"
            assert call_kwargs[1]["model_provider"] == "openai"

    def test_openai_compatible_calls_init_chat_model(self) -> None:
        """openai_compatible calls init_chat_model with model_provider='openai' + base_url."""
        settings = self._make_settings()

        with patch("server.app.llm.deep_agent_service.init_chat_model") as mock_init:
            _build_model(
                "openai_compatible",
                "google/gemini-3-flash-preview",
                "sk-or-key",
                "https://openrouter.ai/api/v1",
                None,
                None,
                settings,
            )
            mock_init.assert_called_once()
            call_kwargs = mock_init.call_args
            assert call_kwargs[0][0] == "google/gemini-3-flash-preview"
            assert call_kwargs[1]["model_provider"] == "openai"
            assert call_kwargs[1]["base_url"] == "https://openrouter.ai/api/v1"
            assert call_kwargs[1]["api_key"] == "sk-or-key"

    def test_openai_compatible_raises_without_base_url(self) -> None:
        """openai_compatible without a base_url raises LLMProviderConfigError."""
        from server.app.exceptions import LLMProviderConfigError

        settings = self._make_settings()
        settings.openai_compatible_base_url = None  # No fallback either

        with pytest.raises(LLMProviderConfigError, match="base_url is required"):
            _build_model("openai_compatible", "some-model", None, None, None, None, settings)

    def test_anthropic_provider_calls_init_chat_model(self) -> None:
        """anthropic provider calls init_chat_model with model_provider='anthropic'."""
        settings = self._make_settings()

        with patch("server.app.llm.deep_agent_service.init_chat_model") as mock_init:
            _build_model("anthropic", "claude-sonnet-4-6", None, None, None, None, settings)
            call_kwargs = mock_init.call_args
            assert call_kwargs[0][0] == "claude-sonnet-4-6"
            assert call_kwargs[1]["model_provider"] == "anthropic"

    def test_unknown_provider_raises_config_error(self) -> None:
        """Completely unknown provider type raises LLMProviderConfigError."""
        from server.app.exceptions import LLMProviderConfigError

        settings = self._make_settings()

        with pytest.raises(LLMProviderConfigError, match="Unknown provider"):
            _build_model("mystic_cloud", "some-model", None, None, None, None, settings)

    def test_build_model_wraps_init_chat_model_errors(self) -> None:
        """Errors from init_chat_model are wrapped in LLMProviderConfigError."""
        from server.app.exceptions import LLMProviderConfigError

        settings = self._make_settings()

        with patch(
            "server.app.llm.deep_agent_service.init_chat_model",
            side_effect=ValueError("Bad API key"),
        ):
            with pytest.raises(LLMProviderConfigError, match="Bad API key"):
                _build_model("openai", "gpt-4o", "bad-key", None, None, None, settings)


# ---------------------------------------------------------------------------
# provider_id resolution tests
# ---------------------------------------------------------------------------


class TestProviderIdResolution:
    """_resolve_provider_config() with session.config.provider_id."""

    @pytest.mark.asyncio
    async def test_provider_id_takes_priority_over_provider(self) -> None:
        """provider_id overrides provider/model session override."""
        service = _make_service()
        session = _make_session(
            provider_id="my-openai-config",
            provider="anthropic",
            model="claude-sonnet-4-6",
        )

        mock_reg = MagicMock()
        mock_reg.get_provider = AsyncMock(
            return_value=_MockProviderConfig(
                id="my-openai-config",
                provider="openai",
                model="gpt-4o",
                enabled=True,
                api_key_env="OPENAI_API_KEY",
            )
        )

        with (
            patch(
                "server.app.storage.config_registry.get_config_registry",
                return_value=mock_reg,
            ),
            patch.dict("os.environ", {"OPENAI_API_KEY": "sk-test-key"}),
        ):
            (
                provider,
                model_id,
                api_key,
                *_rest,
            ) = await service._resolve_provider_config(session=session, scope=None)

        assert provider == "openai"
        assert model_id == "gpt-4o"
        assert api_key == "sk-test-key"
        # Registry was queried by ID, not list_providers
        mock_reg.get_provider.assert_called_once_with("my-openai-config", scope=None)

    @pytest.mark.asyncio
    async def test_provider_id_not_found_raises_error(self) -> None:
        """Unknown provider_id raises LLMProviderConfigError."""
        from server.app.exceptions import LLMProviderConfigError

        service = _make_service()
        session = _make_session(provider_id="nonexistent-config")

        mock_reg = MagicMock()
        mock_reg.get_provider = AsyncMock(return_value=None)

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            with pytest.raises(LLMProviderConfigError, match="not found"):
                await service._resolve_provider_config(session=session, scope=None)

    @pytest.mark.asyncio
    async def test_disabled_provider_id_raises_error(self) -> None:
        """Disabled provider_id raises LLMProviderConfigError."""
        from server.app.exceptions import LLMProviderConfigError

        service = _make_service()
        session = _make_session(provider_id="disabled-config")

        mock_reg = MagicMock()
        mock_reg.get_provider = AsyncMock(
            return_value=_MockProviderConfig(
                id="disabled-config",
                provider="openai",
                model="gpt-4o",
                enabled=False,
            )
        )

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            with pytest.raises(LLMProviderConfigError, match="disabled"):
                await service._resolve_provider_config(session=session, scope=None)

    @pytest.mark.asyncio
    async def test_provider_id_passes_scope(self) -> None:
        """Scope is forwarded to get_provider when using provider_id."""
        service = _make_service()
        session = _make_session(provider_id="scoped-config")
        scope = {"user": "alice", "project": "myapp"}

        mock_reg = MagicMock()
        mock_reg.get_provider = AsyncMock(
            return_value=_MockProviderConfig(
                id="scoped-config",
                provider="anthropic",
                model="claude-sonnet-4-6",
            )
        )

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            return_value=mock_reg,
        ):
            await service._resolve_provider_config(session=session, scope=scope)

        mock_reg.get_provider.assert_called_once_with("scoped-config", scope=scope)

    @pytest.mark.asyncio
    async def test_provider_id_with_unavailable_registry(self) -> None:
        """provider_id with unavailable ConfigRegistry raises LLMProviderConfigError."""
        from server.app.exceptions import LLMProviderConfigError

        service = _make_service()
        session = _make_session(provider_id="some-config")

        with patch(
            "server.app.storage.config_registry.get_config_registry",
            side_effect=RuntimeError("Registry not initialized"),
        ):
            with pytest.raises(LLMProviderConfigError, match="ConfigRegistry not initialized"):
                await service._resolve_provider_config(session=session, scope=None)
