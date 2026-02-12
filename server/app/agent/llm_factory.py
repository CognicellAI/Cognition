"""LLM provider factory supporting OpenAI, Anthropic, and AWS Bedrock."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_aws import ChatBedrock
from langchain_openai import ChatOpenAI

from server.app.agent.bedrock_client import BedrockClientFactory
from server.app.settings import get_settings

if TYPE_CHECKING:
    from langchain_core.language_models.chat_models import BaseChatModel

    from server.app.settings import Settings

logger = structlog.get_logger()


class LLMProviderFactory:
    """Factory for creating LLM clients based on configuration."""

    SUPPORTED_PROVIDERS: ClassVar[set[str]] = {
        "openai",
        "openai_compatible",
        "anthropic",
        "bedrock",
    }

    @classmethod
    def create_llm(cls, settings: Settings | None = None) -> BaseChatModel:
        """Create an LLM client based on settings.

        Args:
            settings: Application settings (uses default if None)

        Returns:
            Configured LLM client

        Raises:
            ValueError: If provider not supported or configuration invalid
            RuntimeError: If failed to create LLM client
        """
        settings = settings or get_settings()
        provider = settings.llm_provider.lower()

        if provider not in cls.SUPPORTED_PROVIDERS:
            raise ValueError(
                f"Unsupported LLM provider: {provider}. "
                f"Supported: {', '.join(cls.SUPPORTED_PROVIDERS)}"
            )

        logger.info("Creating LLM client", provider=provider, model=settings.default_model)

        if provider == "openai":
            return cls._create_openai_llm(settings)
        elif provider == "openai_compatible":
            return cls._create_openai_compatible_llm(settings)
        elif provider == "anthropic":
            return cls._create_anthropic_llm(settings)
        elif provider == "bedrock":
            return cls._create_bedrock_llm(settings)

        raise RuntimeError(f"Failed to create LLM for provider: {provider}")

    @staticmethod
    def _create_openai_llm(settings: Settings) -> ChatOpenAI:
        """Create OpenAI LLM client.

        Args:
            settings: Application settings

        Returns:
            ChatOpenAI instance

        Raises:
            ValueError: If OpenAI API key not configured
        """
        if not settings.openai_api_key:
            raise ValueError(
                "OpenAI API key not configured. Set OPENAI_API_KEY environment variable."
            )

        return ChatOpenAI(
            model=settings.default_model,
            api_key=settings.openai_api_key,
            temperature=0.1,
            max_tokens=4096,
        )

    @staticmethod
    def _create_openai_compatible_llm(settings: Settings) -> ChatOpenAI:
        """Create OpenAI-compatible LLM client (e.g., LiteLLM, vLLM, Ollama).

        Args:
            settings: Application settings

        Returns:
            ChatOpenAI instance configured for custom base URL

        Raises:
            ValueError: If OpenAI-compatible API base URL not configured
        """
        if not settings.openai_api_base:
            raise ValueError(
                "OpenAI-compatible API base URL not configured. "
                "Set OPENAI_API_BASE environment variable (e.g., http://localhost:8000/v1)."
            )

        logger.info(
            "Creating OpenAI-compatible LLM client",
            base_url=settings.openai_api_base,
            model=settings.default_model,
        )

        # For OpenAI-compatible APIs, we use ChatOpenAI with a custom base_url
        # This works with LiteLLM, vLLM, Ollama, LocalAI, etc.
        return ChatOpenAI(
            model=settings.default_model,
            api_key=settings.openai_api_key or "not-needed",
            base_url=settings.openai_api_base,
            temperature=0.1,
            max_tokens=4096,
        )

    @staticmethod
    def _create_anthropic_llm(settings: Settings) -> ChatAnthropic:
        """Create Anthropic LLM client.

        Args:
            settings: Application settings

        Returns:
            ChatAnthropic instance

        Raises:
            ValueError: If Anthropic API key not configured
        """
        if not settings.anthropic_api_key:
            raise ValueError(
                "Anthropic API key not configured. Set ANTHROPIC_API_KEY environment variable."
            )

        # Use default model or extract from settings
        model = settings.default_model
        if "claude" not in model.lower():
            # Default to Claude if model doesn't look like a Claude model
            model = "claude-3-opus-20240229"

        return ChatAnthropic(
            model=model,
            api_key=settings.anthropic_api_key,
            temperature=0.1,
            max_tokens=4096,
        )

    @staticmethod
    def _create_bedrock_llm(settings: Settings) -> ChatBedrock:
        """Create AWS Bedrock LLM client.

        Args:
            settings: Application settings

        Returns:
            ChatBedrock instance

        Raises:
            ValueError: If Bedrock credentials not configured
            RuntimeError: If failed to create Bedrock client
        """
        if not settings.bedrock_has_credentials:
            raise ValueError(
                "AWS Bedrock credentials not configured. "
                "Set AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY, "
                "or AWS_PROFILE, or USE_BEDROCK_IAM_ROLE=true."
            )

        try:
            # Create Bedrock client using factory
            bedrock_client = BedrockClientFactory.create_bedrock_client(settings)

            # Create LangChain Bedrock chat model
            return ChatBedrock(
                client=bedrock_client,
                model_id=settings.bedrock_model_id,
                region_name=settings.aws_region,
                model_kwargs={
                    "temperature": 0.1,
                    "max_tokens": 4096,
                },
            )
        except Exception as e:
            logger.error("Failed to create Bedrock LLM", error=str(e))
            raise RuntimeError(f"Failed to create Bedrock LLM: {e}") from e


def get_llm() -> BaseChatModel:
    """Get the configured LLM client.

    Returns:
        Configured LLM client
    """
    return LLMProviderFactory.create_llm()
