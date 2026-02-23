from __future__ import annotations

from collections.abc import Callable
from typing import Any

# Registry for LLM provider factory functions
# Signature: (config: Any, settings: Any) -> Any
# 'config' is expected to have 'provider', 'model', 'api_key', 'base_url', 'region' attributes
# 'settings' is the global settings object
PROVIDER_REGISTRY: dict[str, Callable[[Any, Any], Any]] = {}


def register_provider(name: str, factory: Callable[[Any, Any], Any]) -> None:
    """Register a new LLM provider factory."""
    PROVIDER_REGISTRY[name] = factory


def get_provider_factory(name: str) -> Callable[[Any, Any], Any]:
    """Get the factory function for a provider."""
    if name not in PROVIDER_REGISTRY:
        raise ValueError(f"Unknown LLM provider: {name}")
    return PROVIDER_REGISTRY[name]


# ============================================================================
# Built-in Provider Factories
# ============================================================================


def create_openai_model(config: Any, settings: Any) -> Any:
    """Factory for OpenAI models."""
    from langchain_openai import ChatOpenAI

    api_key = getattr(config, "api_key", None)
    if not api_key and hasattr(settings, "openai_api_key") and settings.openai_api_key:
        api_key = settings.openai_api_key.get_secret_value()

    return ChatOpenAI(
        model=config.model,
        api_key=api_key,
        base_url=getattr(config, "base_url", None) or settings.openai_api_base,
    )


def create_openai_compatible_model(config: Any, settings: Any) -> Any:
    """Factory for OpenAI-compatible models."""
    from langchain_openai import ChatOpenAI

    api_key = getattr(config, "api_key", None)
    if not api_key:
        api_key = settings.openai_compatible_api_key.get_secret_value()

    base_url = getattr(config, "base_url", None) or settings.openai_compatible_base_url

    return ChatOpenAI(
        model=config.model,
        api_key=api_key,
        base_url=base_url,
    )


def create_bedrock_model(config: Any, settings: Any) -> Any:
    """Factory for AWS Bedrock models."""
    from langchain_aws import ChatBedrock

    aws_access_key = None
    aws_secret_key = None
    if settings.aws_access_key_id:
        aws_access_key = settings.aws_access_key_id.get_secret_value()
    if settings.aws_secret_access_key:
        aws_secret_key = settings.aws_secret_access_key.get_secret_value()

    return ChatBedrock(
        model_id=config.model,
        region_name=getattr(config, "region", None) or settings.aws_region,
        aws_access_key_id=aws_access_key,
        aws_secret_access_key=aws_secret_key,
    )


def create_mock_model(config: Any, settings: Any) -> Any:
    """Factory for Mock LLM models."""
    from server.app.llm.mock import MockLLM

    return MockLLM()


# Register built-ins
register_provider("openai", create_openai_model)
register_provider("openai_compatible", create_openai_compatible_model)
register_provider("bedrock", create_bedrock_model)
register_provider("mock", create_mock_model)
