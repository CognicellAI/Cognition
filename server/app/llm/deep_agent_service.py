"""Streaming LLM service using DeepAgents for multi-step task completion.

This service leverages deepagents' built-in capabilities:
- Automatic ReAct loop (LLM → tool → LLM until completion)
- State persistence via thread_id checkpointing
- Built-in planning via Deep Agents' TodoListMiddleware
- Context management and conversation summarization

Provider/model resolution reads from ConfigStore (scope-aware) via RuntimeResolver
and builds a LangChain BaseChatModel. No custom fallback chains.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any, cast

import structlog
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage

from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent
from server.app.agent.resolver import RuntimeResolver
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
from server.app.storage.config_store import ConfigStore
from server.app.storage.factory import create_storage_backend

logger = structlog.get_logger(__name__)


async def _load_config_registry_tools(scope: dict[str, str] | None) -> list[Any]:
    """Load tools registered via POST /tools from ConfigStore.

    .. deprecated:: Use RuntimeResolver.build_tools() instead.
    """
    try:
        from server.app.api.dependencies import get_config_store

        config_store = get_config_store()
        resolver = RuntimeResolver(config_store=config_store, settings=Settings())
        return await resolver.build_tools(scope=scope)
    except RuntimeError:
        logger.debug("ConfigStore not initialized — skipping API-registered tools")
        return []


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


@dataclass
class ResolvedAgentConfig:
    """Fields resolved from an AgentDefinition, ready for CognitionAgentParams."""

    system_prompt: str | None = None
    skills: list[str] = field(default_factory=list)
    memory: list[str] | None = None
    interrupt_on: dict[str, Any] | None = None
    middleware: list[Any] | None = None
    response_format: str | None = None
    tool_token_limit_before_evict: int | None = None
    subagents: list[Any] = field(default_factory=list)
    agent_def: Any = None


@dataclass
class StreamAccumulator:
    """Tracks token counts and accumulated content during streaming."""

    input_tokens: int = 0
    output_tokens: int = 0
    accumulated_content: str = ""
    _current_tool_call: str | None = field(default=None, repr=False)

    def record_token(self, content: str) -> None:
        self.output_tokens += len(content.split())
        self.accumulated_content += content

    def set_tool_call(self, tool_call_id: str | None) -> None:
        self._current_tool_call = tool_call_id

    @property
    def in_tool_call(self) -> bool:
        return self._current_tool_call is not None


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
    """Build a LangChain BaseChatModel. Delegates to RuntimeResolver.build_model().

    .. deprecated:: Use RuntimeResolver.build_model() directly.
    """
    try:
        from server.app.api.dependencies import get_runtime_resolver

        resolver = get_runtime_resolver()
    except RuntimeError:
        resolver = RuntimeResolver(config_store=None, settings=settings)

    return resolver.build_model(
        provider=provider,
        model_id=model_id,
        api_key=api_key,
        base_url=base_url,
        region=region,
        role_arn=role_arn,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
        timeout=timeout,
    )


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
    """Build a ChatBedrock model. Delegates to RuntimeResolver.

    .. deprecated:: Use RuntimeResolver.build_model(provider="bedrock", ...) instead.
    """
    return _build_model(
        provider="bedrock",
        model_id=model_id,
        api_key=None,
        base_url=None,
        region=region,
        role_arn=role_arn,
        settings=settings,
        temperature=temperature,
        max_tokens=max_tokens,
        max_retries=max_retries,
        timeout=timeout,
    )


class DeepAgentStreamingService:
    """Streaming service using DeepAgents for multi-step completion.

    This service uses deepagents' create_deep_agent which provides:
    - Automatic multi-turn ReAct loop
    - Built-in planning via Deep Agents' TodoListMiddleware
    - State checkpointing via thread_id
    - Context window management
    """

    def __init__(
        self,
        settings: Settings,
        runtime_resolver: RuntimeResolver | None = None,
        config_store: ConfigStore | None = None,
    ) -> None:
        self.settings = settings
        self.storage_backend = create_storage_backend(settings)
        self._runtime_resolver = runtime_resolver
        self._config_store = config_store

    def _get_runtime_resolver(self) -> RuntimeResolver:
        if self._runtime_resolver is None:
            try:
                from server.app.api.dependencies import get_runtime_resolver

                self._runtime_resolver = get_runtime_resolver()
            except RuntimeError:
                self._runtime_resolver = RuntimeResolver(
                    config_store=self._get_config_store(), settings=self.settings
                )
        return self._runtime_resolver

    def _get_config_store(self) -> ConfigStore | None:
        if self._config_store is None:
            try:
                from server.app.api.dependencies import get_config_store

                self._config_store = get_config_store()
            except RuntimeError:
                pass
        return self._config_store

    async def _resolve_agent_config(
        self,
        session: Any,
        project_path: str,
        system_prompt: str | None = None,
    ) -> tuple[ResolvedAgentConfig, list[Any]]:
        """Resolve agent definition fields and custom tools from ConfigStore.

        Returns:
            (ResolvedAgentConfig, custom_tools) tuple. The config holds all
            agent_def-derived overrides; custom_tools includes both
            AgentRegistry tools and agent_def-resolved tools.
        """
        custom_tools: list[Any] = []
        try:
            from server.app.agent_registry import get_agent_registry

            registry = get_agent_registry()
            custom_tools = registry.create_tools()
        except RuntimeError:
            pass
        except Exception:
            logger.warning(
                "Failed to load custom tools from agent registry — agent will run without them",
                exc_info=True,
            )

        resolved = ResolvedAgentConfig(system_prompt=system_prompt)

        cs = self._get_config_store()
        if cs is None or session is None:
            return resolved, custom_tools

        agent_def = await cs.get_agent_definition(session.agent_name)
        if not agent_def:
            logger.warning(
                "Agent definition not found, falling back to 'default'",
                requested=session.agent_name,
            )
            agent_def = await cs.get_agent_definition("default")

        if agent_def is None:
            return resolved, custom_tools

        resolved.agent_def = agent_def
        if resolved.system_prompt is None:
            resolved.system_prompt = agent_def.system_prompt

        if agent_def.skills:
            resolved.skills = list(agent_def.skills)
        if "/skills/api/" not in resolved.skills:
            resolved.skills.append("/skills/api/")

        all_defs = await cs.list_agent_definitions(include_hidden=True)
        resolved.subagents = [s.to_subagent() for s in all_defs if s.name != agent_def.name]

        if agent_def.memory:
            resolved.memory = list(agent_def.memory)

        if agent_def.interrupt_on:
            resolved.interrupt_on = dict(agent_def.interrupt_on)

        if agent_def.response_format:
            resolved.response_format = agent_def.response_format

        if agent_def.config.tool_token_limit_before_evict is not None:
            resolved.tool_token_limit_before_evict = agent_def.config.tool_token_limit_before_evict

        if agent_def.middleware:
            resolved.middleware = _resolve_middleware(agent_def.middleware)

        if agent_def.tools:
            agent_def_tools = agent_def._resolve_tools(base_path=project_path)
            if agent_def_tools:
                custom_tools = list(custom_tools) + agent_def_tools

        return resolved, custom_tools

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

            agent_cfg, custom_tools = await self._resolve_agent_config(
                session=session,
                project_path=project_path,
                system_prompt=system_prompt,
            )

            model, provider, model_id, recursion_limit = await self._resolve_model(
                session=session, scope=scope, agent_def=agent_cfg.agent_def
            )

            # Get checkpointer from storage backend
            checkpointer = await self.storage_backend.get_checkpointer()

            # Load tools registered via POST /tools from ConfigStore.
            config_store_tools = await self._get_runtime_resolver().build_tools(
                scope=scope, extra_tools=custom_tools if custom_tools else None
            )
            if config_store_tools:
                custom_tools = config_store_tools

            store = await self.storage_backend.get_store()

            from server.app.agent.cognition_agent import CognitionContext

            invocation_context = CognitionContext.from_scope(
                session.scopes if session and hasattr(session, "scopes") else scope
            )

            mcp_configs = await self._resolve_mcp_configs(scope=scope)

            agent_params = CognitionAgentParams(
                project_path=project_path,
                model=model,
                store=store,
                checkpointer=checkpointer,
                settings=self.settings,
                tools=custom_tools if custom_tools else None,
                system_prompt=agent_cfg.system_prompt,
                skills=agent_cfg.skills if agent_cfg.skills else None,
                subagents=agent_cfg.subagents,
                memory=agent_cfg.memory,
                interrupt_on=agent_cfg.interrupt_on,
                response_format=(
                    session.config.response_format if session and session.config else None
                )
                or agent_cfg.response_format,
                tool_token_limit_before_evict=agent_cfg.tool_token_limit_before_evict,
                middleware=agent_cfg.middleware,
                mcp_configs=mcp_configs or None,
                scope=scope,
                config_store=self._get_config_store(),
            )
            agent = await create_cognition_agent(agent_params)

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

            acc = StreamAccumulator(input_tokens=len(content.split()))

            try:
                async for event in runtime.astream_events(
                    {"messages": messages},
                    thread_id=thread_id,
                ):
                    if isinstance(event, TokenEvent):
                        acc.record_token(event.content)
                        yield event

                    elif isinstance(event, ToolCallEvent):
                        acc.set_tool_call(event.tool_call_id)
                        yield event

                    elif isinstance(event, ToolResultEvent):
                        acc.set_tool_call(None)
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
                input_tokens=acc.input_tokens,
                output_tokens=acc.output_tokens,
                estimated_cost=self._estimate_cost(acc.input_tokens, acc.output_tokens, provider),
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

            agent_cfg, custom_tools = await self._resolve_agent_config(
                session=session,
                project_path=project_path,
            )

            model, provider, model_id, recursion_limit = await self._resolve_model(
                session=session, scope=scope, agent_def=agent_cfg.agent_def
            )
            checkpointer = await self.storage_backend.get_checkpointer()
            config_store_tools = await self._get_runtime_resolver().build_tools(
                scope=scope, extra_tools=custom_tools if custom_tools else None
            )
            if config_store_tools:
                custom_tools = config_store_tools
            store = await self.storage_backend.get_store()

            from server.app.agent.cognition_agent import CognitionContext

            invocation_context = CognitionContext.from_scope(
                session.scopes if hasattr(session, "scopes") else scope
            )
            mcp_configs = await self._resolve_mcp_configs(scope=scope)

            agent_params = CognitionAgentParams(
                project_path=project_path,
                model=model,
                store=store,
                checkpointer=checkpointer,
                settings=self.settings,
                tools=custom_tools if custom_tools else None,
                system_prompt=agent_cfg.system_prompt,
                skills=agent_cfg.skills if agent_cfg.skills else None,
                subagents=agent_cfg.subagents,
                memory=agent_cfg.memory,
                interrupt_on=agent_cfg.interrupt_on,
                response_format=(session.config.response_format if session.config else None)
                or agent_cfg.response_format,
                tool_token_limit_before_evict=agent_cfg.tool_token_limit_before_evict,
                middleware=agent_cfg.middleware,
                mcp_configs=mcp_configs or None,
                scope=scope,
                config_store=self._get_config_store(),
            )
            agent = await create_cognition_agent(agent_params)

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

            acc = StreamAccumulator(input_tokens=0)
            async for event in runtime.astream_resume_events(
                decision=decision,
                tool_name=tool_name,
                args=args,
                thread_id=thread_id,
            ):
                if isinstance(event, TokenEvent):
                    acc.record_token(event.content)
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
                output_tokens=acc.output_tokens,
                estimated_cost=self._estimate_cost(0, acc.output_tokens, provider),
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

    async def _resolve_model(
        self,
        session: Any,
        scope: dict[str, str] | None,
        agent_def: Any | None = None,
    ) -> tuple[BaseChatModel, str, str, int]:
        """Resolve provider config and build a LangChain BaseChatModel.

        Delegates to RuntimeResolver.resolve_model_for_session().
        """
        return await self._get_runtime_resolver().resolve_model_for_session(
            session=session, scope=scope, agent_def=agent_def
        )

    async def _resolve_mcp_configs(self, scope: dict[str, str] | None) -> list[Any]:
        """Load MCP server registrations from ConfigStore.

        Delegates to RuntimeResolver.resolve_mcp_configs().
        """
        return await self._get_runtime_resolver().resolve_mcp_configs(scope=scope)

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
