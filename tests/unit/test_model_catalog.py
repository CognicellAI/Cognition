"""Unit tests for server.app.llm.model_catalog.

Tests the ModelCatalog service: caching, provider mapping, model lookup,
search, and graceful degradation when the catalog is unreachable.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.llm.model_catalog import (
    PROVIDER_TYPE_TO_CATALOG_SLUGS,
    CatalogModel,
    ModelCatalog,
    _parse_model_entry,
    catalog_slugs_for_provider,
    reset_model_catalog,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SAMPLE_CATALOG_JSON: dict[str, Any] = {
    "openai": {
        "id": "openai",
        "name": "OpenAI",
        "env": ["OPENAI_API_KEY"],
        "npm": "@ai-sdk/openai",
        "doc": "https://platform.openai.com/docs",
        "models": {
            "gpt-4o": {
                "id": "gpt-4o",
                "name": "GPT-4o",
                "family": "gpt",
                "attachment": True,
                "reasoning": False,
                "tool_call": True,
                "structured_output": True,
                "temperature": True,
                "knowledge": "2023-09",
                "release_date": "2024-05-13",
                "last_updated": "2024-08-06",
                "modalities": {
                    "input": ["text", "image"],
                    "output": ["text"],
                },
                "open_weights": False,
                "cost": {"input": 2.5, "output": 10, "cache_read": 1.25},
                "limit": {"context": 128000, "output": 16384},
            },
            "o1-mini": {
                "id": "o1-mini",
                "name": "o1 Mini",
                "family": "o1",
                "attachment": False,
                "reasoning": True,
                "tool_call": False,
                "modalities": {"input": ["text"], "output": ["text"]},
                "open_weights": False,
                "cost": {"input": 3, "output": 12},
                "limit": {"context": 128000, "output": 65536},
            },
        },
    },
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "env": ["ANTHROPIC_API_KEY"],
        "npm": "@ai-sdk/anthropic",
        "doc": "https://docs.anthropic.com",
        "models": {
            "claude-sonnet-4-6": {
                "id": "claude-sonnet-4-6",
                "name": "Claude Sonnet 4.6",
                "family": "claude-sonnet",
                "attachment": True,
                "reasoning": True,
                "tool_call": True,
                "temperature": True,
                "modalities": {
                    "input": ["text", "image", "pdf"],
                    "output": ["text"],
                },
                "open_weights": False,
                "cost": {"input": 3, "output": 15},
                "limit": {"context": 1000000, "output": 64000},
            },
        },
    },
    "amazon-bedrock": {
        "id": "amazon-bedrock",
        "name": "Amazon Bedrock",
        "env": [],
        "npm": "@ai-sdk/amazon-bedrock",
        "doc": "https://docs.aws.amazon.com/bedrock",
        "models": {
            "anthropic.claude-3-sonnet-20240229-v1:0": {
                "id": "anthropic.claude-3-sonnet-20240229-v1:0",
                "name": "Claude 3 Sonnet (Bedrock)",
                "family": "claude-sonnet",
                "attachment": True,
                "reasoning": False,
                "tool_call": True,
                "modalities": {"input": ["text", "image"], "output": ["text"]},
                "open_weights": False,
                "cost": {"input": 3, "output": 15},
                "limit": {"context": 200000, "output": 4096},
            },
        },
    },
}


def _make_catalog(ttl: int = 3600) -> ModelCatalog:
    """Create a catalog with a test URL."""
    return ModelCatalog(catalog_url="https://test.example.com/api.json", ttl_seconds=ttl)


# ---------------------------------------------------------------------------
# _parse_model_entry tests
# ---------------------------------------------------------------------------


class TestParseModelEntry:
    """Tests for the low-level model entry parser."""

    def test_parses_complete_entry(self) -> None:
        data = SAMPLE_CATALOG_JSON["openai"]["models"]["gpt-4o"]
        result = _parse_model_entry("gpt-4o", data, "openai")

        assert result is not None
        assert result.id == "gpt-4o"
        assert result.name == "GPT-4o"
        assert result.provider_slug == "openai"
        assert result.family == "gpt"
        assert result.tool_call is True
        assert result.reasoning is False
        assert result.context_window == 128000
        assert result.output_limit == 16384
        assert result.input_cost == 2.5
        assert result.output_cost == 10
        assert result.structured_output is True
        assert result.modalities == {"input": ["text", "image"], "output": ["text"]}

    def test_parses_minimal_entry(self) -> None:
        data = {
            "name": "Test Model",
            "family": "test",
            "tool_call": False,
            "reasoning": False,
            "modalities": {"input": ["text"], "output": ["text"]},
            "open_weights": False,
            "cost": {},
            "limit": {},
        }
        result = _parse_model_entry("test-model", data, "test-provider")

        assert result is not None
        assert result.id == "test-model"
        assert result.context_window == 0
        assert result.input_cost == 0.0

    def test_returns_none_for_malformed_data(self) -> None:
        # limit is a string instead of dict — should cause TypeError
        data = {"limit": "bad", "cost": "bad"}
        result = _parse_model_entry("bad-model", data, "test")
        assert result is None


# ---------------------------------------------------------------------------
# catalog_slugs_for_provider tests
# ---------------------------------------------------------------------------


class TestCatalogSlugsForProvider:
    """Tests for the static provider mapping."""

    def test_known_providers(self) -> None:
        assert catalog_slugs_for_provider("openai") == ["openai"]
        assert catalog_slugs_for_provider("anthropic") == ["anthropic"]
        assert catalog_slugs_for_provider("bedrock") == ["amazon-bedrock"]
        assert catalog_slugs_for_provider("google_genai") == ["google"]
        assert catalog_slugs_for_provider("google_vertexai") == ["google-vertex"]

    def test_openai_compatible_returns_empty(self) -> None:
        assert catalog_slugs_for_provider("openai_compatible") == []

    def test_unknown_provider_returns_empty(self) -> None:
        assert catalog_slugs_for_provider("totally_unknown") == []

    def test_mapping_covers_all_cognition_providers(self) -> None:
        """All expected Cognition provider types are in the mapping."""
        expected = {
            "openai",
            "anthropic",
            "bedrock",
            "google_genai",
            "google_vertexai",
            "openai_compatible",
            "mock",
        }
        assert set(PROVIDER_TYPE_TO_CATALOG_SLUGS.keys()) == expected


# ---------------------------------------------------------------------------
# ModelCatalog core tests
# ---------------------------------------------------------------------------


class TestModelCatalog:
    """Tests for the ModelCatalog service."""

    def test_starts_empty(self) -> None:
        catalog = _make_catalog()
        assert catalog.is_empty is True
        assert catalog.is_stale is True

    def test_parse_catalog_populates_cache(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)

        assert "openai" in catalog._cache
        assert "anthropic" in catalog._cache
        assert "amazon-bedrock" in catalog._cache
        assert len(catalog._cache["openai"]) == 2
        assert len(catalog._cache["anthropic"]) == 1

    def test_parse_catalog_populates_all_models(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)

        assert "openai:gpt-4o" in catalog._all_models
        assert "openai:o1-mini" in catalog._all_models
        assert "anthropic:claude-sonnet-4-6" in catalog._all_models

    def test_parse_catalog_populates_provider_names(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)

        assert catalog.get_provider_name("openai") == "OpenAI"
        assert catalog.get_provider_name("anthropic") == "Anthropic"
        assert catalog.get_provider_name("unknown") == "unknown"

    @pytest.mark.asyncio
    async def test_get_models_for_provider(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()  # Mark as fresh

        models = await catalog.get_models_for_provider("openai")
        assert len(models) == 2
        ids = {m.id for m in models}
        assert "gpt-4o" in ids
        assert "o1-mini" in ids

    @pytest.mark.asyncio
    async def test_get_models_for_provider_unknown(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        models = await catalog.get_models_for_provider("unknown-provider")
        assert models == []

    @pytest.mark.asyncio
    async def test_get_models_for_cognition_provider(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        models = await catalog.get_models_for_cognition_provider("bedrock")
        assert len(models) == 1
        assert models[0].id == "anthropic.claude-3-sonnet-20240229-v1:0"

    @pytest.mark.asyncio
    async def test_get_models_for_openai_compatible_returns_empty(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        models = await catalog.get_models_for_cognition_provider("openai_compatible")
        assert models == []

    @pytest.mark.asyncio
    async def test_get_model_by_slug_and_id(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        entry = await catalog.get_model("openai", "gpt-4o")
        assert entry is not None
        assert entry.tool_call is True
        assert entry.context_window == 128000

    @pytest.mark.asyncio
    async def test_get_model_not_found(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        entry = await catalog.get_model("openai", "nonexistent")
        assert entry is None

    @pytest.mark.asyncio
    async def test_find_model_by_cognition_provider(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        entry = await catalog.find_model("anthropic", "claude-sonnet-4-6")
        assert entry is not None
        assert entry.reasoning is True

    @pytest.mark.asyncio
    async def test_find_model_not_found(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        entry = await catalog.find_model("openai", "nonexistent-model")
        assert entry is None

    @pytest.mark.asyncio
    async def test_find_model_openai_compatible_returns_none(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        entry = await catalog.find_model("openai_compatible", "gpt-4o")
        assert entry is None


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


class TestModelCatalogSearch:
    """Tests for the catalog search functionality."""

    @pytest.mark.asyncio
    async def test_search_by_query(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        results = await catalog.search(query="gpt")
        assert len(results) == 1
        assert results[0].id == "gpt-4o"

    @pytest.mark.asyncio
    async def test_search_by_tool_call(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        results = await catalog.search(tool_call=True)
        ids = {m.id for m in results}
        assert "gpt-4o" in ids
        assert "claude-sonnet-4-6" in ids
        assert "o1-mini" not in ids

    @pytest.mark.asyncio
    async def test_search_by_reasoning(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        results = await catalog.search(reasoning=True)
        ids = {m.id for m in results}
        assert "o1-mini" in ids
        assert "claude-sonnet-4-6" in ids
        assert "gpt-4o" not in ids

    @pytest.mark.asyncio
    async def test_search_by_provider_slug(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        results = await catalog.search(provider_slug="anthropic")
        assert len(results) == 1
        assert results[0].id == "claude-sonnet-4-6"

    @pytest.mark.asyncio
    async def test_search_combined_filters(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        results = await catalog.search(query="claude", tool_call=True)
        assert len(results) == 2  # Both anthropic and bedrock Claude models
        ids = {m.id for m in results}
        assert "claude-sonnet-4-6" in ids

    @pytest.mark.asyncio
    async def test_search_no_results(self) -> None:
        catalog = _make_catalog()
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()

        results = await catalog.search(query="zzz-nonexistent")
        assert results == []


# ---------------------------------------------------------------------------
# Cache / refresh behavior tests
# ---------------------------------------------------------------------------


class TestModelCatalogRefresh:
    """Tests for cache TTL and refresh behavior."""

    @pytest.mark.asyncio
    async def test_refresh_success(self) -> None:
        catalog = _make_catalog()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = SAMPLE_CATALOG_JSON
        mock_response.raise_for_status = MagicMock()

        with patch("server.app.llm.model_catalog.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await catalog.refresh()

        assert catalog.is_empty is False
        assert "openai" in catalog._cache

    @pytest.mark.asyncio
    async def test_refresh_failure_keeps_stale_data(self) -> None:
        catalog = _make_catalog()
        # Pre-populate cache
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic() - 7200  # 2 hours ago

        with patch("server.app.llm.model_catalog.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = Exception("Network error")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await catalog.refresh()

        # Stale data should still be available
        assert "openai" in catalog._cache
        assert len(catalog._cache["openai"]) == 2

    @pytest.mark.asyncio
    async def test_refresh_failure_with_empty_cache(self) -> None:
        catalog = _make_catalog()

        with patch("server.app.llm.model_catalog.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.side_effect = Exception("Network error")
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await catalog.refresh()

        # Should still be empty
        assert catalog.is_empty is True
        assert catalog._cache == {}

    @pytest.mark.asyncio
    async def test_ensure_loaded_triggers_refresh_when_stale(self) -> None:
        catalog = _make_catalog(ttl=1)
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic() - 2  # Expired

        mock_response = MagicMock()
        mock_response.json.return_value = SAMPLE_CATALOG_JSON
        mock_response.raise_for_status = MagicMock()

        with patch("server.app.llm.model_catalog.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.get.return_value = mock_response
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await catalog.ensure_loaded()

        mock_instance.get.assert_called_once()

    @pytest.mark.asyncio
    async def test_ensure_loaded_skips_refresh_when_fresh(self) -> None:
        catalog = _make_catalog(ttl=3600)
        catalog._parse_catalog(SAMPLE_CATALOG_JSON)
        catalog._last_refresh = time.monotonic()  # Just refreshed

        with patch("server.app.llm.model_catalog.httpx.AsyncClient") as mock_client:
            mock_instance = AsyncMock()
            mock_instance.__aenter__ = AsyncMock(return_value=mock_instance)
            mock_instance.__aexit__ = AsyncMock(return_value=None)
            mock_client.return_value = mock_instance

            await catalog.ensure_loaded()

        # Should not have attempted a network call
        mock_instance.get.assert_not_called()

    def test_is_stale_respects_ttl(self) -> None:
        catalog = _make_catalog(ttl=60)
        catalog._last_refresh = time.monotonic() - 30  # 30s ago, TTL 60s
        assert catalog.is_stale is False

        catalog._last_refresh = time.monotonic() - 90  # 90s ago, TTL 60s
        assert catalog.is_stale is True


# ---------------------------------------------------------------------------
# Singleton tests
# ---------------------------------------------------------------------------


class TestModelCatalogSingleton:
    """Tests for the global singleton pattern."""

    def test_reset_clears_singleton(self) -> None:
        reset_model_catalog()
        from server.app.llm import model_catalog

        assert model_catalog._catalog is None

    def test_get_model_catalog_creates_instance(self) -> None:
        reset_model_catalog()

        settings = MagicMock()
        settings.model_catalog_url = "https://test.example.com/api.json"
        settings.model_catalog_ttl_seconds = 1800

        with patch("server.app.settings.get_settings", return_value=settings):
            from server.app.llm.model_catalog import get_model_catalog

            catalog = get_model_catalog()
            assert catalog._catalog_url == "https://test.example.com/api.json"
            assert catalog._ttl_seconds == 1800

        reset_model_catalog()


# ---------------------------------------------------------------------------
# CatalogModel dataclass tests
# ---------------------------------------------------------------------------


class TestCatalogModel:
    """Tests for the CatalogModel frozen dataclass."""

    def test_frozen(self) -> None:
        model = CatalogModel(
            id="test",
            name="Test",
            provider_slug="test",
            family="test",
            tool_call=True,
            reasoning=False,
            context_window=128000,
            output_limit=4096,
            input_cost=1.0,
            output_cost=2.0,
        )
        with pytest.raises(AttributeError):
            model.id = "changed"  # type: ignore[misc]

    def test_default_fields(self) -> None:
        model = CatalogModel(
            id="test",
            name="Test",
            provider_slug="test",
            family="test",
            tool_call=True,
            reasoning=False,
            context_window=128000,
            output_limit=4096,
            input_cost=1.0,
            output_cost=2.0,
        )
        assert model.modalities == {}
        assert model.structured_output is None
        assert model.status is None
