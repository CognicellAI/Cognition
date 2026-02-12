"""LLM handler for in-process agent interaction."""

from __future__ import annotations

import json
from typing import Any, AsyncGenerator

import structlog
from anthropic import AsyncAnthropic, Anthropic
from langgraph.graph import StateGraph, START, END, MessagesState
from server.app.settings import Settings

logger = structlog.get_logger()


class LLMHandler:
    """Handle LLM interactions with support for multiple providers."""

    def __init__(self, settings: Settings):
        """Initialize LLM handler with configured provider."""
        self.settings = settings
        self.provider = settings.llm_provider

        # Initialize provider clients
        if self.provider == "openai_compatible":
            # For OpenAI-compatible APIs (including local models)
            try:
                from openai import AsyncOpenAI

                self.client = AsyncOpenAI(
                    api_key=settings.openai_api_key,
                    base_url=settings.openai_api_base,
                )
            except ImportError:
                logger.warning("openai package not installed, LLM calls will fail")
                self.client = None
        elif self.provider == "bedrock":
            # Use boto3 for Bedrock
            try:
                import boto3

                self.client = boto3.client(
                    "bedrock-runtime",
                    region_name=settings.aws_region,
                )
            except ImportError:
                logger.warning("boto3 package not installed, Bedrock calls will fail")
                self.client = None
        else:
            raise ValueError(f"Unsupported LLM provider: {self.provider}")

    async def generate_response(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Generate response from LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            **kwargs: Additional parameters (temperature, max_tokens, etc)

        Returns:
            Generated response text
        """
        if self.provider == "openai_compatible":
            return await self._call_openai(messages, **kwargs)
        elif self.provider == "bedrock":
            return await self._call_bedrock(messages, **kwargs)
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def stream_response(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream response from LLM.

        Args:
            messages: List of message dicts with 'role' and 'content'
            **kwargs: Additional parameters

        Yields:
            Response chunks
        """
        if self.provider == "openai_compatible":
            async for chunk in self._stream_openai(messages, **kwargs):
                yield chunk
        elif self.provider == "bedrock":
            async for chunk in self._stream_bedrock(messages, **kwargs):
                yield chunk
        else:
            raise ValueError(f"Unsupported provider: {self.provider}")

    async def _call_openai(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Call OpenAI-compatible API."""
        if not self.client:
            raise RuntimeError("OpenAI client not initialized")

        try:
            response = await self.client.chat.completions.create(
                model=kwargs.pop("model", self.settings.default_model),
                messages=messages,
                temperature=kwargs.pop("temperature", 0.7),
                max_tokens=kwargs.pop("max_tokens", 4096),
                **kwargs,
            )
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error("OpenAI API call failed", error=str(e))
            raise

    async def _stream_openai(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream from OpenAI-compatible API."""
        if not self.client:
            raise RuntimeError("OpenAI client not initialized")

        try:
            stream = await self.client.chat.completions.create(
                model=kwargs.pop("model", self.settings.default_model),
                messages=messages,
                temperature=kwargs.pop("temperature", 0.7),
                max_tokens=kwargs.pop("max_tokens", 4096),
                stream=True,
                **kwargs,
            )
            async for chunk in stream:
                if chunk.choices[0].delta.content:
                    yield chunk.choices[0].delta.content
        except Exception as e:
            logger.error("OpenAI stream failed", error=str(e))
            raise

    async def _call_bedrock(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> str:
        """Call AWS Bedrock."""
        if not self.client:
            raise RuntimeError("Bedrock client not initialized")

        try:
            # Convert messages to Claude format
            system_prompt = ""
            formatted_messages = []
            for msg in messages:
                if msg["role"] == "system":
                    system_prompt = msg["content"]
                else:
                    formatted_messages.append(msg)

            model_id = kwargs.pop("model", self.settings.bedrock_model_id)
            temperature = kwargs.pop("temperature", 0.7)
            max_tokens = kwargs.pop("max_tokens", 4096)

            response = self.client.converse(
                modelId=model_id,
                messages=formatted_messages,
                system=[{"text": system_prompt}] if system_prompt else [],
                inferenceConfig={
                    "temperature": temperature,
                    "maxTokens": max_tokens,
                },
            )
            return response["output"]["message"]["content"][0]["text"]
        except Exception as e:
            logger.error("Bedrock API call failed", error=str(e))
            raise

    async def _stream_bedrock(
        self,
        messages: list[dict[str, str]],
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        """Stream from Bedrock (uses separate method for streaming)."""
        # For now, fallback to non-streaming
        # In production, would use Bedrock's streaming API
        response = await self._call_bedrock(messages, **kwargs)
        yield response
