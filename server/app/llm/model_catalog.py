"""Model catalog service backed by models.dev.

Fetches and caches the public model catalog from models.dev/api.json,
providing model metadata (context windows, tool call support, pricing,
modalities) for enrichment of API responses and validation warnings.

The catalog is **enrichment only** — it never blocks model execution.
If the catalog is unreachable, endpoints degrade gracefully (return
empty lists or skip enrichment).

Architecture: Layer 5 (LLM Provider) — read-only data service.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Static mapping: Cognition provider type → models.dev provider slug(s)
# ---------------------------------------------------------------------------

PROVIDER_TYPE_TO_CATALOG_SLUGS: dict[str, list[str]] = {
    "openai": ["openai"],
    "anthropic": ["anthropic"],
    "bedrock": ["amazon-bedrock"],
    "google_genai": ["google"],
    "google_vertexai": ["google-vertex"],
    # openai_compatible is a passthrough — the backing provider depends on
    # the base_url (OpenRouter, vLLM, etc.). We can't determine which
    # catalog provider to map to, so we return an empty list.
    "openai_compatible": [],
    "mock": [],
}


def catalog_slugs_for_provider(provider_type: str) -> list[str]:
    """Return models.dev provider slugs for a Cognition provider type.

    Args:
        provider_type: Cognition provider type (e.g. "openai", "bedrock").

    Returns:
        List of models.dev provider slugs. Empty if no mapping exists.
    """
    return PROVIDER_TYPE_TO_CATALOG_SLUGS.get(provider_type, [])


# ---------------------------------------------------------------------------
# CatalogModel — enriched model metadata from the catalog
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CatalogModel:
    """A model entry from the models.dev catalog.

    All fields come directly from the catalog JSON. Costs are per-million
    tokens. Context/output limits are in tokens.
    """

    id: str
    name: str
    provider_slug: str
    family: str
    tool_call: bool
    reasoning: bool
    context_window: int
    output_limit: int
    input_cost: float
    output_cost: float
    modalities: dict[str, list[str]] = field(default_factory=dict)
    structured_output: bool | None = None
    status: str | None = None


# ---------------------------------------------------------------------------
# ModelCatalog — in-memory cache with TTL
# ---------------------------------------------------------------------------


class ModelCatalog:
    """In-memory cache of the models.dev catalog.

    Fetches the full catalog JSON on first access, then serves from
    cache until the TTL expires. If a refresh fails, stale data is
    served with a warning. If no data has ever been fetched, methods
    return empty results.

    Usage:
        catalog = ModelCatalog(url="https://models.dev/api.json", ttl_seconds=3600)
        models = await catalog.get_models_for_provider("openai")
    """

    def __init__(self, catalog_url: str, ttl_seconds: int = 3600) -> None:
        self._catalog_url = catalog_url
        self._ttl_seconds = ttl_seconds

        # Cache state
        self._cache: dict[str, list[CatalogModel]] = {}  # provider_slug → models
        self._all_models: dict[str, CatalogModel] = {}  # (provider_slug, model_id) key → model
        self._last_refresh: float = 0.0
        self._provider_names: dict[str, str] = {}  # provider_slug → display name

    @property
    def is_stale(self) -> bool:
        """Whether the cache has expired or was never populated."""
        if self._last_refresh == 0.0:
            return True
        return (time.monotonic() - self._last_refresh) > self._ttl_seconds

    @property
    def is_empty(self) -> bool:
        """Whether the cache has never been populated."""
        return self._last_refresh == 0.0

    async def ensure_loaded(self) -> None:
        """Ensure the catalog is loaded, refreshing if stale or empty."""
        if self.is_stale or self.is_empty:
            await self.refresh()

    async def refresh(self) -> None:
        """Fetch the catalog from the configured URL and replace the cache.

        On failure, logs a warning and keeps stale data if available.
        """
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(self._catalog_url)
                resp.raise_for_status()
                raw = resp.json()

            self._parse_catalog(raw)
            self._last_refresh = time.monotonic()
            total_models = sum(len(models) for models in self._cache.values())
            logger.info(
                "Model catalog refreshed",
                url=self._catalog_url,
                providers=len(self._cache),
                models=total_models,
            )

        except Exception as exc:
            if self.is_empty:
                logger.warning(
                    "Model catalog fetch failed — no cached data available",
                    url=self._catalog_url,
                    error=str(exc),
                )
            else:
                logger.warning(
                    "Model catalog refresh failed — serving stale data",
                    url=self._catalog_url,
                    error=str(exc),
                    stale_age_seconds=int(time.monotonic() - self._last_refresh),
                )

    def _parse_catalog(self, raw: dict[str, Any]) -> None:
        """Parse the models.dev JSON structure into CatalogModel instances."""
        cache: dict[str, list[CatalogModel]] = {}
        all_models: dict[str, CatalogModel] = {}
        provider_names: dict[str, str] = {}

        for provider_slug, provider_data in raw.items():
            if not isinstance(provider_data, dict):
                continue
            models_dict = provider_data.get("models", {})
            if not isinstance(models_dict, dict):
                continue

            provider_names[provider_slug] = provider_data.get("name", provider_slug)
            provider_models: list[CatalogModel] = []

            for model_id, model_data in models_dict.items():
                if not isinstance(model_data, dict):
                    continue

                catalog_model = _parse_model_entry(
                    model_id=model_id,
                    data=model_data,
                    provider_slug=provider_slug,
                )
                if catalog_model is not None:
                    provider_models.append(catalog_model)
                    all_models[f"{provider_slug}:{model_id}"] = catalog_model

            if provider_models:
                cache[provider_slug] = provider_models

        self._cache = cache
        self._all_models = all_models
        self._provider_names = provider_names

    async def get_models_for_provider(self, provider_slug: str) -> list[CatalogModel]:
        """Return all models for a models.dev provider slug.

        Args:
            provider_slug: The models.dev provider slug (e.g. "openai", "anthropic").

        Returns:
            List of CatalogModel entries. Empty if provider not found or catalog unavailable.
        """
        await self.ensure_loaded()
        return list(self._cache.get(provider_slug, []))

    async def get_models_for_cognition_provider(self, provider_type: str) -> list[CatalogModel]:
        """Return models for a Cognition provider type using the static mapping.

        Args:
            provider_type: Cognition provider type (e.g. "openai", "bedrock").

        Returns:
            Combined list of CatalogModel entries from all mapped provider slugs.
        """
        slugs = catalog_slugs_for_provider(provider_type)
        if not slugs:
            return []

        result: list[CatalogModel] = []
        for slug in slugs:
            result.extend(await self.get_models_for_provider(slug))
        return result

    async def get_model(self, provider_slug: str, model_id: str) -> CatalogModel | None:
        """Look up a specific model by provider slug and model ID.

        Args:
            provider_slug: The models.dev provider slug.
            model_id: The model identifier within that provider.

        Returns:
            CatalogModel if found, None otherwise.
        """
        await self.ensure_loaded()
        return self._all_models.get(f"{provider_slug}:{model_id}")

    async def find_model(self, provider_type: str, model_id: str) -> CatalogModel | None:
        """Look up a model using Cognition provider type and model ID.

        Searches across all mapped provider slugs for the given model ID.

        Args:
            provider_type: Cognition provider type (e.g. "openai", "anthropic").
            model_id: The model identifier.

        Returns:
            CatalogModel if found, None otherwise.
        """
        slugs = catalog_slugs_for_provider(provider_type)
        for slug in slugs:
            entry = await self.get_model(slug, model_id)
            if entry is not None:
                return entry
        return None

    async def search(
        self,
        *,
        query: str | None = None,
        provider_slug: str | None = None,
        tool_call: bool | None = None,
        reasoning: bool | None = None,
    ) -> list[CatalogModel]:
        """Filter the catalog by various criteria.

        Args:
            query: Case-insensitive substring match on model ID or name.
            provider_slug: Filter to a specific models.dev provider.
            tool_call: Filter to models with/without tool call support.
            reasoning: Filter to models with/without reasoning support.

        Returns:
            Matching CatalogModel entries.
        """
        await self.ensure_loaded()

        candidates: list[CatalogModel]
        if provider_slug:
            candidates = list(self._cache.get(provider_slug, []))
        else:
            candidates = list(self._all_models.values())

        results: list[CatalogModel] = []
        for model in candidates:
            if query:
                q = query.lower()
                if q not in model.id.lower() and q not in model.name.lower():
                    continue
            if tool_call is not None and model.tool_call != tool_call:
                continue
            if reasoning is not None and model.reasoning != reasoning:
                continue
            results.append(model)

        return results

    def get_provider_name(self, provider_slug: str) -> str:
        """Return the display name for a provider slug, or the slug itself."""
        return self._provider_names.get(provider_slug, provider_slug)


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _parse_model_entry(
    model_id: str,
    data: dict[str, Any],
    provider_slug: str,
) -> CatalogModel | None:
    """Parse a single model entry from the catalog JSON.

    Returns None if required fields are missing or malformed.
    """
    try:
        limit = data.get("limit", {})
        cost = data.get("cost", {})

        return CatalogModel(
            id=model_id,
            name=data.get("name", model_id),
            provider_slug=provider_slug,
            family=data.get("family", ""),
            tool_call=bool(data.get("tool_call", False)),
            reasoning=bool(data.get("reasoning", False)),
            context_window=int(limit.get("context", 0)),
            output_limit=int(limit.get("output", 0)),
            input_cost=float(cost.get("input", 0.0)),
            output_cost=float(cost.get("output", 0.0)),
            modalities=data.get("modalities", {}),
            structured_output=data.get("structured_output"),
            status=data.get("status"),
        )
    except (TypeError, ValueError, AttributeError) as exc:
        logger.debug(
            "Skipping malformed catalog model entry",
            model_id=model_id,
            provider=provider_slug,
            error=str(exc),
        )
        return None


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_catalog: ModelCatalog | None = None


def get_model_catalog() -> ModelCatalog:
    """Get the global ModelCatalog singleton.

    Initializes on first call using the current Settings.
    """
    global _catalog
    if _catalog is None:
        from server.app.settings import get_settings

        settings = get_settings()
        _catalog = ModelCatalog(
            catalog_url=settings.model_catalog_url,
            ttl_seconds=settings.model_catalog_ttl_seconds,
        )
    return _catalog


def reset_model_catalog() -> None:
    """Reset the global catalog singleton (for testing)."""
    global _catalog
    _catalog = None
