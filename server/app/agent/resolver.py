"""RuntimeResolver: builds live Python objects from ConfigStore.

RuntimeResolver is the CQRS "read" side optimized for the Agent Runtime.
It takes a ConfigStore as a dependency and produces the live Python objects
that deepagents expects: BaseTool instances, middleware objects, BaseChatModel
instances, and AgentDefinition lookups.

Layer: 4 (Agent Runtime)

This replaces the scattered resolution logic that was previously in:
- ``_load_config_registry_tools()`` in deep_agent_service.py
- ``_resolve_provider_config()`` / ``_build_model()`` in deep_agent_service.py
- ``AgentRegistry.create_tools()`` / ``create_middleware()``
- ``AgentDefinitionRegistry.get()``

Cognition does NOT roll its own agent logic — this is purely a
configuration bridge from ConfigStore → deepagents primitives.
"""

from __future__ import annotations

import importlib
import inspect
import os
from typing import Any, cast

import structlog
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

from server.app.agent.definition import AgentDefinition
from server.app.exceptions import LLMProviderConfigError
from server.app.settings import Settings
from server.app.storage.config_store import ConfigStore

logger = structlog.get_logger(__name__)

_TEST_ONLY_PROVIDERS = {"mock"}


class RuntimeResolver:
    """Resolves ConfigStore data into live Python objects for the agent runtime.

    Takes ConfigStore as a dependency and provides:
    - ``build_tools()``: Load BaseTool instances from all sources
    - ``build_model()``: Build a BaseChatModel from resolved provider config
    - ``get_agent_definition()``: Look up an AgentDefinition by name
    - ``resolve_provider_config()``: Walk the provider resolution priority chain
    """

    def __init__(self, config_store: ConfigStore, settings: Settings) -> None:
        self._store = config_store
        self._settings = settings

    # ------------------------------------------------------------------
    # Tool resolution
    # ------------------------------------------------------------------

    async def build_tools(
        self,
        scope: dict[str, str] | None = None,
        extra_tools: list[Any] | None = None,
    ) -> list[Any]:
        """Build BaseTool instances from all sources.

        Sources (in priority order):
        1. extra_tools: Programmatically provided tools (from AgentRegistry, etc.)
        2. ConfigStore tools: API-registered tools (code or module path)

        Args:
            scope: Scope dict for ConfigStore lookup.
            extra_tools: Additional tools to include (e.g., from AgentRegistry).

        Returns:
            List of BaseTool instances.
        """
        tools: list[Any] = list(extra_tools) if extra_tools else []

        try:
            registrations = await self._store.list_tools(scope)
        except Exception:
            logger.debug("ConfigStore unavailable — skipping API-registered tools")
            return tools

        for reg_tool in registrations:
            if not reg_tool.enabled:
                continue
            try:
                if reg_tool.code:
                    namespace: dict[str, Any] = {}
                    exec(compile(reg_tool.code, reg_tool.name, "exec"), namespace)  # noqa: S102
                    for obj in namespace.values():
                        if (
                            isinstance(obj, BaseTool)
                            or callable(obj)
                            and hasattr(obj, "name")
                            and hasattr(obj, "run")
                        ):
                            tools.append(obj)
                elif reg_tool.path:
                    module = importlib.import_module(reg_tool.path)
                    for _, obj in inspect.getmembers(module):
                        if isinstance(obj, BaseTool):
                            tools.append(obj)
            except Exception:
                logger.warning(
                    "Failed to load ConfigStore tool — skipping",
                    tool_name=reg_tool.name,
                    source_type="api_code" if reg_tool.code else "api_path",
                    exc_info=True,
                )

        return tools

    # ------------------------------------------------------------------
    # Agent definition resolution
    # ------------------------------------------------------------------

    async def get_agent_definition(
        self, name: str, scope: dict[str, str] | None = None
    ) -> AgentDefinition | None:
        """Look up an AgentDefinition by name.

        Checks built-in/file agents first, then DB-seeded agents.

        Args:
            name: Agent name to look up.
            scope: Optional scope for resolution.

        Returns:
            AgentDefinition if found, None otherwise.
        """
        return await self._store.get_agent_definition(name, scope)

    async def is_valid_primary(self, name: str, scope: dict[str, str] | None = None) -> bool:
        """Check if an agent name is a valid primary agent."""
        return await self._store.is_valid_primary(name, scope)

    # ------------------------------------------------------------------
    # Provider / model resolution
    # ------------------------------------------------------------------

    async def resolve_provider_config(
        self,
        provider_id: str | None = None,
        provider_type: str | None = None,
        model_id: str | None = None,
        scope: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Walk the provider resolution priority chain.

        Priority:
        1. provider_id: Exact match from ConfigStore
        2. provider_type + model_id: Filter ConfigStore by type/model
        3. List all providers from ConfigStore, pick highest priority
        4. Global defaults from ConfigStore

        Args:
            provider_id: Exact provider config ID from SessionConfig.
            provider_type: Provider type (e.g., "openai", "anthropic").
            model_id: Model identifier to match.
            scope: Scope dict for ConfigStore lookup.

        Returns:
            Dict with resolved provider config fields.

        Raises:
            LLMProviderConfigError: If no provider can be resolved.
        """
        providers = await self._store.list_providers(scope)

        if provider_id:
            for p in providers:
                if p.id == provider_id:
                    return self._provider_to_dict(p)
            raise LLMProviderConfigError(
                provider=provider_id,
                reason=f"Provider config '{provider_id}' not found in scope",
            )

        if provider_type or model_id:
            candidates = []
            for p in providers:
                if provider_type and p.provider != provider_type:
                    continue
                if model_id and p.model and p.model != model_id:
                    continue
                if not p.enabled:
                    continue
                candidates.append(p)

            if candidates:
                candidates.sort(key=lambda p: p.priority or 0, reverse=True)
                return self._provider_to_dict(candidates[0])

        if providers:
            enabled = [p for p in providers if p.enabled]
            if enabled:
                enabled.sort(key=lambda p: p.priority or 0, reverse=True)
                return self._provider_to_dict(enabled[0])

        defaults = await self._store.get_global_provider_defaults(scope)
        if defaults.provider or defaults.model:
            return {
                "provider": defaults.provider,
                "model": defaults.model,
                "max_tokens": defaults.max_tokens,
                "api_key": None,
                "api_key_env": None,
                "base_url": None,
                "region": None,
                "role_arn": None,
            }

        raise LLMProviderConfigError(
            provider="unknown",
            reason="No providers configured. Use POST /models/providers or set COGNITION_LLM_PROVIDER.",
        )

    def build_model(
        self,
        provider: str,
        model_id: str,
        api_key: str | None = None,
        base_url: str | None = None,
        region: str | None = None,
        role_arn: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
        timeout: int | None = None,
    ) -> BaseChatModel:
        """Build a LangChain BaseChatModel from resolved provider config.

        Uses LangChain's init_chat_model for native provider support.

        Args:
            provider: Provider type.
            model_id: Model identifier.
            api_key: Optional explicit API key.
            base_url: Optional base URL override.
            region: AWS region (Bedrock only).
            role_arn: IAM role ARN for STS AssumeRole (Bedrock only).
            temperature: Optional temperature override.
            max_tokens: Optional max_tokens override.
            max_retries: Optional max_retries override.
            timeout: Optional timeout override.

        Returns:
            Configured BaseChatModel.

        Raises:
            LLMProviderConfigError: If provider is unknown or construction fails.
        """
        if provider == "mock":
            from server.app.llm.mock import MockLLM

            return cast(BaseChatModel, MockLLM())

        try:
            if provider == "openai":
                resolved_key = api_key
                if not resolved_key and self._settings.openai_api_key:
                    resolved_key = self._settings.openai_api_key.get_secret_value()
                kwargs: dict[str, Any] = {"model_provider": "openai"}
                if resolved_key:
                    kwargs["api_key"] = resolved_key
                if base_url or self._settings.openai_api_base:
                    kwargs["base_url"] = base_url or self._settings.openai_api_base
                if temperature is not None:
                    kwargs["temperature"] = temperature
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if max_retries is not None:
                    kwargs["max_retries"] = max_retries
                if timeout is not None:
                    kwargs["timeout"] = timeout
                return cast(BaseChatModel, init_chat_model(model_id, **kwargs))

            elif provider == "anthropic":
                kwargs = {"model_provider": "anthropic"}
                if api_key:
                    kwargs["api_key"] = api_key
                if temperature is not None:
                    kwargs["temperature"] = temperature
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if max_retries is not None:
                    kwargs["max_retries"] = max_retries
                if timeout is not None:
                    kwargs["timeout"] = timeout
                return cast(BaseChatModel, init_chat_model(model_id, **kwargs))

            elif provider == "bedrock":
                return self._build_bedrock_model(
                    model_id=model_id,
                    region=region,
                    role_arn=role_arn,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    max_retries=max_retries,
                    timeout=timeout,
                )

            elif provider == "openai_compatible":
                resolved_key = api_key
                if not resolved_key:
                    resolved_key = self._settings.openai_compatible_api_key.get_secret_value()
                resolved_base_url = base_url or self._settings.openai_compatible_base_url
                if not resolved_base_url:
                    raise LLMProviderConfigError(
                        provider=provider,
                        reason="base_url is required for openai_compatible provider.",
                    )
                compat_kwargs: dict[str, Any] = {
                    "model_provider": "openai",
                    "base_url": resolved_base_url,
                    "api_key": resolved_key,
                }
                if temperature is not None:
                    compat_kwargs["temperature"] = temperature
                if max_tokens is not None:
                    compat_kwargs["max_tokens"] = max_tokens
                if max_retries is not None:
                    compat_kwargs["max_retries"] = max_retries
                if timeout is not None:
                    compat_kwargs["timeout"] = timeout
                return cast(BaseChatModel, init_chat_model(model_id, **compat_kwargs))

            elif provider == "google_genai":
                kwargs = {"model_provider": "google_genai"}
                if api_key:
                    kwargs["api_key"] = api_key
                if temperature is not None:
                    kwargs["temperature"] = temperature
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if max_retries is not None:
                    kwargs["max_retries"] = max_retries
                if timeout is not None:
                    kwargs["timeout"] = timeout
                return cast(BaseChatModel, init_chat_model(model_id, **kwargs))

            elif provider == "google_vertexai":
                kwargs = {"model_provider": "google_vertexai"}
                if temperature is not None:
                    kwargs["temperature"] = temperature
                if max_tokens is not None:
                    kwargs["max_tokens"] = max_tokens
                if max_retries is not None:
                    kwargs["max_retries"] = max_retries
                if timeout is not None:
                    kwargs["timeout"] = timeout
                return cast(BaseChatModel, init_chat_model(model_id, **kwargs))

            else:
                raise LLMProviderConfigError(
                    provider=provider,
                    reason=f"Unknown provider '{provider}'.",
                )

        except LLMProviderConfigError:
            raise
        except Exception as exc:
            raise LLMProviderConfigError(
                provider=provider,
                reason=str(exc),
            ) from exc

    def _build_bedrock_model(
        self,
        model_id: str,
        region: str | None,
        role_arn: str | None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        max_retries: int | None = None,
        timeout: int | None = None,
    ) -> BaseChatModel:
        from botocore.config import Config
        from langchain_aws import ChatBedrock

        resolved_region = region or self._settings.aws_region
        botocore_config = Config(
            read_timeout=timeout or 120,
            connect_timeout=10,
            retries={"max_attempts": max_retries + 1, "mode": "standard"}
            if max_retries is not None
            else None,
        )

        kwargs: dict[str, Any] = {
            "model_id": model_id,
            "region_name": resolved_region,
            "config": botocore_config,
        }
        model_kwargs: dict[str, Any] = {}
        if temperature is not None:
            model_kwargs["temperature"] = temperature
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if model_kwargs:
            kwargs["model_kwargs"] = model_kwargs

        resolved_role_arn = role_arn or getattr(self._settings, "bedrock_role_arn", None)

        if resolved_role_arn:
            import boto3

            sts = boto3.client("sts", region_name=resolved_region)
            assumed = sts.assume_role(
                RoleArn=resolved_role_arn,
                RoleSessionName="cognition-bedrock-session",
            )
            creds = assumed["Credentials"]
            kwargs["aws_access_key_id"] = creds["AccessKeyId"]
            kwargs["aws_secret_access_key"] = creds["SecretAccessKey"]
            kwargs["aws_session_token"] = creds["SessionToken"]
        else:
            aws_access_key = (
                self._settings.aws_access_key_id.get_secret_value()
                if self._settings.aws_access_key_id
                else None
            )
            aws_secret_key = (
                self._settings.aws_secret_access_key.get_secret_value()
                if self._settings.aws_secret_access_key
                else None
            )
            aws_session_token = (
                self._settings.aws_session_token.get_secret_value()
                if self._settings.aws_session_token
                else None
            )

            if aws_access_key and aws_secret_key:
                kwargs["aws_access_key_id"] = aws_access_key
                kwargs["aws_secret_access_key"] = aws_secret_key
                if aws_session_token:
                    kwargs["aws_session_token"] = aws_session_token
            elif aws_access_key or aws_secret_key:
                raise LLMProviderConfigError(
                    provider="bedrock",
                    reason="Both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set together.",
                )

        return ChatBedrock(**kwargs)

    @staticmethod
    def _provider_to_dict(p: Any) -> dict[str, Any]:
        api_key = None
        if p.api_key_env:
            api_key = os.environ.get(p.api_key_env)
        return {
            "provider": p.provider,
            "model": p.model,
            "max_tokens": p.max_tokens,
            "api_key": api_key,
            "api_key_env": p.api_key_env,
            "base_url": p.base_url,
            "region": p.region,
            "role_arn": p.role_arn,
        }


__all__ = ["RuntimeResolver"]
