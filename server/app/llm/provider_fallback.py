"""Provider fallback chain for LLM resilience.

Attempts providers in order, falling back to the next
on failure. Integrates with the circuit breaker pattern.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import structlog

from server.app.exceptions import LLMUnavailableError

logger = structlog.get_logger(__name__)


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""

    provider: str  # "openai", "bedrock", "openai_compatible", "mock"
    model: str  # Model ID (e.g. "gpt-4o", "claude-3-sonnet")
    priority: int = 0  # Lower = tried first
    enabled: bool = True
    max_retries: int = 2

    # Provider-specific overrides
    api_key: Optional[str] = None
    base_url: Optional[str] = None
    region: Optional[str] = None


@dataclass
class FallbackResult:
    """Result of a fallback chain attempt."""

    model: Any  # The LLM model instance
    provider_config: ProviderConfig
    attempts: list[tuple[str, Optional[str]]] = field(
        default_factory=list
    )  # (provider, error_or_None)


class ProviderFallbackChain:
    """Tries providers in priority order, falling back on failure.

    Example:
        chain = ProviderFallbackChain([
            ProviderConfig(provider="openai", model="gpt-4o", priority=0),
            ProviderConfig(provider="bedrock", model="claude-3-sonnet", priority=1),
        ])
        result = await chain.get_model(settings)
        # Uses openai first, falls back to bedrock if openai fails
    """

    def __init__(
        self,
        providers: Sequence[ProviderConfig] | None = None,
    ) -> None:
        self._providers: list[ProviderConfig] = sorted(providers or [], key=lambda p: p.priority)

    @property
    def providers(self) -> Sequence[ProviderConfig]:
        """Active providers in priority order."""
        return [p for p in self._providers if p.enabled]

    def add_provider(self, config: ProviderConfig) -> None:
        """Add a provider to the fallback chain.

        Args:
            config: Provider configuration to add.
        """
        self._providers.append(config)
        self._providers.sort(key=lambda p: p.priority)

    def remove_provider(self, provider: str) -> None:
        """Remove a provider from the fallback chain.

        Args:
            provider: Provider name to remove.
        """
        self._providers = [p for p in self._providers if p.provider != provider]

    async def get_model(self, settings: Any) -> FallbackResult:
        """Try providers in order and return the first successful model.

        Args:
            settings: Application settings for provider configuration.

        Returns:
            FallbackResult with the model and attempt history.

        Raises:
            LLMUnavailableError: If all providers fail.
        """
        attempts: list[tuple[str, Optional[str]]] = []
        active_providers = self.providers

        if not active_providers:
            raise LLMUnavailableError(
                provider="none",
                reason="No providers configured in fallback chain",
            )

        for config in active_providers:
            try:
                model = self._create_model(config, settings)
                attempts.append((config.provider, None))

                logger.info(
                    "Provider selected",
                    provider=config.provider,
                    model=config.model,
                    attempt=len(attempts),
                )

                return FallbackResult(
                    model=model,
                    provider_config=config,
                    attempts=attempts,
                )

            except Exception as e:
                error_msg = str(e)
                attempts.append((config.provider, error_msg))
                logger.warning(
                    "Provider failed, trying next",
                    provider=config.provider,
                    model=config.model,
                    error=error_msg,
                    remaining=len(active_providers) - len(attempts),
                )

        # All providers failed
        provider_names = [a[0] for a in attempts]
        raise LLMUnavailableError(
            provider=", ".join(provider_names),
            reason=f"All {len(attempts)} providers failed",
        )

    def _create_model(self, config: ProviderConfig, settings: Any) -> Any:
        """Create an LLM model instance from provider config.

        Args:
            config: Provider configuration.
            settings: Application settings for defaults.

        Returns:
            LLM model instance.

        Raises:
            ImportError: If provider SDK is not installed.
            Exception: If model creation fails.
        """
        if config.provider == "openai":
            from langchain_openai import ChatOpenAI

            api_key = config.api_key
            if not api_key and settings.openai_api_key:
                api_key = settings.openai_api_key.get_secret_value()

            return ChatOpenAI(
                model=config.model,
                api_key=api_key,
                base_url=config.base_url or settings.openai_api_base,
            )

        elif config.provider == "openai_compatible":
            from langchain_openai import ChatOpenAI

            api_key = config.api_key
            if not api_key:
                api_key = settings.openai_compatible_api_key.get_secret_value()

            base_url = config.base_url or settings.openai_compatible_base_url

            return ChatOpenAI(
                model=config.model,
                api_key=api_key,
                base_url=base_url,
            )

        elif config.provider == "bedrock":
            from langchain_aws import ChatBedrock

            aws_access_key = None
            aws_secret_key = None
            if settings.aws_access_key_id:
                aws_access_key = settings.aws_access_key_id.get_secret_value()
            if settings.aws_secret_access_key:
                aws_secret_key = settings.aws_secret_access_key.get_secret_value()

            return ChatBedrock(
                model_id=config.model,
                region_name=config.region or settings.aws_region,
                aws_access_key_id=aws_access_key,
                aws_secret_access_key=aws_secret_key,
            )

        elif config.provider == "mock":
            from server.app.llm.mock import MockLLM

            return MockLLM()

        else:
            raise ValueError(f"Unknown provider: {config.provider}")

    @classmethod
    def from_settings(cls, settings: Any) -> ProviderFallbackChain:
        """Create a fallback chain from application settings.

        Uses the primary provider from settings, with optional
        fallback providers configured via COGNITION_FALLBACK_PROVIDERS.

        Args:
            settings: Application settings.

        Returns:
            Configured ProviderFallbackChain.
        """
        providers = []

        # Primary provider from settings
        primary = ProviderConfig(
            provider=settings.llm_provider,
            model=_get_model_id(settings),
            priority=0,
        )
        providers.append(primary)

        # Add fallback providers if configured
        fallback_providers = getattr(settings, "fallback_providers", [])
        for i, fb in enumerate(fallback_providers):
            if isinstance(fb, dict):
                providers.append(
                    ProviderConfig(
                        provider=fb.get("provider", ""),
                        model=fb.get("model", ""),
                        priority=i + 1,
                        api_key=fb.get("api_key"),
                        base_url=fb.get("base_url"),
                        region=fb.get("region"),
                    )
                )

        return cls(providers)


def _get_model_id(settings: Any) -> str:
    """Extract the model ID from settings based on provider."""
    if settings.llm_provider == "bedrock":
        return settings.bedrock_model_id
    return settings.llm_model
