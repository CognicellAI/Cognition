"""Unit tests for model_registry module."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from server.app.llm.model_registry import (
    ModelCost,
    ModelInfo,
    ModelLimits,
    ModelRegistry,
    ProviderInfo,
)


# Minimal mock API response
MOCK_API_DATA = {
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "env": ["ANTHROPIC_API_KEY"],
        "npm": "@ai-sdk/anthropic",
        "doc": "https://docs.anthropic.com",
        "models": {
            "claude-opus-4-6": {
                "id": "claude-opus-4-6",
                "name": "Claude Opus 4.6",
                "family": "claude-opus",
                "tool_call": True,
                "reasoning": True,
                "attachment": True,
                "temperature": True,
                "knowledge": "2025-05",
                "release_date": "2026-02-05",
                "open_weights": False,
                "cost": {
                    "input": 5.0,
                    "output": 25.0,
                    "cache_read": 0.5,
                    "cache_write": 6.25,
                },
                "limit": {
                    "context": 200000,
                    "output": 128000,
                },
                "modalities": {
                    "input": ["text", "image", "pdf"],
                    "output": ["text"],
                },
            },
            "claude-sonnet-4-20250514": {
                "id": "claude-sonnet-4-20250514",
                "name": "Claude Sonnet 4",
                "family": "claude-sonnet",
                "tool_call": True,
                "reasoning": False,
                "attachment": True,
                "temperature": True,
                "cost": {
                    "input": 3.0,
                    "output": 15.0,
                },
                "limit": {
                    "context": 200000,
                    "output": 64000,
                },
                "modalities": {
                    "input": ["text", "image"],
                    "output": ["text"],
                },
            },
        },
    },
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
                "tool_call": True,
                "reasoning": False,
                "attachment": True,
                "structured_output": True,
                "temperature": True,
                "cost": {
                    "input": 2.5,
                    "output": 10.0,
                    "cache_read": 1.25,
                },
                "limit": {
                    "context": 128000,
                    "output": 16384,
                },
                "modalities": {
                    "input": ["text", "image"],
                    "output": ["text"],
                },
            },
            "o1-deprecated": {
                "id": "o1-deprecated",
                "name": "O1 (Deprecated)",
                "tool_call": False,
                "reasoning": True,
                "status": "deprecated",
                "cost": {"input": 15.0, "output": 60.0},
                "limit": {"context": 200000, "output": 100000},
                "modalities": {"input": ["text"], "output": ["text"]},
            },
        },
    },
}


class TestModelCost:
    """Test ModelCost dataclass."""

    def test_defaults(self):
        """Test default cost values."""
        cost = ModelCost()
        assert cost.input == 0.0
        assert cost.output == 0.0
        assert cost.cache_read is None

    def test_estimate_basic(self):
        """Test basic cost estimation."""
        cost = ModelCost(input=5.0, output=25.0)
        # 1M input + 500k output
        result = cost.estimate(input_tokens=1_000_000, output_tokens=500_000)
        assert result == pytest.approx(5.0 + 12.5)

    def test_estimate_with_cache(self):
        """Test cost estimation with cached tokens."""
        cost = ModelCost(input=5.0, output=25.0, cache_read=0.5)
        result = cost.estimate(
            input_tokens=500_000,
            output_tokens=100_000,
            cached_tokens=500_000,
        )
        expected = (500_000 / 1e6) * 5.0 + (100_000 / 1e6) * 25.0 + (500_000 / 1e6) * 0.5
        assert result == pytest.approx(expected)

    def test_estimate_zero_tokens(self):
        """Test cost estimation with zero tokens."""
        cost = ModelCost(input=5.0, output=25.0)
        assert cost.estimate(0, 0) == 0.0


class TestModelLimits:
    """Test ModelLimits dataclass."""

    def test_effective_input_with_explicit(self):
        """Test effective_input when input is explicitly set."""
        limits = ModelLimits(context=200000, output=128000, input=150000)
        assert limits.effective_input == 150000

    def test_effective_input_computed(self):
        """Test effective_input computed from context - output."""
        limits = ModelLimits(context=200000, output=128000)
        assert limits.effective_input == 72000

    def test_effective_input_zero_context(self):
        """Test effective_input with zero context."""
        limits = ModelLimits(context=0, output=0)
        assert limits.effective_input == 0


class TestModelInfo:
    """Test ModelInfo dataclass."""

    def test_qualified_id(self):
        """Test qualified_id property."""
        model = ModelInfo(
            id="gpt-4o",
            name="GPT-4o",
            provider_id="openai",
            provider_name="OpenAI",
        )
        assert model.qualified_id == "openai/gpt-4o"


class TestModelRegistry:
    """Test ModelRegistry class."""

    @pytest.fixture
    def registry(self, tmp_path):
        """Create a registry with mock data loaded."""
        reg = ModelRegistry(cache_dir=tmp_path)
        reg._parse_api_data(MOCK_API_DATA)
        reg._last_fetched = 999999999999.0
        return reg

    def test_parse_providers(self, registry):
        """Test that providers are parsed correctly."""
        assert len(registry._providers) == 2
        anthropic = registry.get_provider("anthropic")
        assert anthropic is not None
        assert anthropic.name == "Anthropic"
        assert "ANTHROPIC_API_KEY" in anthropic.env

    def test_parse_models(self, registry):
        """Test that models are parsed correctly."""
        # 2 anthropic + 2 openai = 4
        assert len(registry._models) == 4

    def test_get_model(self, registry):
        """Test model lookup by provider and model ID."""
        model = registry.get_model("anthropic", "claude-opus-4-6")
        assert model is not None
        assert model.name == "Claude Opus 4.6"
        assert model.tool_call is True
        assert model.reasoning is True
        assert model.cost.input == 5.0
        assert model.limits.context == 200000

    def test_get_model_not_found(self, registry):
        """Test model lookup for nonexistent model."""
        assert registry.get_model("anthropic", "nonexistent") is None

    def test_get_model_by_qualified_id(self, registry):
        """Test lookup by qualified ID."""
        model = registry.get_model_by_qualified_id("openai/gpt-4o")
        assert model is not None
        assert model.name == "GPT-4o"

    def test_list_models_all(self, registry):
        """Test listing all non-deprecated models."""
        models = registry.list_models()
        # Excludes the deprecated o1 model
        assert len(models) == 3

    def test_list_models_by_provider(self, registry):
        """Test listing models filtered by provider."""
        models = registry.list_models(provider="anthropic")
        assert len(models) == 2
        assert all(m.provider_id == "anthropic" for m in models)

    def test_list_models_by_tool_call(self, registry):
        """Test listing models filtered by tool_call support."""
        models = registry.list_models(tool_call=True)
        assert all(m.tool_call for m in models)
        assert len(models) == 3

    def test_list_models_by_reasoning(self, registry):
        """Test listing models filtered by reasoning support."""
        models = registry.list_models(reasoning=True)
        assert all(m.reasoning for m in models)
        assert len(models) == 1
        assert models[0].id == "claude-opus-4-6"

    def test_list_models_by_min_context(self, registry):
        """Test listing models with minimum context window."""
        models = registry.list_models(min_context=200000)
        assert all(m.limits.context >= 200000 for m in models)

    def test_list_deprecated(self, registry):
        """Test listing deprecated models explicitly."""
        models = registry.list_models(status="deprecated")
        assert len(models) == 1
        assert models[0].id == "o1-deprecated"

    def test_search_models(self, registry):
        """Test searching models by name."""
        results = registry.search_models("claude")
        assert len(results) == 2

    def test_search_models_by_qualified_id(self, registry):
        """Test searching models by qualified ID."""
        results = registry.search_models("openai/gpt")
        assert len(results) == 1
        assert results[0].id == "gpt-4o"

    def test_search_case_insensitive(self, registry):
        """Test that search is case-insensitive."""
        results = registry.search_models("GPT")
        assert len(results) == 1

    def test_search_no_results(self, registry):
        """Test search with no matches."""
        results = registry.search_models("nonexistent-model")
        assert len(results) == 0

    def test_is_loaded(self, registry):
        """Test is_loaded property."""
        assert registry.is_loaded is True

    def test_not_loaded(self, tmp_path):
        """Test is_loaded when empty."""
        reg = ModelRegistry(cache_dir=tmp_path)
        assert reg.is_loaded is False

    def test_list_providers(self, registry):
        """Test listing all providers."""
        providers = registry.list_providers()
        assert len(providers) == 2
        names = {p.name for p in providers}
        assert "Anthropic" in names
        assert "OpenAI" in names

    def test_cache_file_written(self, tmp_path):
        """Test that parse + cache path works."""
        reg = ModelRegistry(cache_dir=tmp_path)
        reg._parse_api_data(MOCK_API_DATA)

        # Write cache manually
        cache_file = tmp_path / "models_dev.json"
        cache_file.write_text(json.dumps(MOCK_API_DATA))

        # New registry should be able to read cache
        reg2 = ModelRegistry(cache_dir=tmp_path, cache_ttl=99999)
        assert reg2.is_loaded is False  # Not loaded yet


class TestModelInfoModalities:
    """Test model modality handling."""

    def test_default_modalities(self):
        """Test default input/output modalities."""
        model = ModelInfo(
            id="test",
            name="Test",
            provider_id="test",
            provider_name="Test",
        )
        assert model.input_modalities == ("text",)
        assert model.output_modalities == ("text",)

    def test_multimodal(self):
        """Test multimodal model."""
        model = ModelInfo(
            id="test",
            name="Test",
            provider_id="test",
            provider_name="Test",
            input_modalities=("text", "image", "pdf"),
            output_modalities=("text",),
        )
        assert "image" in model.input_modalities
        assert "pdf" in model.input_modalities
