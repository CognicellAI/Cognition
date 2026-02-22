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
from server.app.execution.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    get_circuit_breaker_registry,
)

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

    Integrates with circuit breakers to prevent cascading failures
    when providers are degraded. Each provider has its own circuit
    breaker that opens after repeated failures.

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
        self._circuit_breakers: dict[str, CircuitBreaker] = {}
        self._init_circuit_breakers()

    def _init_circuit_breakers(self) -> None:
        """Initialize circuit breakers for all providers."""
        registry = get_circuit_breaker_registry()
        for config in self._providers:
            breaker_name = f"llm_provider_{config.provider}"
            if breaker_name not in registry:
                registry[breaker_name] = CircuitBreaker(
                    config=CircuitBreakerConfig(
                        name=breaker_name,
                        failure_threshold=max(1, config.max_retries),
                        success_threshold=2,
                        timeout_seconds=60.0,
                        half_open_max_calls=1,
                    )
                )
            self._circuit_breakers[config.provider] = registry[breaker_name]

    def _get_circuit_breaker(self, provider: str) -> CircuitBreaker:
        """Get the circuit breaker for a provider."""
        return self._circuit_breakers[provider]

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

        Uses circuit breakers to prevent cascading failures when providers
        are degraded. Respects the max_retries field from ProviderConfig.

        Args:
            settings: Application settings for provider configuration.

        Returns:
            FallbackResult with the model and attempt history.

        Raises:
            LLMUnavailableError: If all providers fail.
            CircuitBreakerOpenError: If a provider's circuit breaker is open.
        """
        attempts: list[tuple[str, Optional[str]]] = []
        active_providers = self.providers

        if not active_providers:
            raise LLMUnavailableError(
                provider="none",
                reason="No providers configured in fallback chain",
            )

        for config in active_providers:
            breaker = self._get_circuit_breaker(config.provider)

            # Check if circuit breaker is open
            if breaker.is_open():
                error_msg = f"Circuit breaker is OPEN for {config.provider}"
                attempts.append((config.provider, error_msg))
                logger.warning(
                    "Provider circuit breaker open, skipping",
                    provider=config.provider,
                    state=breaker.get_metrics().state,
                )
                continue

            try:
                # Use circuit breaker to protect model creation
                # Retry logic is handled by the circuit breaker with exponential backoff
                model = await breaker.call(
                    self._create_model_with_retry,
                    config,
                    settings,
                    max_retries=config.max_retries,
                )

                attempts.append((config.provider, None))

                # Record success
                await breaker.record_success()

                logger.info(
                    "Provider selected",
                    provider=config.provider,
                    model=config.model,
                    attempt=len(attempts),
                    circuit_state=breaker.get_metrics().state,
                )

                return FallbackResult(
                    model=model,
                    provider_config=config,
                    attempts=attempts,
                )

            except CircuitBreakerOpenError:
                # Circuit opened during the call
                error_msg = f"Circuit breaker opened during call to {config.provider}"
                attempts.append((config.provider, error_msg))
                logger.warning(
                    "Provider circuit breaker opened mid-call",
                    provider=config.provider,
                )

            except Exception as e:
                error_msg = str(e)
                attempts.append((config.provider, error_msg))

                # Record failure
                await breaker.record_failure(error_msg)

                logger.warning(
                    "Provider failed, trying next",
                    provider=config.provider,
                    model=config.model,
                    error=error_msg,
                    circuit_state=breaker.get_metrics().state,
                    remaining=len(active_providers) - len(attempts),
                )

        # All providers failed
        provider_names = [a[0] for a in attempts]
        raise LLMUnavailableError(
            provider=", ".join(provider_names),
            reason=f"All {len(attempts)} providers failed",
        )

    async def _create_model_with_retry(
        self,
        config: ProviderConfig,
        settings: Any,
        max_retries: int = 2,
    ) -> Any:
        """Create an LLM model instance with retry logic.

        Args:
            config: Provider configuration.
            settings: Application settings for defaults.
            max_retries: Maximum number of retry attempts.

        Returns:
            LLM model instance.

        Raises:
            Exception: If model creation fails after all retries.
        """
        from server.app.llm.registry import get_provider_factory

        factory = get_provider_factory(config.provider)
        last_error: Exception | None = None

        for attempt in range(max_retries + 1):
            try:
                return factory(config, settings)
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    wait_time = 2**attempt  # Exponential backoff: 1s, 2s, 4s
                    logger.warning(
                        "Model creation failed, retrying",
                        provider=config.provider,
                        attempt=attempt + 1,
                        max_retries=max_retries,
                        wait_seconds=wait_time,
                        error=str(e),
                    )
                    await asyncio.sleep(wait_time)
                else:
                    break

        # All retries exhausted
        raise last_error or Exception(f"Failed to create model for {config.provider}")

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
        from server.app.llm.registry import get_provider_factory

        factory = get_provider_factory(config.provider)
        return factory(config, settings)

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
