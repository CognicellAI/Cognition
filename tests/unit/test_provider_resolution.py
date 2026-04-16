"""Unit tests for the canonical RuntimeResolver model pipeline."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.agent.resolver import RuntimeResolver
from server.app.models import Session, SessionConfig, SessionStatus


class _MockProviderConfig:
    def __init__(
        self,
        id: str = "prov-1",
        provider: str = "openai_compatible",
        model: str = "google/gemini-3-flash-preview",
        enabled: bool = True,
        priority: int = 0,
        max_retries: int = 2,
        timeout: int | None = None,
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
        self.max_retries = max_retries
        self.timeout = timeout
        self.api_key_env = api_key_env
        self.base_url = base_url
        self.region = region
        self.role_arn = role_arn


def _make_resolver(store: Any = None) -> RuntimeResolver:
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
    return RuntimeResolver(config_store=store, settings=settings)


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


class TestSelectModelTarget:
    @pytest.mark.asyncio
    async def test_session_provider_override_takes_priority(self) -> None:
        resolver = _make_resolver()
        session = _make_session(provider="openai", model="gpt-4o")

        target = await resolver.select_model_target_for_session(session=session, scope=None)

        assert target.provider == "openai"
        assert target.model_id == "gpt-4o"
        assert target.provider_id is None

    @pytest.mark.asyncio
    async def test_provider_id_takes_priority(self) -> None:
        session = _make_session(
            provider_id="my-openai-config", provider="anthropic", model="claude"
        )
        mock_reg = MagicMock()
        mock_reg.get_provider = AsyncMock(
            return_value=_MockProviderConfig(
                id="my-openai-config",
                provider="openai",
                model="gpt-4o",
                api_key_env="OPENAI_API_KEY",
            )
        )
        resolver = _make_resolver(store=mock_reg)

        target = await resolver.select_model_target_for_session(session=session, scope=None)

        assert target.provider == "openai"
        assert target.model_id == "gpt-4o"
        assert target.provider_id == "my-openai-config"

    @pytest.mark.asyncio
    async def test_agent_override_is_direct_target(self) -> None:
        from server.app.agent.definition import AgentConfig, AgentDefinition

        session = _make_session()
        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            config=AgentConfig(provider="anthropic", model="claude-sonnet-4-6"),
        )
        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(
            return_value=[_MockProviderConfig(provider="openai", model="gpt-4o")]
        )
        resolver = _make_resolver(store=mock_reg)

        target = await resolver.select_model_target_for_session(
            session=session,
            scope=None,
            agent_def=agent_def,
        )

        assert target.provider == "anthropic"
        assert target.model_id == "claude-sonnet-4-6"
        assert target.base_url is None

    @pytest.mark.asyncio
    async def test_registry_fallback_uses_lowest_priority(self) -> None:
        session = _make_session()
        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(
            return_value=[
                _MockProviderConfig(id="high", model="gpt-4o", priority=10),
                _MockProviderConfig(id="low", model="gemini-3-flash-preview", priority=1),
                _MockProviderConfig(id="medium", model="llama-3.3-70b", priority=5),
            ]
        )
        resolver = _make_resolver(store=mock_reg)

        target = await resolver.select_model_target_for_session(session=session, scope=None)

        assert target.provider_id == "low"
        assert target.model_id == "gemini-3-flash-preview"

    @pytest.mark.asyncio
    async def test_missing_provider_configuration_raises(self) -> None:
        from server.app.exceptions import LLMProviderConfigError

        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(return_value=[])
        resolver = _make_resolver(store=mock_reg)

        with pytest.raises(LLMProviderConfigError, match="No provider configuration found"):
            await resolver.select_model_target_for_session(session=_make_session(), scope=None)


class TestResolveModelConfig:
    @pytest.mark.asyncio
    async def test_resolve_model_config_materializes_env_credentials(self) -> None:
        mock_reg = MagicMock()
        mock_reg.list_providers = AsyncMock(
            return_value=[
                _MockProviderConfig(
                    provider="openai_compatible",
                    model="google/gemini-3-flash-preview",
                    api_key_env="MY_CUSTOM_API_KEY",
                    base_url="https://api.example.com/v1",
                )
            ]
        )
        resolver = _make_resolver(store=mock_reg)

        with patch.dict("os.environ", {"MY_CUSTOM_API_KEY": "resolved-key"}):
            resolved = await resolver.resolve_model_config_for_session(
                session=_make_session(),
                scope=None,
            )

        assert resolved.provider == "openai_compatible"
        assert resolved.model_id == "google/gemini-3-flash-preview"
        assert resolved.api_key == "resolved-key"
        assert resolved.base_url == "https://api.example.com/v1"

    @pytest.mark.asyncio
    async def test_resolve_model_for_session_forwards_temperature_and_max_tokens(self) -> None:
        from server.app.agent.definition import AgentConfig, AgentDefinition

        resolver = _make_resolver(store=None)
        agent_def = AgentDefinition(
            name="test-agent",
            system_prompt="test",
            config=AgentConfig(temperature=0.2, max_tokens=16000),
        )

        with (
            patch.object(
                resolver,
                "select_model_target_for_session",
                new=AsyncMock(
                    return_value=MagicMock(
                        provider="openai",
                        model_id="gpt-4o",
                        api_key_env=None,
                        base_url=None,
                        region=None,
                        role_arn=None,
                        max_retries=2,
                        timeout=30,
                    )
                ),
            ),
            patch.object(resolver, "build_model", return_value=MagicMock()) as mock_build_model,
            patch.object(resolver, "_warn_if_no_tool_call_support", new=AsyncMock()),
        ):
            await resolver.resolve_model_for_session(
                session=_make_session(),
                scope=None,
                agent_def=agent_def,
            )

        kwargs = mock_build_model.call_args.kwargs
        assert kwargs["temperature"] == 0.2
        assert kwargs["max_tokens"] == 16000


class TestBuildModel:
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
        resolver = RuntimeResolver(config_store=None, settings=self._make_settings())
        resolver._settings.openai_api_key = MagicMock()
        resolver._settings.openai_api_key.get_secret_value.return_value = "sk-openai"

        with patch("server.app.agent.resolver.init_chat_model") as mock_init:
            resolver.build_model("openai", "gpt-4o")
            assert mock_init.call_args[0][0] == "gpt-4o"
            assert mock_init.call_args.kwargs["model_provider"] == "openai"

    def test_openai_compatible_raises_without_base_url(self) -> None:
        from server.app.exceptions import LLMProviderConfigError

        settings = self._make_settings()
        settings.openai_compatible_base_url = None
        resolver = RuntimeResolver(config_store=None, settings=settings)

        with pytest.raises(LLMProviderConfigError, match="base_url is required"):
            resolver.build_model("openai_compatible", "some-model")

    def test_unknown_provider_raises_config_error(self) -> None:
        from server.app.exceptions import LLMProviderConfigError

        resolver = RuntimeResolver(config_store=None, settings=self._make_settings())

        with pytest.raises(LLMProviderConfigError, match="Unknown provider"):
            resolver.build_model("mystic_cloud", "some-model")
