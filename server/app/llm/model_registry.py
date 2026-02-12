"""Model registry backed by models.dev API.

Fetches, caches, and queries AI model metadata including
capabilities, pricing, and token limits.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

import httpx
import structlog

logger = structlog.get_logger(__name__)

MODELS_DEV_API_URL = "https://models.dev/api.json"
CACHE_TTL_SECONDS = 3600  # 1 hour
DEFAULT_CACHE_DIR = Path.home() / ".cache" / "cognition"


@dataclass(frozen=True)
class ModelCost:
    """Token pricing for a model (USD per million tokens)."""

    input: float = 0.0
    output: float = 0.0
    cache_read: Optional[float] = None
    cache_write: Optional[float] = None
    reasoning: Optional[float] = None

    def estimate(
        self,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
    ) -> float:
        """Estimate cost for a given token usage.

        Args:
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            cached_tokens: Number of cached input tokens.

        Returns:
            Estimated cost in USD.
        """
        cost = (input_tokens / 1_000_000) * self.input
        cost += (output_tokens / 1_000_000) * self.output
        if cached_tokens and self.cache_read is not None:
            cost += (cached_tokens / 1_000_000) * self.cache_read
        return cost


@dataclass(frozen=True)
class ModelLimits:
    """Token limits for a model."""

    context: int = 0
    output: int = 0
    input: Optional[int] = None

    @property
    def effective_input(self) -> int:
        """Maximum input tokens (explicit or context - output)."""
        if self.input is not None:
            return self.input
        return max(0, self.context - self.output)


@dataclass(frozen=True)
class ModelInfo:
    """Complete model metadata from models.dev."""

    id: str
    name: str
    provider_id: str
    provider_name: str
    family: Optional[str] = None
    tool_call: bool = False
    reasoning: bool = False
    attachment: bool = False
    structured_output: bool = False
    temperature: bool = True
    knowledge: Optional[str] = None
    release_date: Optional[str] = None
    open_weights: bool = False
    cost: ModelCost = field(default_factory=ModelCost)
    limits: ModelLimits = field(default_factory=ModelLimits)
    input_modalities: tuple[str, ...] = ("text",)
    output_modalities: tuple[str, ...] = ("text",)
    status: Optional[str] = None

    @property
    def qualified_id(self) -> str:
        """Provider-qualified model ID (e.g. 'anthropic/claude-opus-4-6')."""
        return f"{self.provider_id}/{self.id}"


@dataclass(frozen=True)
class ProviderInfo:
    """Provider metadata from models.dev."""

    id: str
    name: str
    env: tuple[str, ...] = ()
    npm: Optional[str] = None
    doc: Optional[str] = None
    api: Optional[str] = None


class ModelRegistry:
    """Registry of AI models sourced from models.dev.

    Fetches the full catalog from https://models.dev/api.json,
    caches it locally, and provides lookup methods.

    Example:
        registry = ModelRegistry()
        await registry.refresh()

        model = registry.get_model("anthropic", "claude-opus-4-6")
        print(model.cost.estimate(input_tokens=1000, output_tokens=500))

        models = registry.list_models(provider="openai", tool_call=True)
    """

    def __init__(
        self,
        cache_dir: Path | None = None,
        cache_ttl: int = CACHE_TTL_SECONDS,
    ):
        self._cache_dir = cache_dir or DEFAULT_CACHE_DIR
        self._cache_ttl = cache_ttl
        self._providers: dict[str, ProviderInfo] = {}
        self._models: dict[str, ModelInfo] = {}  # keyed by "provider/model"
        self._last_fetched: float = 0.0

    @property
    def is_loaded(self) -> bool:
        """Whether the registry has data loaded."""
        return len(self._models) > 0

    @property
    def is_stale(self) -> bool:
        """Whether the cache is older than TTL."""
        return (time.time() - self._last_fetched) > self._cache_ttl

    async def refresh(self, force: bool = False) -> None:
        """Refresh the model catalog.

        Fetches from models.dev API if cache is stale or force=True.
        Falls back to local cache on network failure.

        Args:
            force: Force a refresh even if cache is fresh.
        """
        if not force and self.is_loaded and not self.is_stale:
            return

        # Try loading from local cache first
        cache_file = self._cache_dir / "models_dev.json"
        if not force and cache_file.exists():
            cache_age = time.time() - cache_file.stat().st_mtime
            if cache_age < self._cache_ttl:
                try:
                    data = json.loads(cache_file.read_text())
                    self._parse_api_data(data)
                    self._last_fetched = cache_file.stat().st_mtime
                    logger.info(
                        "Loaded model registry from cache",
                        providers=len(self._providers),
                        models=len(self._models),
                    )
                    return
                except Exception as e:
                    logger.warning("Failed to load cached registry", error=str(e))

        # Fetch from API
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.get(MODELS_DEV_API_URL)
                response.raise_for_status()
                data = response.json()

            self._parse_api_data(data)
            self._last_fetched = time.time()

            # Save to cache
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            cache_file.write_text(json.dumps(data))

            logger.info(
                "Refreshed model registry from models.dev",
                providers=len(self._providers),
                models=len(self._models),
            )

        except Exception as e:
            logger.error("Failed to fetch models.dev API", error=str(e))

            # Fall back to stale cache if available
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text())
                    self._parse_api_data(data)
                    self._last_fetched = cache_file.stat().st_mtime
                    logger.info(
                        "Fell back to stale cache",
                        providers=len(self._providers),
                        models=len(self._models),
                    )
                except Exception as cache_err:
                    logger.error("Failed to load stale cache", error=str(cache_err))

    def _parse_api_data(self, data: dict[str, Any]) -> None:
        """Parse the models.dev API response into registry objects."""
        providers: dict[str, ProviderInfo] = {}
        models: dict[str, ModelInfo] = {}

        for provider_id, provider_data in data.items():
            if not isinstance(provider_data, dict):
                continue

            provider = ProviderInfo(
                id=provider_id,
                name=provider_data.get("name", provider_id),
                env=tuple(provider_data.get("env", [])),
                npm=provider_data.get("npm"),
                doc=provider_data.get("doc"),
                api=provider_data.get("api"),
            )
            providers[provider_id] = provider

            for model_id, model_data in provider_data.get("models", {}).items():
                if not isinstance(model_data, dict):
                    continue

                cost_data = model_data.get("cost", {})
                limit_data = model_data.get("limit", {})
                modalities = model_data.get("modalities", {})

                model = ModelInfo(
                    id=model_id,
                    name=model_data.get("name", model_id),
                    provider_id=provider_id,
                    provider_name=provider.name,
                    family=model_data.get("family"),
                    tool_call=model_data.get("tool_call", False),
                    reasoning=model_data.get("reasoning", False),
                    attachment=model_data.get("attachment", False),
                    structured_output=model_data.get("structured_output", False),
                    temperature=model_data.get("temperature", True),
                    knowledge=model_data.get("knowledge"),
                    release_date=model_data.get("release_date"),
                    open_weights=model_data.get("open_weights", False),
                    cost=ModelCost(
                        input=cost_data.get("input", 0.0),
                        output=cost_data.get("output", 0.0),
                        cache_read=cost_data.get("cache_read"),
                        cache_write=cost_data.get("cache_write"),
                        reasoning=cost_data.get("reasoning"),
                    ),
                    limits=ModelLimits(
                        context=limit_data.get("context", 0),
                        output=limit_data.get("output", 0),
                        input=limit_data.get("input"),
                    ),
                    input_modalities=tuple(modalities.get("input", ["text"])),
                    output_modalities=tuple(modalities.get("output", ["text"])),
                    status=model_data.get("status"),
                )
                models[model.qualified_id] = model

        self._providers = providers
        self._models = models

    def get_model(self, provider: str, model_id: str) -> Optional[ModelInfo]:
        """Look up a model by provider and model ID.

        Args:
            provider: Provider identifier (e.g. 'anthropic', 'openai').
            model_id: Model identifier (e.g. 'claude-opus-4-6', 'gpt-4o').

        Returns:
            ModelInfo if found, None otherwise.
        """
        return self._models.get(f"{provider}/{model_id}")

    def get_model_by_qualified_id(self, qualified_id: str) -> Optional[ModelInfo]:
        """Look up a model by qualified ID (provider/model).

        Args:
            qualified_id: Full model ID (e.g. 'anthropic/claude-opus-4-6').

        Returns:
            ModelInfo if found, None otherwise.
        """
        return self._models.get(qualified_id)

    def get_provider(self, provider_id: str) -> Optional[ProviderInfo]:
        """Look up a provider by ID.

        Args:
            provider_id: Provider identifier (e.g. 'anthropic').

        Returns:
            ProviderInfo if found, None otherwise.
        """
        return self._providers.get(provider_id)

    def list_providers(self) -> Sequence[ProviderInfo]:
        """List all known providers."""
        return list(self._providers.values())

    def list_models(
        self,
        provider: Optional[str] = None,
        tool_call: Optional[bool] = None,
        reasoning: Optional[bool] = None,
        min_context: Optional[int] = None,
        status: Optional[str] = None,
    ) -> Sequence[ModelInfo]:
        """List models with optional filters.

        Args:
            provider: Filter by provider ID.
            tool_call: Filter by tool calling support.
            reasoning: Filter by reasoning support.
            min_context: Filter by minimum context window.
            status: Filter by model status (None excludes deprecated).

        Returns:
            Sequence of matching ModelInfo objects.
        """
        results: list[ModelInfo] = []

        for model in self._models.values():
            # Exclude deprecated by default
            if status is None and model.status == "deprecated":
                continue

            if provider and model.provider_id != provider:
                continue
            if tool_call is not None and model.tool_call != tool_call:
                continue
            if reasoning is not None and model.reasoning != reasoning:
                continue
            if min_context and model.limits.context < min_context:
                continue
            if status is not None and model.status != status:
                continue

            results.append(model)

        return results

    def search_models(self, query: str) -> Sequence[ModelInfo]:
        """Search models by name or ID (case-insensitive).

        Args:
            query: Search term to match against model name or ID.

        Returns:
            Sequence of matching ModelInfo objects.
        """
        query_lower = query.lower()
        results: list[ModelInfo] = []

        for model in self._models.values():
            if model.status == "deprecated":
                continue
            if (
                query_lower in model.id.lower()
                or query_lower in model.name.lower()
                or query_lower in model.qualified_id.lower()
            ):
                results.append(model)

        return results


# Global registry instance
_registry: ModelRegistry | None = None


def get_model_registry() -> ModelRegistry:
    """Get the global model registry instance."""
    global _registry
    if _registry is None:
        _registry = ModelRegistry()
    return _registry
