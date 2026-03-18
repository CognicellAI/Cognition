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

    # Build kwargs for ChatOpenAI
    kwargs: dict[str, Any] = {
        "model": config.model,
        "api_key": api_key,
        "base_url": getattr(config, "base_url", None) or settings.openai_api_base,
    }
    # Add max_tokens if configured
    if hasattr(settings, "llm_max_tokens") and settings.llm_max_tokens is not None:
        kwargs["max_tokens"] = settings.llm_max_tokens

    return ChatOpenAI(**kwargs)


def create_openai_compatible_model(config: Any, settings: Any) -> Any:
    """Factory for OpenAI-compatible models."""
    from langchain_openai import ChatOpenAI

    api_key = getattr(config, "api_key", None)
    if not api_key:
        api_key = settings.openai_compatible_api_key.get_secret_value()

    base_url = getattr(config, "base_url", None) or settings.openai_compatible_base_url

    # Build kwargs for ChatOpenAI
    kwargs: dict[str, Any] = {
        "model": config.model,
        "api_key": api_key,
        "base_url": base_url,
    }
    # Add max_tokens if configured
    if hasattr(settings, "llm_max_tokens") and settings.llm_max_tokens is not None:
        kwargs["max_tokens"] = settings.llm_max_tokens

    return ChatOpenAI(**kwargs)


def create_bedrock_model(config: Any, settings: Any) -> Any:
    """Factory for AWS Bedrock models.

    Credential resolution order:
    1. Role assumption: if ``settings.bedrock_role_arn`` is set, Cognition calls
       ``sts:AssumeRole`` and uses the resulting temporary credentials.
    2. Explicit static keys: if both ``AWS_ACCESS_KEY_ID`` and
       ``AWS_SECRET_ACCESS_KEY`` are set (optionally with ``AWS_SESSION_TOKEN``),
       those credentials are passed directly to ChatBedrock.
    3. Ambient credential chain: if no explicit keys are set, ChatBedrock calls
       ``boto3.client()`` directly, which walks the standard boto3 chain:
       environment variables → ~/.aws/credentials → EC2 instance profile →
       ECS task role → Lambda execution role → IRSA / WebIdentity token.
    """
    from botocore.config import Config
    from langchain_aws import ChatBedrock

    region = getattr(config, "region", None) or settings.aws_region

    # ISSUE-016: Add timeout configuration to prevent stream stalls
    botocore_config = Config(
        read_timeout=120,  # 2 minute read timeout
        connect_timeout=10,  # 10 second connection timeout
    )

    # Build base kwargs — credentials added below based on which path is taken
    kwargs: dict[str, Any] = {
        "model_id": config.model,
        "region_name": region,
        "config": botocore_config,
    }

    # Path 1: Role assumption via sts:AssumeRole
    role_arn = getattr(settings, "bedrock_role_arn", None)
    if role_arn:
        import boto3

        sts = boto3.client("sts", region_name=region)
        assumed = sts.assume_role(
            RoleArn=role_arn,
            RoleSessionName="cognition-bedrock-session",
        )
        creds = assumed["Credentials"]
        kwargs["aws_access_key_id"] = creds["AccessKeyId"]
        kwargs["aws_secret_access_key"] = creds["SecretAccessKey"]
        kwargs["aws_session_token"] = creds["SessionToken"]

    else:
        # Path 2: Explicit static/temporary keys
        aws_access_key = None
        aws_secret_key = None
        aws_session_token = None
        if settings.aws_access_key_id:
            aws_access_key = settings.aws_access_key_id.get_secret_value()
        if settings.aws_secret_access_key:
            aws_secret_key = settings.aws_secret_access_key.get_secret_value()
        if getattr(settings, "aws_session_token", None):
            aws_session_token = settings.aws_session_token.get_secret_value()

        if aws_access_key and aws_secret_key:
            # Both key + secret present — inject explicitly (with optional session token)
            kwargs["aws_access_key_id"] = aws_access_key
            kwargs["aws_secret_access_key"] = aws_secret_key
            if aws_session_token:
                kwargs["aws_session_token"] = aws_session_token
        elif aws_access_key or aws_secret_key:
            # Partial credentials — fail fast with a clear message
            raise ValueError(
                "Both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set together, "
                "or both must be absent to use IAM role / ambient credentials. "
                "Only one key was provided."
            )
        # Path 3: Neither key set — omit all credential kwargs so ChatBedrock falls
        # through to boto3.client() and the full ambient credential chain.

    # Add max_tokens if configured
    if hasattr(settings, "llm_max_tokens") and settings.llm_max_tokens is not None:
        kwargs["max_tokens"] = settings.llm_max_tokens

    return ChatBedrock(**kwargs)


def create_mock_model(config: Any, settings: Any) -> Any:
    """Factory for Mock LLM models."""
    from server.app.llm.mock import MockLLM

    return MockLLM()


# Register built-ins
register_provider("openai", create_openai_model)
register_provider("openai_compatible", create_openai_compatible_model)
register_provider("bedrock", create_bedrock_model)
register_provider("mock", create_mock_model)
