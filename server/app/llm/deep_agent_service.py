"""Streaming LLM service using DeepAgents for multi-step task completion.

This service leverages deepagents' built-in capabilities:
- Automatic ReAct loop (LLM → tool → LLM until completion)
- State persistence via thread_id checkpointing
- Built-in planning via Deep Agents' TodoListMiddleware
- Context management and conversation summarization

Provider/model resolution reads from ConfigRegistry (scope-aware) and builds
a LangChain BaseChatModel via init_chat_model — the same primitive that
create_deep_agent uses internally. No custom fallback chains.
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from typing import Any, cast

import structlog
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from server.app.agent import create_cognition_agent
from server.app.agent.runtime import (
    DeepAgentRuntime,
    DelegationEvent,
    DoneEvent,
    ErrorEvent,
    InterruptEvent,
    PlanningEvent,
    StatusEvent,
    StepCompleteEvent,  # noqa: F401 — re-exported for consumers of this module
    StreamEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from server.app.agent.runtime import (
    _resolve_middleware as _resolve_single_middleware,
)
from server.app.exceptions import LLMProviderConfigError
from server.app.settings import Settings
from server.app.storage import get_storage_backend
from server.app.storage.factory import create_storage_backend

logger = structlog.get_logger(__name__)

# Providers that are only allowed in test environments.
_TEST_ONLY_PROVIDERS = {"mock"}


async def _load_config_registry_tools(scope: dict[str, str] | None) -> list[Any]:
    """Load tools registered via POST /tools from ConfigRegistry.

    For each enabled ToolRegistration:
    - ``code`` tools: executed via ``exec()`` in a fresh namespace; all
      ``BaseTool`` instances and ``@tool``-decorated functions found in that
      namespace are collected.
    - ``path`` tools: loaded via ``importlib.import_module()``; all ``BaseTool``
      instances found in the module are collected.

    Errors are logged and skipped — a single bad tool does not prevent others
    from loading.

    Security note: Tool code executes with full Python privileges. The caller
    is responsible for ensuring that ``POST /tools`` is restricted to
    authorized administrators at the Gateway/proxy layer.

    Args:
        scope: Scope dict for ConfigRegistry lookup. ``None`` or ``{}`` returns
            global tools visible to all scopes.

    Returns:
        List of ``BaseTool`` instances loaded from ConfigRegistry.
    """
    from langchain_core.tools import BaseTool

    tools: list[Any] = []

    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        registrations = await reg.list_tools(scope)
    except RuntimeError:
        logger.debug("ConfigRegistry not initialized — skipping API-registered tools")
        return tools

    for reg_tool in registrations:
        if not reg_tool.enabled:
            continue

        try:
            if reg_tool.code:
                # Source-in-DB: exec into a fresh namespace and collect BaseTool instances
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
                # Module path: import and inspect for BaseTool instances
                import importlib
                import inspect as _inspect

                module = importlib.import_module(reg_tool.path)
                for _, obj in _inspect.getmembers(module):
                    if isinstance(obj, BaseTool):
                        tools.append(obj)

        except Exception:
            logger.warning(
                "Failed to load ConfigRegistry tool — skipping",
                tool_name=reg_tool.name,
                source_type="api_code" if reg_tool.code else "api_path",
                exc_info=True,
            )

    return tools


def _resolve_middleware(specs: list[str | dict[str, Any]]) -> list[Any]:
    """Resolve a list of middleware specs to instantiated middleware objects.

    Wraps ``_resolve_single_middleware`` from runtime.py, filtering out any
    specs that fail to resolve (with a warning already logged by the inner
    function).

    Args:
        specs: List of middleware specs — each is either a well-known name
            (``"tool_retry"``, ``"tool_call_limit"``, ``"pii"``,
            ``"human_in_the_loop"``), a dotted class path, or a dict with
            a ``"name"`` key plus optional constructor kwargs.

    Returns:
        List of successfully resolved middleware instances.
    """
    resolved = []
    for spec in specs:
        instance = _resolve_single_middleware(spec)
        if instance is not None:
            resolved.append(instance)
    return resolved


def _build_model(
    provider: str,
    model_id: str,
    api_key: str | None,
    base_url: str | None,
    region: str | None,
    role_arn: str | None,
    settings: Settings,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_retries: int | None = None,
    timeout: int | None = None,
) -> BaseChatModel:
    """Build a LangChain BaseChatModel from resolved provider config.

    Uses langchain's init_chat_model for native provider support.
    For Bedrock with role_arn, performs sts:AssumeRole before constructing
    the model.

    Args:
        provider: Provider type — "openai", "anthropic", "bedrock",
            "openai_compatible", or "mock" (test only).
        model_id: Model identifier, e.g. "gpt-4o", "claude-sonnet-4-6".
        api_key: Optional explicit API key (resolved from api_key_env).
        base_url: Optional base URL override (required for openai_compatible).
        region: AWS region (Bedrock only).
        role_arn: IAM role ARN for STS AssumeRole (Bedrock only).
        settings: Application settings for credential fallbacks.

    Returns:
        Configured BaseChatModel ready for use with create_deep_agent.

    Raises:
        LLMProviderConfigError: If the provider is unknown, unsupported in
            production, or model construction fails (including auth errors).
    """
    if provider == "mock":
        # Guard: mock is test-only. In production it should never be reached
        # because _resolve_provider_config raises before we get here.
        from server.app.llm.mock import MockLLM

        return cast(BaseChatModel, MockLLM())

    try:
        if provider == "openai":
            resolved_key = api_key
            if not resolved_key and settings.openai_api_key:
                resolved_key = settings.openai_api_key.get_secret_value()
            kwargs: dict[str, Any] = {"model_provider": "openai"}
            if resolved_key:
                kwargs["api_key"] = resolved_key
            if base_url or settings.openai_api_base:
                kwargs["base_url"] = base_url or settings.openai_api_base
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
            return _build_bedrock_model(
                model_id=model_id,
                region=region,
                role_arn=role_arn,
                settings=settings,
                temperature=temperature,
                max_tokens=max_tokens,
                max_retries=max_retries,
                timeout=timeout,
            )

        elif provider == "openai_compatible":
            resolved_key = api_key
            if not resolved_key:
                resolved_key = settings.openai_compatible_api_key.get_secret_value()
            resolved_base_url = base_url or settings.openai_compatible_base_url
            if not resolved_base_url:
                raise LLMProviderConfigError(
                    provider=provider,
                    reason=(
                        "base_url is required for openai_compatible provider. "
                        "Set it on the ProviderConfig or via "
                        "COGNITION_OPENAI_COMPATIBLE_BASE_URL."
                    ),
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
                reason=(
                    f"Unknown provider '{provider}'. "
                    "Supported: openai, anthropic, bedrock, openai_compatible, "
                    "google_genai, google_vertexai."
                ),
            )

    except LLMProviderConfigError:
        raise
    except Exception as exc:
        raise LLMProviderConfigError(
            provider=provider,
            reason=str(exc),
        ) from exc


def _build_bedrock_model(
    model_id: str,
    region: str | None,
    role_arn: str | None,
    settings: Settings,
    temperature: float | None = None,
    max_tokens: int | None = None,
    max_retries: int | None = None,
    timeout: int | None = None,
) -> BaseChatModel:
    """Build a ChatBedrock model, optionally assuming an IAM role via STS.

    Credential resolution order:
    1. role_arn → sts:AssumeRole → temporary credentials
    2. Explicit static keys from settings (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
    3. Ambient boto3 chain (instance profile, ECS task role, IRSA, etc.)

    Args:
        model_id: Bedrock model ID, e.g. "anthropic.claude-3-sonnet-20240229-v1:0".
        region: AWS region. Defaults to settings.aws_region.
        role_arn: Optional IAM role ARN to assume before calling Bedrock.
        settings: Application settings for credential fallbacks.

    Returns:
        Configured ChatBedrock instance.

    Raises:
        LLMProviderConfigError: If STS AssumeRole fails or credentials are partial.
    """
    from botocore.config import Config
    from langchain_aws import ChatBedrock

    resolved_region = region or settings.aws_region
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

    # Resolve role_arn: config takes priority over settings
    resolved_role_arn = role_arn or getattr(settings, "bedrock_role_arn", None)

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
            settings.aws_access_key_id.get_secret_value() if settings.aws_access_key_id else None
        )
        aws_secret_key = (
            settings.aws_secret_access_key.get_secret_value()
            if settings.aws_secret_access_key
            else None
        )
        aws_session_token = (
            settings.aws_session_token.get_secret_value() if settings.aws_session_token else None
        )

        if aws_access_key and aws_secret_key:
            kwargs["aws_access_key_id"] = aws_access_key
            kwargs["aws_secret_access_key"] = aws_secret_key
            if aws_session_token:
                kwargs["aws_session_token"] = aws_session_token
        elif aws_access_key or aws_secret_key:
            raise LLMProviderConfigError(
                provider="bedrock",
                reason=(
                    "Both AWS_ACCESS_KEY_ID and AWS_SECRET_ACCESS_KEY must be set together, "
                    "or both must be absent to use IAM role / ambient credentials. "
                    "Only one key was provided."
                ),
            )
        # Neither set → fall through to boto3 ambient credential chain

    return ChatBedrock(**kwargs)


class DeepAgentStreamingService:
    """Streaming service using DeepAgents for multi-step completion.

    This service uses deepagents' create_deep_agent which provides:
    - Automatic multi-turn ReAct loop
    - Built-in planning via Deep Agents' TodoListMiddleware
    - State checkpointing via thread_id
    - Context window management
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the deep agent streaming service.

        Args:
            settings: Application settings for infrastructure configuration.
        """
        self.settings = settings
        self.storage_backend = create_storage_backend(settings)

    async def stream_response(
        self,
        session_id: str,
        thread_id: str,
        project_path: str,
        content: str,
        system_prompt: str | None = None,
        manager: SessionAgentManager | None = None,
        scope: dict[str, str] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream LLM response using DeepAgents with multi-step support."""
        runtime: DeepAgentRuntime | None = None
        try:
            # Get session for config / agent_name resolution
            storage = get_storage_backend()
            session = await storage.get_session(session_id)

            # Get tools from AgentRegistry if available (needed before agent_def resolution
            # so agent_def tools can be merged in below)
            custom_tools: list[Any] = []
            registry = None
            try:
                from server.app.agent_registry import get_agent_registry

                registry = get_agent_registry()
                custom_tools = registry.create_tools()
            except RuntimeError:
                pass  # Registry not initialized (e.g., in test contexts)
            except Exception:
                logger.warning(
                    "Failed to load custom tools from agent registry — agent will run without them",
                    exc_info=True,
                )

            # Resolve agent definition — all fields consumed before model resolution
            # so agent_def.config can feed into _resolve_model as an override tier.
            subagents: list[Any] = []
            agent_skills: list[str] = []
            agent_memory: list[str] | None = None
            agent_interrupt_on: dict[str, Any] | None = None
            agent_middleware: list[Any] | None = None
            agent_response_format: str | None = None
            agent_tool_token_limit_before_evict: int | None = None
            agent_def = None

            if registry and session:
                from server.app.agent.agent_definition_registry import (
                    get_agent_definition_registry,
                )

                def_registry = get_agent_definition_registry()
                if def_registry:
                    agent_def = def_registry.get(session.agent_name)
                    if not agent_def:
                        logger.warning(
                            "Agent definition not found, falling back to 'default'",
                            requested=session.agent_name,
                        )
                        agent_def = def_registry.get("default")

                    if agent_def:
                        # system_prompt
                        if system_prompt is None:
                            system_prompt = agent_def.system_prompt

                        # skills — always append the DB-backed skills API path
                        if agent_def.skills:
                            agent_skills = list(agent_def.skills)
                        if "/skills/api/" not in agent_skills:
                            agent_skills.append("/skills/api/")

                        # subagents — all registry subagents except this agent itself
                        all_subagents = def_registry.subagents()
                        subagents = [
                            s.to_subagent() for s in all_subagents if s.name != agent_def.name
                        ]

                        # memory — per-agent memory files (e.g. AGENTS.md paths)
                        if agent_def.memory:
                            agent_memory = list(agent_def.memory)

                        # interrupt_on — per-agent HITL tool approval config
                        if agent_def.interrupt_on:
                            agent_interrupt_on = dict(agent_def.interrupt_on)

                        if agent_def.response_format:
                            agent_response_format = agent_def.response_format

                        if agent_def.config.tool_token_limit_before_evict is not None:
                            agent_tool_token_limit_before_evict = (
                                agent_def.config.tool_token_limit_before_evict
                            )

                        # middleware — resolve declarative names to middleware instances
                        if agent_def.middleware:
                            agent_middleware = _resolve_middleware(agent_def.middleware)

                        # tools — resolve agent-def tool paths and merge with registry tools
                        if agent_def.tools:
                            agent_def_tools = agent_def._resolve_tools(base_path=project_path)
                            if agent_def_tools:
                                custom_tools = list(custom_tools) + agent_def_tools

            # Resolve provider config and build a BaseChatModel.
            # agent_def is passed so its config tier slots between
            # GlobalProviderDefaults and SessionConfig in the resolution hierarchy.
            model, provider, model_id, recursion_limit = await self._resolve_model(
                session=session, scope=scope, agent_def=agent_def
            )

            # Get checkpointer from storage backend
            checkpointer = await self.storage_backend.get_checkpointer()

            # Load tools registered via POST /tools from ConfigRegistry.
            # These are merged on top of AgentRegistry (file-discovered) and
            # agent_def tools. Errors are logged and skipped.
            config_registry_tools = await _load_config_registry_tools(scope=scope)
            if config_registry_tools:
                custom_tools = list(custom_tools) + config_registry_tools

            # Get LangGraph Store for cross-thread agent memory.
            # Store namespaces are scoped per-user via CognitionContext so
            # different users cannot read each other's memories.
            store = await self.storage_backend.get_store()

            # Build invocation context from session scope.
            # This is forwarded to astream() so that runtime.context is
            # available inside nodes and middleware for Store scoping.
            from server.app.agent.cognition_agent import CognitionContext

            invocation_context = CognitionContext.from_scope(
                session.scopes if session and hasattr(session, "scopes") else scope
            )

            # Resolve MCP servers from ConfigRegistry
            mcp_configs = await self._resolve_mcp_configs(scope=scope)

            # Pass the pre-built BaseChatModel directly to create_cognition_agent.
            # This bypasses deepagents' internal init_chat_model call and uses
            # our already-configured model with correct credentials / base_url.
            agent = await create_cognition_agent(
                project_path=project_path,
                model=model,
                store=store,
                checkpointer=checkpointer,
                settings=self.settings,
                tools=custom_tools if custom_tools else None,
                system_prompt=system_prompt,
                skills=agent_skills if agent_skills else None,
                subagents=subagents,
                memory=agent_memory,
                interrupt_on=agent_interrupt_on,
                response_format=(
                    session.config.response_format if session and session.config else None
                )
                or agent_response_format,
                tool_token_limit_before_evict=agent_tool_token_limit_before_evict,
                middleware=agent_middleware,
                mcp_configs=mcp_configs or None,
                scope=scope,
            )

            if manager and agent.sandbox_backend is not None:
                manager.register_sandbox_backend(session_id, agent.sandbox_backend)

            runtime = DeepAgentRuntime(
                agent=agent.agent,
                checkpointer=checkpointer,
                thread_id=thread_id,
                recursion_limit=recursion_limit,
                context=invocation_context,
            )
            if manager:
                manager.register_runtime(session_id, runtime)

            # Build message input (system prompt already embedded in agent graph)
            messages = self._build_messages(content, None)

            # Track token counts for UsageEvent
            input_tokens = len(content.split())
            output_tokens = 0
            accumulated_content = ""
            _current_tool_call = None

            try:
                async for event in runtime.astream_events(
                    {"messages": messages},
                    thread_id=thread_id,
                ):
                    if isinstance(event, TokenEvent):
                        accumulated_content += event.content
                        output_tokens += len(event.content.split())
                        yield event

                    elif isinstance(event, ToolCallEvent):
                        _current_tool_call = event.tool_call_id
                        yield event

                    elif isinstance(event, ToolResultEvent):
                        _current_tool_call = None
                        yield event

                    elif isinstance(event, PlanningEvent) or isinstance(
                        event, (DelegationEvent, StatusEvent, StepCompleteEvent, InterruptEvent)
                    ):
                        yield event
                        if isinstance(event, InterruptEvent):
                            return

                    elif isinstance(event, ErrorEvent):
                        yield event
                        if event.code == "ABORTED":
                            return

                    # DoneEvent from the runtime is absorbed here; we emit our own below.

            except Exception as exc:
                logger.error(
                    "LangGraph execution failed during stream_response",
                    error=str(exc),
                    session_id=session_id,
                    exc_info=True,
                )
                yield ErrorEvent(message=f"Agent execution failed: {exc}", code="STREAMING_ERROR")

            finally:
                if manager:
                    manager.unregister_runtime(session_id)

            yield UsageEvent(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=self._estimate_cost(input_tokens, output_tokens, provider),
                provider=provider,
                model=model_id,
            )
            yield DoneEvent()

        except LLMProviderConfigError as e:
            logger.error(
                "Provider configuration error",
                error=str(e),
                session_id=session_id,
            )
            yield ErrorEvent(message=str(e), code="PROVIDER_CONFIG_ERROR")
        except Exception as e:
            logger.error("DeepAgents streaming error", error=str(e), session_id=session_id)
            yield ErrorEvent(message=str(e), code="STREAMING_ERROR")

    async def resume_response(
        self,
        session_id: str,
        thread_id: str,
        project_path: str,
        decision: str,
        tool_name: str,
        args: dict[str, Any] | None = None,
        scope: dict[str, str] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Resume an interrupted Deep Agents run from persisted checkpoint state."""
        try:
            storage = get_storage_backend()
            session = await storage.get_session(session_id)
            if session is None:
                yield ErrorEvent(message=f"Session not found: {session_id}", code="NOT_FOUND")
                return

            custom_tools: list[Any] = []
            registry = None
            try:
                from server.app.agent_registry import get_agent_registry

                registry = get_agent_registry()
                custom_tools = registry.create_tools()
            except RuntimeError:
                pass
            except Exception:
                logger.warning(
                    "Failed to load custom tools from agent registry for resume",
                    exc_info=True,
                )

            subagents: list[Any] = []
            agent_skills: list[str] = []
            agent_memory: list[str] | None = None
            agent_interrupt_on: dict[str, Any] | None = None
            agent_middleware: list[Any] | None = None
            agent_response_format: str | None = None
            agent_tool_token_limit_before_evict: int | None = None
            agent_def = None
            system_prompt: str | None = None

            if registry:
                from server.app.agent.agent_definition_registry import get_agent_definition_registry

                def_registry = get_agent_definition_registry()
                if def_registry:
                    agent_def = def_registry.get(session.agent_name) or def_registry.get("default")
                    if agent_def:
                        system_prompt = agent_def.system_prompt
                        if agent_def.skills:
                            agent_skills = list(agent_def.skills)
                        if "/skills/api/" not in agent_skills:
                            agent_skills.append("/skills/api/")
                        all_subagents = def_registry.subagents()
                        subagents = [
                            s.to_subagent() for s in all_subagents if s.name != agent_def.name
                        ]
                        if agent_def.memory:
                            agent_memory = list(agent_def.memory)
                        if agent_def.interrupt_on:
                            agent_interrupt_on = dict(agent_def.interrupt_on)
                        if agent_def.response_format:
                            agent_response_format = agent_def.response_format
                        if agent_def.config.tool_token_limit_before_evict is not None:
                            agent_tool_token_limit_before_evict = (
                                agent_def.config.tool_token_limit_before_evict
                            )
                        if agent_def.middleware:
                            agent_middleware = _resolve_middleware(agent_def.middleware)
                        if agent_def.tools:
                            agent_def_tools = agent_def._resolve_tools(base_path=project_path)
                            if agent_def_tools:
                                custom_tools = list(custom_tools) + agent_def_tools

            model, provider, model_id, recursion_limit = await self._resolve_model(
                session=session, scope=scope, agent_def=agent_def
            )
            checkpointer = await self.storage_backend.get_checkpointer()
            config_registry_tools = await _load_config_registry_tools(scope=scope)
            if config_registry_tools:
                custom_tools = list(custom_tools) + config_registry_tools
            store = await self.storage_backend.get_store()

            from server.app.agent.cognition_agent import CognitionContext

            invocation_context = CognitionContext.from_scope(
                session.scopes if hasattr(session, "scopes") else scope
            )
            mcp_configs = await self._resolve_mcp_configs(scope=scope)

            agent = await create_cognition_agent(
                project_path=project_path,
                model=model,
                store=store,
                checkpointer=checkpointer,
                settings=self.settings,
                tools=custom_tools if custom_tools else None,
                system_prompt=system_prompt,
                skills=agent_skills if agent_skills else None,
                subagents=subagents,
                memory=agent_memory,
                interrupt_on=agent_interrupt_on,
                response_format=(session.config.response_format if session.config else None)
                or agent_response_format,
                tool_token_limit_before_evict=agent_tool_token_limit_before_evict,
                middleware=agent_middleware,
                mcp_configs=mcp_configs or None,
                scope=scope,
            )

            resume_decision: dict[str, Any] = {"type": decision}
            if decision == "edit":
                resume_decision["edited_action"] = {
                    "name": tool_name,
                    "args": args or {},
                }

            runtime = DeepAgentRuntime(
                agent=agent.agent,
                checkpointer=checkpointer,
                thread_id=thread_id,
                recursion_limit=recursion_limit,
                context=invocation_context,
            )

            output_tokens = 0
            async for event in runtime.astream_resume_events(
                decision=decision,
                tool_name=tool_name,
                args=args,
                thread_id=thread_id,
            ):
                if isinstance(event, TokenEvent):
                    output_tokens += len(event.content.split())
                if isinstance(event, InterruptEvent):
                    continue
                if isinstance(event, DoneEvent):
                    continue
                if isinstance(
                    event,
                    (
                        TokenEvent,
                        ToolCallEvent,
                        ToolResultEvent,
                        StatusEvent,
                        ErrorEvent,
                        UsageEvent,
                        PlanningEvent,
                        StepCompleteEvent,
                        DelegationEvent,
                    ),
                ):
                    yield cast(StreamEvent, event)

            yield UsageEvent(
                input_tokens=0,
                output_tokens=output_tokens,
                estimated_cost=self._estimate_cost(0, output_tokens, provider),
                provider=provider,
                model=model_id,
            )
            yield DoneEvent()

        except LLMProviderConfigError as e:
            logger.error(
                "Provider configuration error on resume", error=str(e), session_id=session_id
            )
            yield ErrorEvent(message=str(e), code="PROVIDER_CONFIG_ERROR")
        except Exception as e:
            logger.error("DeepAgents resume error", error=str(e), session_id=session_id)
            yield ErrorEvent(message=str(e), code="RESUME_ERROR")

    async def rebuild_message_projection(
        self,
        session_id: str,
        thread_id: str,
    ) -> int:
        """Rebuild the API message projection from authoritative checkpoint state."""
        checkpointer = await self.storage_backend.get_checkpointer()
        checkpoint = await checkpointer.aget({"configurable": {"thread_id": thread_id}})
        if checkpoint is None:
            return 0

        checkpoint_messages = checkpoint.get("channel_values", {}).get("messages", [])
        if not isinstance(checkpoint_messages, list):
            return 0

        rebuilt_count = await self.storage_backend.rebuild_message_projection(
            session_id=session_id,
            thread_id=thread_id,
            checkpoint_messages=checkpoint_messages,
        )
        return int(rebuilt_count)

    async def _resolve_provider_config(
        self,
        session: Any,
        scope: dict[str, str] | None,
        agent_def: Any | None = None,
    ) -> tuple[
        str, str, str | None, str | None, str | None, str | None, int, int | None, int | None
    ]:
        """Resolve provider configuration from ConfigRegistry.

        Priority (highest to lowest):
        1. session.config.provider_id — looks up exact ProviderConfig by ID
        2. session.config.provider + session.config.model — direct override
        3. agent_def.config.provider + agent_def.config.model — per-agent override
        4. First enabled ProviderConfig from ConfigRegistry (global default)

        recursion_limit follows the same hierarchy:
        session.config > agent_def.config > hardcoded default (1000)

        Returns:
            (provider, model_id, api_key, base_url, region, role_arn, recursion_limit, max_retries, timeout)

        Raises:
            LLMProviderConfigError: If no provider can be resolved or the
                resolved provider is test-only in a production context.
        """
        # Resolve recursion_limit: session overrides agent, agent overrides default
        recursion_limit = 1000
        if agent_def and agent_def.config and agent_def.config.recursion_limit is not None:
            recursion_limit = agent_def.config.recursion_limit
        if session and session.config and session.config.recursion_limit is not None:
            recursion_limit = session.config.recursion_limit

        # 1. provider_id override: look up a specific ProviderConfig by ID
        if session and session.config and session.config.provider_id:
            provider_id = session.config.provider_id
            try:
                from server.app.storage.config_registry import get_config_registry

                reg = get_config_registry()
                provider_config = await reg.get_provider(provider_id, scope=scope)
                if provider_config is None:
                    raise LLMProviderConfigError(
                        provider=provider_id,
                        reason=(
                            f"Provider config '{provider_id}' not found in ConfigRegistry. "
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
                api_key = (
                    os.environ.get(provider_config.api_key_env)
                    if provider_config.api_key_env
                    else None
                )
                logger.debug(
                    "Provider resolved from session provider_id",
                    provider_id=provider_id,
                    provider=provider_config.provider,
                    model=provider_config.model,
                )
                return (
                    provider_config.provider,
                    provider_config.model,
                    api_key,
                    provider_config.base_url,
                    provider_config.region,
                    provider_config.role_arn,
                    recursion_limit,
                    provider_config.max_retries,
                    provider_config.timeout,
                )
            except RuntimeError as exc:
                raise LLMProviderConfigError(
                    provider=provider_id,
                    reason="ConfigRegistry not initialized — cannot resolve provider_id.",
                ) from exc

        # 2. Session-level provider/model override: bypass ConfigRegistry entirely
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
            logger.debug(
                "Using session-level provider override",
                provider=prov,
                model=mod,
            )
            return prov, mod, None, None, None, None, recursion_limit, None, None

        # 3. Individual ProviderConfig entries from ConfigRegistry.
        #    Unscoped providers (scope={}) are visible to all scopes and act as
        #    global defaults. No separate GlobalProviderDefaults fallback.
        try:
            from server.app.storage.config_registry import get_config_registry

            reg = get_config_registry()
            provider_configs = await reg.list_providers(scope=scope)

            enabled = [pc for pc in provider_configs if pc.enabled]
            if enabled:
                # Sort ascending by priority (lower number = higher priority)
                enabled.sort(key=lambda pc: pc.priority)
                chosen = enabled[0]
                api_key = os.environ.get(chosen.api_key_env) if chosen.api_key_env else None

                # Apply agent_def.config as an override tier above the global default.
                # Only model/provider are overridable at the agent level — credentials
                # and infra config (base_url, region, role_arn) stay from the registry.
                resolved_provider = chosen.provider
                resolved_model = chosen.model
                if agent_def and agent_def.config:
                    if agent_def.config.provider:
                        resolved_provider = agent_def.config.provider
                    if agent_def.config.model:
                        resolved_model = agent_def.config.model
                    if resolved_provider != chosen.provider or resolved_model != chosen.model:
                        logger.debug(
                            "Provider/model overridden by agent_def.config",
                            agent=getattr(agent_def, "name", "unknown"),
                            base_provider=chosen.provider,
                            base_model=chosen.model,
                            override_provider=resolved_provider,
                            override_model=resolved_model,
                        )

                logger.debug(
                    "Provider resolved from ConfigRegistry",
                    provider=resolved_provider,
                    model=resolved_model,
                    id=chosen.id,
                )
                return (
                    resolved_provider,
                    resolved_model,
                    api_key,
                    chosen.base_url,
                    chosen.region,
                    chosen.role_arn,
                    recursion_limit,
                    chosen.max_retries,
                    chosen.timeout,
                )
        except RuntimeError:
            logger.warning("ConfigRegistry not initialized — cannot resolve provider configuration")

        raise LLMProviderConfigError(
            provider="none",
            reason=(
                "No provider configuration found. "
                "Create a provider via POST /models/providers. "
                "Providers with an empty scope are visible to all users."
            ),
        )

    async def _resolve_model(
        self,
        session: Any,
        scope: dict[str, str] | None,
        agent_def: Any | None = None,
    ) -> tuple[BaseChatModel, str, str, int]:
        """Resolve provider config and build a LangChain BaseChatModel.

        Args:
            session: Session object (may be None in tests).
            scope: Scope dict for ConfigRegistry lookup.
            agent_def: Optional AgentDefinition whose config acts as an override
                tier between GlobalProviderDefaults and SessionConfig.

        Returns:
            (model, provider_name, model_id, recursion_limit)

        Raises:
            LLMProviderConfigError: If the provider is misconfigured, unknown,
                or is test-only in a production context.
        """
        (
            provider,
            model_id,
            api_key,
            base_url,
            region,
            role_arn,
            recursion_limit,
            max_retries,
            timeout,
        ) = await self._resolve_provider_config(session=session, scope=scope, agent_def=agent_def)

        # Guard: refuse to silently use mock in production
        if provider in _TEST_ONLY_PROVIDERS:
            raise LLMProviderConfigError(
                provider=provider,
                reason=(
                    f"Provider '{provider}' is reserved for testing and cannot be used in "
                    "production. Configure a real provider via POST /models/providers."
                ),
            )

        # Extract temperature from agent_def.config if present
        # (session.config doesn't have a temperature field — that's agent-level config)
        temperature: float | None = None
        if agent_def and agent_def.config and agent_def.config.temperature is not None:
            temperature = agent_def.config.temperature

        max_tokens: int | None = None
        if agent_def and agent_def.config and agent_def.config.max_tokens is not None:
            max_tokens = agent_def.config.max_tokens

        model = _build_model(
            provider=provider,
            model_id=model_id,
            api_key=api_key,
            base_url=base_url,
            region=region,
            role_arn=role_arn,
            settings=self.settings,
            temperature=temperature,
            max_tokens=max_tokens,
            max_retries=max_retries,
            timeout=timeout,
        )

        # Tool call validation warning — enrichment only, never blocks execution
        await self._warn_if_no_tool_call_support(provider, model_id)

        return model, provider, model_id, recursion_limit

    async def _warn_if_no_tool_call_support(self, provider: str, model_id: str) -> None:
        """Log a warning if the model catalog says this model lacks tool call support.

        This is best-effort: if the catalog is unavailable or the model is not
        found, no warning is emitted.
        """
        try:
            from server.app.llm.model_catalog import get_model_catalog

            catalog = get_model_catalog()
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
            # Catalog errors must never block model resolution
            pass

    async def _resolve_mcp_configs(self, scope: dict[str, str] | None) -> list[Any]:
        """Load MCP server registrations from ConfigRegistry."""
        try:
            from server.app.agent.mcp_client import McpServerConfig
            from server.app.storage.config_registry import get_config_registry

            reg = get_config_registry()
            servers = await reg.list_mcp_servers(scope)
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
            logger.warning("ConfigRegistry not initialized — MCP servers will not be available")
            return []

    def _build_messages(self, user_content: str, custom_system_prompt: str | None = None) -> list:
        """Build message list with optional system prompt.

        Args:
            user_content: User message content.
            custom_system_prompt: If provided, prepended as a SystemMessage.
                Pass None when the system prompt is already embedded in the
                agent graph via create_cognition_agent(system_prompt=...).

        Returns:
            List of LangChain messages.
        """
        messages: list = []

        if custom_system_prompt is not None:
            messages.append(SystemMessage(content=custom_system_prompt))

        messages.append(HumanMessage(content=user_content))
        return messages

    def _estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        provider: str,
    ) -> float:
        """Estimate cost based on provider pricing (rough approximation)."""
        pricing: dict[str, dict[str, float]] = {
            "openai": {"input": 0.0025, "output": 0.01},
            "anthropic": {"input": 0.003, "output": 0.015},
            "bedrock": {"input": 0.003, "output": 0.015},
            "openai_compatible": {"input": 0.001, "output": 0.002},
            "google_genai": {"input": 0.0005, "output": 0.0015},
            "google_vertexai": {"input": 0.0005, "output": 0.0015},
        }
        rates = pricing.get(provider, pricing["openai"])
        input_cost = (input_tokens / 1000) * rates["input"]
        output_cost = (output_tokens / 1000) * rates["output"]
        return round(input_cost + output_cost, 6)


class SessionAgentManager:
    """Manages DeepAgent services per session.

    Creates and caches agent services for each session.
    Tracks active streaming operations for abort functionality.
    """

    def __init__(self, settings: Settings) -> None:
        """Initialize the session manager.

        Args:
            settings: Application settings.
        """
        self.settings = settings
        self._services: dict[str, DeepAgentStreamingService] = {}
        self._project_paths: dict[str, str] = {}
        self._active_runtimes: dict[str, Any] = {}
        self._sandbox_backends: dict[str, Any] = {}

    def register_session(
        self,
        session_id: str,
        project_path: str,
    ) -> DeepAgentStreamingService:
        """Register a new session and return its streaming service.

        Args:
            session_id: Unique session identifier.
            project_path: Path to the project workspace.

        Returns:
            Configured DeepAgentStreamingService for the session.
        """
        service = DeepAgentStreamingService(settings=self.settings)
        self._services[session_id] = service
        self._project_paths[session_id] = project_path
        logger.info(
            "Session registered with DeepAgents",
            session_id=session_id,
            project_path=project_path,
        )
        return service

    def get_service(self, session_id: str) -> DeepAgentStreamingService | None:
        """Get the agent service for a session."""
        return self._services.get(session_id)

    def get_project_path(self, session_id: str) -> str | None:
        """Get the project path for a session."""
        return self._project_paths.get(session_id)

    def get_runtime(self, session_id: str) -> Any | None:
        """Get the active runtime for a session, if any."""
        return self._active_runtimes.get(session_id)

    def register_runtime(self, session_id: str, runtime: Any) -> None:
        """Register an active runtime for abort tracking."""
        self._active_runtimes[session_id] = runtime
        logger.debug("Runtime registered for abort tracking", session_id=session_id)

    def unregister_runtime(self, session_id: str) -> None:
        """Unregister a runtime when streaming completes."""
        self._active_runtimes.pop(session_id, None)
        logger.debug("Runtime unregistered", session_id=session_id)

    async def abort_session(self, session_id: str, thread_id: str | None = None) -> bool:
        """Abort the current operation for a session.

        Returns:
            True if abort was signaled, False if no active runtime.
        """
        runtime = self._active_runtimes.get(session_id)
        if runtime:
            success = bool(await runtime.abort(thread_id))
            logger.info("Session abort signaled", session_id=session_id, success=success)
            return success
        logger.warning("No active runtime to abort", session_id=session_id)
        return False

    def register_sandbox_backend(self, session_id: str, backend: Any) -> None:
        """Register a sandbox backend for lifecycle tracking.

        The backend's ``terminate()`` method will be called when the session
        is unregistered, cleaning up any K8s Sandbox CRs or Docker containers.

        Args:
            session_id: Unique session identifier.
            backend: The sandbox backend instance (must have a ``terminate()`` method).
        """
        self._sandbox_backends[session_id] = backend
        logger.debug("Sandbox backend registered", session_id=session_id)

    def unregister_session(self, session_id: str) -> None:
        """Unregister a session and clean up resources."""
        self._services.pop(session_id, None)
        self._project_paths.pop(session_id, None)
        self._active_runtimes.pop(session_id, None)

        backend = self._sandbox_backends.pop(session_id, None)
        if backend is not None and hasattr(backend, "terminate"):
            try:
                backend.terminate()
                logger.info("Sandbox backend terminated", session_id=session_id)
            except Exception as e:
                logger.warning(
                    "Sandbox backend terminate failed", session_id=session_id, error=str(e)
                )

        logger.info("Session unregistered", session_id=session_id)


# Global manager instance
_agent_manager: SessionAgentManager | None = None


def get_session_agent_manager(settings: Settings) -> SessionAgentManager:
    """Get or create the global session agent manager."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = SessionAgentManager(settings)
    return _agent_manager
