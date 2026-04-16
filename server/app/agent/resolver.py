"""RuntimeResolver bridges ConfigStore configuration into Deep Agents primitives."""

from __future__ import annotations

import importlib
import inspect
import os
from dataclasses import dataclass
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


@dataclass(frozen=True)
class SelectedModelTarget:
    provider: str
    model_id: str
    provider_id: str | None = None
    base_url: str | None = None
    region: str | None = None
    role_arn: str | None = None
    api_key_env: str | None = None
    max_retries: int | None = None
    timeout: int | None = None


@dataclass(frozen=True)
class ResolvedModelConfig:
    provider: str
    model_id: str
    api_key: str | None = None
    base_url: str | None = None
    region: str | None = None
    role_arn: str | None = None
    recursion_limit: int = 1000
    temperature: float | None = None
    max_tokens: int | None = None
    max_retries: int | None = None
    timeout: int | None = None

    def build_model(self, resolver: RuntimeResolver) -> BaseChatModel:
        return resolver.build_model(
            provider=self.provider,
            model_id=self.model_id,
            api_key=self.api_key,
            base_url=self.base_url,
            region=self.region,
            role_arn=self.role_arn,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            max_retries=self.max_retries,
            timeout=self.timeout,
        )


class RuntimeResolver:
    """Resolves ConfigStore data into live Python objects for the agent runtime."""

    def __init__(self, config_store: ConfigStore | None, settings: Settings) -> None:
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
        1. extra_tools: Programmatically provided tools
        2. ConfigStore tools: API-registered tools (code or module path)

        Args:
            scope: Scope dict for ConfigStore lookup.
            extra_tools: Additional tools to include.

        Returns:
            List of BaseTool instances.
        """
        tools: list[Any] = list(extra_tools) if extra_tools else []
        if self._store is None:
            return tools

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
        if self._store is None:
            return None
        return await self._store.get_agent_definition(name, scope)

    async def is_valid_primary(self, name: str, scope: dict[str, str] | None = None) -> bool:
        """Check if an agent name is a valid primary agent."""
        if self._store is None:
            return False
        return await self._store.is_valid_primary(name, scope)

    # ------------------------------------------------------------------
    # Provider / model resolution
    # ------------------------------------------------------------------

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
        """Build a LangChain BaseChatModel from a resolved provider target."""
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

    async def resolve_mcp_configs(self, scope: dict[str, str] | None = None) -> list[Any]:
        """Load MCP server registrations from ConfigStore as McpServerConfig objects.

        Args:
            scope: Scope dict for ConfigStore lookup.

        Returns:
            List of McpServerConfig instances for enabled servers.
        """
        if self._store is None:
            return []
        try:
            from server.app.agent.mcp_client import McpServerConfig

            servers = await self._store.list_mcp_servers(scope)
            return [
                McpServerConfig(
                    name=s.name,
                    url=s.url,
                    headers=s.headers,
                    enabled=s.enabled,
                )
                for s in servers
                if s.enabled
            ]
        except RuntimeError:
            logger.warning("ConfigStore not initialized — MCP servers will not be available")
            return []

    async def resolve_model_for_session(
        self,
        session: Any,
        scope: dict[str, str] | None = None,
        agent_def: Any | None = None,
    ) -> tuple[BaseChatModel, str, str, int]:
        """Resolve provider config and build a BaseChatModel for a session.

        Combines provider resolution priority chain with model building.

        Args:
            session: Session object (may be None in tests).
            scope: Scope dict for ConfigStore lookup.
            agent_def: Optional AgentDefinition whose config acts as an override
                tier between GlobalProviderDefaults and SessionConfig.

        Returns:
            (model, provider_name, model_id, recursion_limit)

        Raises:
            LLMProviderConfigError: If the provider is misconfigured.
        """
        resolved = await self.resolve_model_config_for_session(
            session=session,
            scope=scope,
            agent_def=agent_def,
        )

        if resolved.provider in _TEST_ONLY_PROVIDERS:
            raise LLMProviderConfigError(
                provider=resolved.provider,
                reason=(
                    f"Provider '{resolved.provider}' is reserved for testing and cannot be used in "
                    "production. Configure a real provider via POST /models/providers."
                ),
            )

        model = resolved.build_model(self)
        await self._warn_if_no_tool_call_support(resolved.provider, resolved.model_id)

        return model, resolved.provider, resolved.model_id, resolved.recursion_limit

    async def resolve_model_config_for_session(
        self,
        session: Any,
        scope: dict[str, str] | None = None,
        agent_def: Any | None = None,
    ) -> ResolvedModelConfig:
        target = await self.select_model_target_for_session(
            session=session,
            scope=scope,
            agent_def=agent_def,
        )
        recursion_limit = self._resolve_recursion_limit(session=session, agent_def=agent_def)
        return ResolvedModelConfig(
            provider=target.provider,
            model_id=target.model_id,
            api_key=os.environ.get(target.api_key_env) if target.api_key_env else None,
            base_url=target.base_url,
            region=target.region,
            role_arn=target.role_arn,
            recursion_limit=recursion_limit,
            max_retries=target.max_retries,
            timeout=target.timeout,
            temperature=self._resolve_temperature(session=session, agent_def=agent_def),
            max_tokens=self._resolve_max_tokens(session=session, agent_def=agent_def),
        )

    async def select_model_target_for_session(
        self,
        session: Any,
        scope: dict[str, str] | None,
        agent_def: Any | None = None,
    ) -> SelectedModelTarget:
        """Select the canonical model target for a session.

        Priority:
        1. ``session.config.provider_id``
        2. ``session.config.provider`` + ``session.config.model``
        3. ``agent_def.config.provider`` + ``agent_def.config.model``
        4. First enabled provider config by ascending priority
        """
        if session and session.config and session.config.provider_id:
            return await self._select_provider_row_by_id(
                provider_id=session.config.provider_id,
                scope=scope,
            )

        if session and session.config and session.config.provider:
            prov = session.config.provider
            mod = session.config.model
            if not mod:
                raise LLMProviderConfigError(
                    provider=prov,
                    reason=(
                        "Session config specifies provider but no model. "
                        "Set SessionConfig.model alongside SessionConfig.provider."
                    ),
                )
            return SelectedModelTarget(
                provider=prov,
                model_id=mod,
            )

        if agent_def and agent_def.config and agent_def.config.provider:
            provider = agent_def.config.provider
            model_id = agent_def.config.model
            if not model_id:
                raise LLMProviderConfigError(
                    provider=provider,
                    reason=(
                        "Agent config specifies provider but no model. "
                        "Set AgentConfig.model alongside AgentConfig.provider."
                    ),
                )
            logger.debug(
                "Using agent-level provider override",
                provider=provider,
                model=model_id,
            )
            return SelectedModelTarget(provider=provider, model_id=model_id)

        if self._store is None:
            raise LLMProviderConfigError(
                provider="unknown",
                reason="ConfigStore not initialized.",
            )

        try:
            provider_configs = await self._store.list_providers(scope=scope)
            enabled = [pc for pc in provider_configs if pc.enabled]
            if enabled:
                enabled.sort(key=lambda pc: pc.priority)
                chosen = enabled[0]
                logger.debug(
                    "Provider resolved from ConfigStore",
                    provider=chosen.provider,
                    model=chosen.model,
                    id=chosen.id,
                )
                return SelectedModelTarget(
                    provider=chosen.provider,
                    model_id=chosen.model,
                    provider_id=chosen.id,
                    base_url=chosen.base_url,
                    region=chosen.region,
                    role_arn=chosen.role_arn,
                    api_key_env=chosen.api_key_env,
                    max_retries=chosen.max_retries,
                    timeout=chosen.timeout,
                )
        except RuntimeError:
            logger.warning("ConfigStore not initialized — cannot resolve provider configuration")

        raise LLMProviderConfigError(
            provider="none",
            reason=(
                "No provider configuration found. "
                "Create a provider via POST /models/providers. "
                "Providers with an empty scope are visible to all users."
            ),
        )

    async def _select_provider_row_by_id(
        self,
        provider_id: str,
        scope: dict[str, str] | None,
    ) -> SelectedModelTarget:
        if self._store is None:
            raise LLMProviderConfigError(
                provider=provider_id,
                reason="ConfigStore not initialized — cannot resolve provider_id.",
            )

        try:
            provider_config = await self._store.get_provider(provider_id, scope=scope)
        except RuntimeError as exc:
            raise LLMProviderConfigError(
                provider=provider_id,
                reason="ConfigStore not initialized — cannot resolve provider_id.",
            ) from exc

        if provider_config is None:
            raise LLMProviderConfigError(
                provider=provider_id,
                reason=(
                    f"Provider config '{provider_id}' not found in ConfigStore. "
                    "Check that the provider_id matches an existing provider config ID."
                ),
            )
        if not provider_config.enabled:
            raise LLMProviderConfigError(
                provider=provider_id,
                reason=(
                    f"Provider config '{provider_id}' is disabled. "
                    "Enable it via PATCH /models/providers/{id} or choose another."
                ),
            )

        logger.debug(
            "Provider resolved from session provider_id",
            provider_id=provider_id,
            provider=provider_config.provider,
            model=provider_config.model,
        )
        return SelectedModelTarget(
            provider=provider_config.provider,
            model_id=provider_config.model,
            provider_id=provider_config.id,
            base_url=provider_config.base_url,
            region=provider_config.region,
            role_arn=provider_config.role_arn,
            api_key_env=provider_config.api_key_env,
            max_retries=provider_config.max_retries,
            timeout=provider_config.timeout,
        )

    @staticmethod
    def _resolve_recursion_limit(session: Any, agent_def: Any | None) -> int:
        recursion_limit = 1000
        if agent_def and agent_def.config and agent_def.config.recursion_limit is not None:
            recursion_limit = agent_def.config.recursion_limit
        if session and session.config and session.config.recursion_limit is not None:
            recursion_limit = session.config.recursion_limit
        return recursion_limit

    @staticmethod
    def _resolve_temperature(session: Any, agent_def: Any | None) -> float | None:
        if agent_def and agent_def.config and agent_def.config.temperature is not None:
            return agent_def.config.temperature
        if session and session.config and session.config.temperature is not None:
            return session.config.temperature
        return None

    @staticmethod
    def _resolve_max_tokens(session: Any, agent_def: Any | None) -> int | None:
        if agent_def and agent_def.config and agent_def.config.max_tokens is not None:
            return agent_def.config.max_tokens
        if session and session.config and session.config.max_tokens is not None:
            return session.config.max_tokens
        return None

    async def _warn_if_no_tool_call_support(self, provider: str, model_id: str) -> None:
        """Log a warning if the model catalog says this model lacks tool call support."""
        try:
            from server.app.llm.model_catalog import get_model_catalog

            catalog = get_model_catalog()
            if catalog is not None:
                entry = await catalog.find_model(provider, model_id)
            if entry is not None and not entry.tool_call:
                logger.warning(
                    "Model does not support tool calls — agent may fail or produce "
                    "unexpected results. Consider switching to a tool-capable model.",
                    model=model_id,
                    provider=provider,
                    model_family=entry.family,
                )
        except Exception:
            pass


__all__ = ["RuntimeResolver"]
