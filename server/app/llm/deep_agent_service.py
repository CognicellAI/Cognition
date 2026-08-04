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

import asyncio
import hashlib
import json
import time
from collections import deque
from collections.abc import AsyncGenerator, AsyncIterator, Mapping
from dataclasses import dataclass, field
from typing import Any, TypeVar, cast

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent
from server.app.agent.definition import AgentDefinition, AgentSkillBundle
from server.app.agent.resolver import ResolvedRuntimeModel, RuntimeResolver
from server.app.agent.runtime import (
    ArtifactEvent,  # noqa: F401 — re-exported for custom runtimes
    CallbackEvent,  # noqa: F401 — re-exported for consumers of this module
    ContextEvent,
    DeepAgentRuntime,
    DelegationEvent,
    DirectMessageEvent,  # noqa: F401 — re-exported for custom runtimes
    DoneEvent,
    ErrorEvent,
    HeartbeatEvent,  # noqa: F401 — re-exported for consumers of this module
    HitlDecisionEvent,
    InterruptEvent,
    ModelUsageEvent,
    PlanningEvent,
    RejectedEvent,  # noqa: F401 — re-exported for custom runtimes
    RunStateEvent,  # noqa: F401 — re-exported for consumers of this module
    SandboxLifecycleEvent,
    StatusEvent,
    StepCompleteEvent,  # noqa: F401 — re-exported for consumers of this module
    StreamEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolSafetyEvent,
    UsageEvent,
)
from server.app.agent.runtime import (
    _resolve_middleware as _resolve_single_middleware,
)
from server.app.agent.usage import ProviderUsageAggregator
from server.app.exceptions import LLMProviderConfigError
from server.app.observability import (
    CONTEXT_EVENT_COUNT,
    RUNTIME_CACHE_EVICTIONS_TOTAL,
    RUNTIME_CACHE_SIZE,
    add_span_event,
)
from server.app.settings import Settings
from server.app.storage.common import canonical_json_digest
from server.app.storage.config_models import SandboxProfile
from server.app.storage.config_store import ConfigStore
from server.app.storage.factory import create_storage_backend

logger = structlog.get_logger(__name__)

_EventT = TypeVar("_EventT")


class AgentDefinitionUnavailableError(Exception):
    """Raised when a session-bound agent cannot be used for invocation."""

    def __init__(
        self,
        agent_name: str,
        reason: str,
        scope: Mapping[str, str] | None = None,
    ) -> None:
        super().__init__(f"Invalid or unavailable agent '{agent_name}': {reason}")
        self.agent_name = agent_name
        self.reason = reason
        self.scope_keys = sorted((scope or {}).keys())


def _context_policy_dict(policy: Any | None) -> dict[str, Any]:
    if policy is None:
        return {}
    if hasattr(policy, "model_dump"):
        return cast(dict[str, Any], policy.model_dump(exclude_none=True, mode="json"))
    if isinstance(policy, dict):
        return dict(policy)
    return {}


def _effective_context_policy(
    policy: Any | None,
    tool_token_limit_before_evict: int | None,
) -> dict[str, Any]:
    effective = _context_policy_dict(policy)
    if tool_token_limit_before_evict is not None:
        effective.setdefault("tool_token_limit_before_evict", tool_token_limit_before_evict)
    return effective


def _pinned_sandbox_profile(
    runtime_manifest: Mapping[str, Any] | None,
) -> SandboxProfile | None:
    """Return a validated immutable SandboxProfile snapshot from a run manifest."""
    if not isinstance(runtime_manifest, Mapping):
        return None
    dependencies = runtime_manifest.get("dependencies")
    profile = dependencies.get("sandbox_profile") if isinstance(dependencies, Mapping) else None
    definition = profile.get("definition") if isinstance(profile, Mapping) else None
    expected_digest = profile.get("digest") if isinstance(profile, Mapping) else None
    if not isinstance(definition, Mapping):
        return None
    sandbox_profile = SandboxProfile.model_validate(dict(definition))
    if canonical_json_digest(sandbox_profile.model_dump(mode="json")) != expected_digest:
        raise RuntimeError("Pinned SandboxProfile manifest is invalid")
    return sandbox_profile


def _audit_context_event(event: ContextEvent) -> None:
    """Record context signals without raw content or raw scope values."""
    logger.info(
        "context_event",
        action=event.action,
        session_id=event.session_id,
        run_id=event.run_id,
        scope_keys=event.scope_keys,
        policy_keys=sorted(event.policy),
        input_tokens=event.input_tokens,
        output_tokens=event.output_tokens,
        message_count=event.message_count,
        retained_messages=event.retained_messages,
        evicted_messages=event.evicted_messages,
        summarized_messages=event.summarized_messages,
        offloaded_messages=event.offloaded_messages,
        summary_id=event.summary_id,
        artifact_id=event.artifact_id,
    )
    CONTEXT_EVENT_COUNT.labels(action=event.action).inc()
    add_span_event(
        "cognition.context",
        {
            "cognition.context.action": event.action,
            "session.id": event.session_id or "",
            "cognition.run.id": event.run_id or "",
            "cognition.scope.keys": ",".join(event.scope_keys),
            "cognition.context.policy_keys": ",".join(sorted(event.policy)),
            "cognition.context.input_tokens": event.input_tokens or 0,
            "cognition.context.output_tokens": event.output_tokens or 0,
            "cognition.context.message_count": event.message_count or 0,
            "cognition.context.retained_messages": event.retained_messages or 0,
            "cognition.context.evicted_messages": event.evicted_messages or 0,
            "cognition.context.summarized_messages": event.summarized_messages or 0,
            "cognition.context.offloaded_messages": event.offloaded_messages or 0,
        },
    )


def _model_cache_key_from_resolved(
    resolved_model: Any,
    provider: str,
    model_id: str,
) -> str:
    cache_key = getattr(resolved_model, "cache_key", None)
    if isinstance(cache_key, str) and cache_key:
        return cache_key
    return f"resolved:{provider}:{model_id}"


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
    skills: list[AgentSkillBundle] = field(default_factory=list)
    memory: list[str] | None = None
    interrupt_on: dict[str, Any] | None = None
    permissions: list[Any] | None = None
    middleware: list[Any] | None = None
    response_format: str | None = None
    tool_token_limit_before_evict: int | None = None
    context_policy: Any | None = None
    sandbox_profile: str | None = None
    sandbox_execution_role_arn: str | None = None
    excluded_tools: list[str] = field(default_factory=list)
    blocked_tools: list[str] = field(default_factory=list)
    subagents: list[Any] = field(default_factory=list)
    async_subagents: list[Any] = field(default_factory=list)
    mcp_configs: list[Any] = field(default_factory=list)
    agent_def: Any = None


@dataclass
class StreamAccumulator:
    """Tracks accumulated content and tool-call state during streaming."""

    accumulated_content: str = ""
    _current_tool_call: str | None = field(default=None, repr=False)

    def record_token(self, content: str) -> None:
        self.accumulated_content += content

    def set_tool_call(self, tool_call_id: str | None) -> None:
        self._current_tool_call = tool_call_id

    @property
    def in_tool_call(self) -> bool:
        return self._current_tool_call is not None


async def _with_execution_timeout(
    events: AsyncIterator[_EventT],
    timeout_seconds: float | None,
) -> AsyncIterator[_EventT]:
    """Yield runtime events while enforcing the configured turn deadline."""
    if timeout_seconds is None:
        async for event in events:
            yield event
        return

    async with asyncio.timeout(timeout_seconds):
        async for event in events:
            yield event


def _has_explicit_agent_field(agent_def: Any, field_name: str) -> bool:
    fields_set = getattr(agent_def, "model_fields_set", None)
    if isinstance(fields_set, set):
        return field_name in fields_set
    return hasattr(agent_def, field_name)


def _scope_fingerprint(scope: Mapping[str, str] | None) -> str | None:
    """Return a short, stable fingerprint without exposing raw scope values."""
    if not scope:
        return None
    encoded = json.dumps(
        dict(scope),
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _effective_session_scope(
    session: Any | None,
    fallback_scope: Mapping[str, str] | None,
) -> dict[str, str] | None:
    """Return the trusted scope for runtime config resolution."""
    if session is not None and hasattr(session, "scopes"):
        session_scope = getattr(session, "scopes", None)
        return dict(session_scope) if session_scope else None
    return dict(fallback_scope) if fallback_scope else None


class SandboxQuotaExceededError(RuntimeError):
    """Raised when a Cognition-side sandbox quota blocks a session."""


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
        mcp_oauth_repository: Any | None = None,
        mcp_readiness_repository: Any | None = None,
    ) -> None:
        self.settings = settings
        self.storage_backend = create_storage_backend(settings)
        self._runtime_resolver = runtime_resolver
        self._config_store = config_store
        self._mcp_oauth_repository = mcp_oauth_repository
        self._mcp_readiness_repository = mcp_readiness_repository

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
        scope: Mapping[str, str] | None = None,
        runtime_manifest: Mapping[str, Any] | None = None,
    ) -> tuple[ResolvedAgentConfig, list[Any]]:
        """Resolve agent definition fields from ConfigStore.

        Returns:
            (ResolvedAgentConfig, custom_tools) tuple. The config holds all
            agent_def-derived overrides. ``custom_tools`` is retained for
            programmatic tools supplied by tests or callers, not ConfigStore
            Python tool loading.
        """
        custom_tools: list[Any] = []

        resolved = ResolvedAgentConfig(system_prompt=system_prompt)

        cs = self._get_config_store()
        if cs is None or session is None:
            return resolved, custom_tools

        effective_scope = _effective_session_scope(session, scope)
        pinned_agent = (
            runtime_manifest.get("agent") if isinstance(runtime_manifest, Mapping) else None
        )
        pinned_definition = (
            pinned_agent.get("definition") if isinstance(pinned_agent, Mapping) else None
        )
        agent_def: AgentDefinition | None
        if isinstance(pinned_definition, Mapping):
            agent_def = AgentDefinition.model_validate(dict(pinned_definition))
            expected_digest = (
                pinned_agent.get("definition_digest") if isinstance(pinned_agent, Mapping) else None
            )
            actual_digest = canonical_json_digest(agent_def.model_dump(mode="json"))
            if agent_def.name != session.agent_name or expected_digest != actual_digest:
                raise AgentDefinitionUnavailableError(
                    session.agent_name,
                    "pinned manifest Agent identity is invalid",
                    effective_scope,
                )
        else:
            agent_def = await cs.get_agent_definition(
                session.agent_name,
                effective_scope,
            )
        if agent_def is None:
            logger.warning(
                "Agent definition not found for session execution",
                requested=session.agent_name,
                scope_keys=sorted((effective_scope or {}).keys()),
            )
            raise AgentDefinitionUnavailableError(
                session.agent_name,
                "not found",
                effective_scope,
            )

        if agent_def.hidden:
            raise AgentDefinitionUnavailableError(
                session.agent_name,
                "agent is hidden",
                effective_scope,
            )
        if agent_def.mode not in ("primary", "all"):
            raise AgentDefinitionUnavailableError(
                session.agent_name,
                f"agent mode '{agent_def.mode}' is not invokable as a primary agent",
                effective_scope,
            )

        resolved.agent_def = agent_def
        if resolved.system_prompt is None:
            resolved.system_prompt = agent_def.system_prompt

        if agent_def.skills:
            resolved.skills = list(agent_def.skills)

        # Only explicitly declared inline subagents are attached. Enumerating
        # every Agent in a tenant scope would silently widen capabilities.
        resolved.subagents = [
            {
                "name": subagent.name,
                "description": subagent.description or "",
                "system_prompt": subagent.system_prompt,
                **(
                    {
                        "permissions": [
                            permission.model_dump() for permission in subagent.permissions
                        ]
                    }
                    if subagent.permissions
                    else {}
                ),
            }
            for subagent in agent_def.subagents
        ]

        if agent_def.async_subagents:
            resolved.async_subagents = [
                spec.model_dump(exclude_none=True) if hasattr(spec, "model_dump") else dict(spec)
                for spec in agent_def.async_subagents
            ]

        if agent_def.memory:
            resolved.memory = list(agent_def.memory)

        if _has_explicit_agent_field(agent_def, "interrupt_on"):
            resolved.interrupt_on = {
                name: config.model_dump(exclude_none=True)
                if hasattr(config, "model_dump")
                else dict(config)
                for name, config in agent_def.interrupt_on.items()
            }

        if agent_def.permissions:
            resolved.permissions = [
                p.model_dump() if hasattr(p, "model_dump") else dict(p)
                for p in agent_def.permissions
            ]

        if agent_def.response_format:
            resolved.response_format = agent_def.response_format

        if agent_def.config.tool_token_limit_before_evict is not None:
            resolved.tool_token_limit_before_evict = agent_def.config.tool_token_limit_before_evict

        if agent_def.config.context_policy is not None:
            resolved.context_policy = agent_def.config.context_policy

        if agent_def.config.sandbox_profile is not None:
            resolved.sandbox_profile = agent_def.config.sandbox_profile

        if agent_def.config.sandbox_execution_role_arn is not None:
            resolved.sandbox_execution_role_arn = agent_def.config.sandbox_execution_role_arn

        if agent_def.config.excluded_tools:
            resolved.excluded_tools = list(agent_def.config.excluded_tools)

        if agent_def.config.blocked_tools:
            resolved.blocked_tools = list(agent_def.config.blocked_tools)

        if agent_def.middleware:
            resolved.middleware = _resolve_middleware(agent_def.middleware)

        if agent_def.mcp.servers:
            from server.app.agent.mcp_client import McpServerConfig

            pinned_revision = (
                pinned_agent.get("revision") if isinstance(pinned_agent, Mapping) else None
            )
            agent_revision = pinned_revision if isinstance(pinned_revision, int) else 1
            resolved.mcp_configs = [
                McpServerConfig.from_agent_config(
                    alias,
                    config,
                    self.settings,
                    agent_name=agent_def.name,
                    agent_revision=agent_revision,
                    effective_scope=effective_scope or {},
                )
                for alias, config in agent_def.mcp.servers.items()
            ]

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
        run_id: str | None = None,
        trace_parent_span: Any | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream LLM response using DeepAgents with multi-step support."""
        runtime: DeepAgentRuntime | None = None
        try:
            # Get session for config / agent_name resolution
            session = await self.storage_backend.get_session(session_id, scope)
            effective_scope = _effective_session_scope(session, scope)
            manifest_digest = ""
            pinned_manifest: Mapping[str, Any] | None = None
            if run_id is not None:
                pinned_run = await self.storage_backend.get_run(
                    run_id,
                    effective_scope,
                )
                if pinned_run is None:
                    raise RuntimeError("Pinned run manifest was not found at exact scope")
                manifest_digest = pinned_run.manifest_digest
                pinned_manifest = pinned_run.runtime_manifest

            agent_cfg, custom_tools = await self._resolve_agent_config(
                session=session,
                project_path=project_path,
                system_prompt=system_prompt,
                scope=effective_scope,
                runtime_manifest=pinned_manifest,
            )

            resolved_model = await self._resolve_model(
                session=session,
                scope=effective_scope,
                agent_def=agent_cfg.agent_def,
                runtime_manifest=pinned_manifest,
            )
            model, provider, model_id, recursion_limit = resolved_model
            model_cache_key = _model_cache_key_from_resolved(
                resolved_model,
                provider,
                model_id,
            )

            # Get checkpointer from storage backend
            checkpointer = await self.storage_backend.get_checkpointer()

            store = await self.storage_backend.get_store()

            from server.app.agent.cognition_agent import CognitionContext

            execution_timeout_seconds = (
                agent_cfg.agent_def.config.timeout_seconds
                if agent_cfg.agent_def is not None
                else None
            )
            request_deadline = (
                int((time.time() + execution_timeout_seconds) * 1000)
                if execution_timeout_seconds is not None
                else None
            )
            invocation_context = CognitionContext.from_scope(
                effective_scope,
                session_id=session.id if session else session_id,
                thread_id=session.thread_id if session else thread_id,
                agent_name=session.agent_name if session else None,
                metadata=session.metadata if session else None,
                request_deadline=request_deadline,
            )

            context_policy = _effective_context_policy(
                agent_cfg.context_policy,
                agent_cfg.tool_token_limit_before_evict,
            )
            context_event = ContextEvent(
                action="policy_resolved",
                session_id=session.id if session else session_id,
                run_id=thread_id,
                scope_keys=sorted((effective_scope or {}).keys()),
                policy=context_policy,
                message_count=getattr(session, "message_count", None),
            )
            _audit_context_event(context_event)
            yield context_event

            agent_params = CognitionAgentParams(
                project_path=project_path,
                model=model,
                model_cache_key=model_cache_key,
                manifest_digest=manifest_digest,
                store=store,
                checkpointer=checkpointer,
                settings=self.settings,
                tools=custom_tools if custom_tools else None,
                system_prompt=agent_cfg.system_prompt,
                skills=agent_cfg.skills if agent_cfg.skills else None,
                subagents=agent_cfg.subagents,
                async_subagents=agent_cfg.async_subagents,
                memory=agent_cfg.memory,
                interrupt_on=agent_cfg.interrupt_on,
                permissions=agent_cfg.permissions,
                response_format=(
                    session.config.response_format if session and session.config else None
                )
                or agent_cfg.response_format,
                tool_token_limit_before_evict=agent_cfg.tool_token_limit_before_evict,
                context_policy=agent_cfg.context_policy,
                excluded_tools=agent_cfg.excluded_tools,
                blocked_tools=agent_cfg.blocked_tools,
                middleware=agent_cfg.middleware,
                mcp_configs=agent_cfg.mcp_configs or None,
                mcp_oauth_repository=self._mcp_oauth_repository,
                mcp_readiness_repository=self._mcp_readiness_repository,
                scope=effective_scope,
                config_store=self._get_config_store(),
                sandbox_profile=agent_cfg.sandbox_profile,
                sandbox_execution_role_arn=agent_cfg.sandbox_execution_role_arn,
                pinned_sandbox_profile_config=_pinned_sandbox_profile(pinned_manifest),
            )
            agent = await create_cognition_agent(agent_params)
            invocation_context.sandbox_backend = agent.sandbox_backend

            if manager and agent.sandbox_backend is not None:
                manager.register_sandbox_backend(
                    session_id,
                    agent.sandbox_backend,
                    run_id=run_id or thread_id,
                    agent_name=session.agent_name if session else None,
                    scope=effective_scope,
                )
                for sandbox_event in manager.drain_sandbox_events(session_id):
                    yield sandbox_event

            runtime = DeepAgentRuntime(
                agent=agent.agent,
                checkpointer=checkpointer,
                thread_id=thread_id,
                recursion_limit=recursion_limit,
                context=invocation_context,
                trace_parent_span=trace_parent_span,
            )
            if manager:
                manager.register_runtime(session_id, runtime)

            # Build message input (system prompt already embedded in agent graph)
            messages = self._build_messages(content, None)

            acc = StreamAccumulator()
            usage_aggregator = ProviderUsageAggregator(
                default_provider=provider,
                default_model=model_id,
            )
            runtime_exception: Exception | None = None

            try:
                try:
                    async for event in _with_execution_timeout(
                        runtime.astream_events(
                            {"messages": messages},
                            thread_id=thread_id,
                        ),
                        execution_timeout_seconds,
                    ):
                        if isinstance(event, TokenEvent):
                            acc.record_token(event.content)
                            yield event

                        elif isinstance(event, ModelUsageEvent):
                            usage_aggregator.observe_usage_metadata(
                                event.call_id,
                                event.usage_metadata,
                                provider=event.provider,
                                model=event.model,
                            )

                        elif isinstance(event, ToolCallEvent):
                            acc.set_tool_call(event.tool_call_id)
                            yield event

                        elif isinstance(event, ToolResultEvent):
                            acc.set_tool_call(None)
                            if manager:
                                for sandbox_event in manager.snapshot_sandbox_backend_events(
                                    session_id,
                                    include_runtime_snapshot=False,
                                ):
                                    yield sandbox_event
                            yield event

                        elif isinstance(event, PlanningEvent) or isinstance(
                            event,
                            (
                                DelegationEvent,
                                ArtifactEvent,
                                StatusEvent,
                                StepCompleteEvent,
                                InterruptEvent,
                                ToolSafetyEvent,
                                ContextEvent,
                            ),
                        ):
                            yield event
                            if isinstance(event, InterruptEvent):
                                return

                        elif isinstance(event, ErrorEvent):
                            yield event
                            return

                    # DoneEvent from the runtime is absorbed here; we emit our own below.

                except TimeoutError:
                    await runtime.abort(thread_id)
                    logger.warning(
                        "Agent execution deadline exceeded",
                        session_id=session_id,
                        thread_id=thread_id,
                        timeout_seconds=execution_timeout_seconds,
                    )
                    yield ErrorEvent(
                        message=(
                            "Agent execution exceeded the configured "
                            f"{execution_timeout_seconds:g} second deadline"
                        ),
                        code="EXECUTION_TIMEOUT",
                    )
                    return
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error(
                        "LangGraph execution failed during stream_response",
                        error=str(exc),
                        session_id=session_id,
                        exc_info=True,
                    )
                    runtime_exception = exc

                if runtime_exception is not None:
                    yield ErrorEvent(
                        message=f"Agent execution failed: {runtime_exception}",
                        code="STREAMING_ERROR",
                    )
                    return

                if acc.accumulated_content:
                    usage_aggregator.mark_unreported_fallback()
                usage_report = usage_aggregator.build_report()
                yield UsageEvent(**usage_report.to_payload())
                yield DoneEvent()

            finally:
                if manager:
                    manager.unregister_runtime(session_id, runtime)

        except LLMProviderConfigError as e:
            logger.error(
                "Provider configuration error",
                error=str(e),
                session_id=session_id,
            )
            yield ErrorEvent(message=str(e), code="PROVIDER_CONFIG_ERROR")
        except AgentDefinitionUnavailableError as e:
            logger.warning(
                "Session agent definition unavailable",
                error=str(e),
                session_id=session_id,
                agent_name=e.agent_name,
                reason=e.reason,
                scope_keys=e.scope_keys,
            )
            yield ErrorEvent(message=str(e), code="AGENT_NOT_FOUND")
        except SandboxQuotaExceededError as e:
            logger.warning("Sandbox quota exceeded", error=str(e), session_id=session_id)
            yield ErrorEvent(message=str(e), code="SANDBOX_QUOTA_EXCEEDED")
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
        trace_parent_span: Any | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Resume an interrupted Deep Agents run from persisted checkpoint state."""
        try:
            session = await self.storage_backend.get_session(session_id, scope)
            if session is None:
                yield ErrorEvent(message=f"Session not found: {session_id}", code="NOT_FOUND")
                return
            effective_scope = _effective_session_scope(session, scope)
            active_run = await self.storage_backend.get_active_run(
                session.id,
                effective_scope,
            )
            if active_run is None:
                raise RuntimeError("Pinned active run manifest was not found at exact scope")

            agent_cfg, custom_tools = await self._resolve_agent_config(
                session=session,
                project_path=project_path,
                scope=effective_scope,
                runtime_manifest=active_run.runtime_manifest,
            )

            resolved_model = await self._resolve_model(
                session=session,
                scope=effective_scope,
                agent_def=agent_cfg.agent_def,
                runtime_manifest=active_run.runtime_manifest,
            )
            model, provider, model_id, recursion_limit = resolved_model
            model_cache_key = _model_cache_key_from_resolved(
                resolved_model,
                provider,
                model_id,
            )
            checkpointer = await self.storage_backend.get_checkpointer()
            store = await self.storage_backend.get_store()

            from server.app.agent.cognition_agent import CognitionContext

            invocation_context = CognitionContext.from_scope(
                effective_scope,
                session_id=session.id,
                thread_id=session.thread_id,
                agent_name=session.agent_name,
                metadata=session.metadata,
            )
            context_policy = _effective_context_policy(
                agent_cfg.context_policy,
                agent_cfg.tool_token_limit_before_evict,
            )
            context_event = ContextEvent(
                action="policy_resolved",
                session_id=session.id,
                run_id=thread_id,
                scope_keys=sorted((effective_scope or {}).keys()),
                policy=context_policy,
                message_count=getattr(session, "message_count", None),
            )
            _audit_context_event(context_event)
            yield context_event

            agent_params = CognitionAgentParams(
                project_path=project_path,
                model=model,
                model_cache_key=model_cache_key,
                manifest_digest=active_run.manifest_digest,
                store=store,
                checkpointer=checkpointer,
                settings=self.settings,
                tools=custom_tools if custom_tools else None,
                system_prompt=agent_cfg.system_prompt,
                skills=agent_cfg.skills if agent_cfg.skills else None,
                subagents=agent_cfg.subagents,
                async_subagents=agent_cfg.async_subagents,
                memory=agent_cfg.memory,
                interrupt_on=agent_cfg.interrupt_on,
                permissions=agent_cfg.permissions,
                response_format=(session.config.response_format if session.config else None)
                or agent_cfg.response_format,
                tool_token_limit_before_evict=agent_cfg.tool_token_limit_before_evict,
                context_policy=agent_cfg.context_policy,
                excluded_tools=agent_cfg.excluded_tools,
                blocked_tools=agent_cfg.blocked_tools,
                middleware=agent_cfg.middleware,
                mcp_configs=agent_cfg.mcp_configs or None,
                mcp_oauth_repository=self._mcp_oauth_repository,
                mcp_readiness_repository=self._mcp_readiness_repository,
                scope=effective_scope,
                config_store=self._get_config_store(),
                sandbox_profile=agent_cfg.sandbox_profile,
                sandbox_execution_role_arn=agent_cfg.sandbox_execution_role_arn,
                pinned_sandbox_profile_config=_pinned_sandbox_profile(active_run.runtime_manifest),
            )
            agent = await create_cognition_agent(agent_params)
            invocation_context.sandbox_backend = agent.sandbox_backend

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
                trace_parent_span=trace_parent_span,
            )

            acc = StreamAccumulator()
            usage_aggregator = ProviderUsageAggregator(
                default_provider=provider,
                default_model=model_id,
            )
            async for event in runtime.astream_resume_events(
                decision=decision,
                tool_name=tool_name,
                args=args,
                thread_id=thread_id,
                trace_parent_span=trace_parent_span,
            ):
                if isinstance(event, TokenEvent):
                    acc.record_token(event.content)
                if isinstance(event, ModelUsageEvent):
                    usage_aggregator.observe_usage_metadata(
                        event.call_id,
                        event.usage_metadata,
                        provider=event.provider,
                        model=event.model,
                    )
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
                        ToolSafetyEvent,
                        ContextEvent,
                        HitlDecisionEvent,
                        StatusEvent,
                        ErrorEvent,
                        UsageEvent,
                        ModelUsageEvent,
                        PlanningEvent,
                        StepCompleteEvent,
                        DelegationEvent,
                    ),
                ):
                    if isinstance(event, ModelUsageEvent):
                        continue
                    yield cast(StreamEvent, event)

            if acc.accumulated_content:
                usage_aggregator.mark_unreported_fallback()
            usage_report = usage_aggregator.build_report()
            yield UsageEvent(**usage_report.to_payload())
            yield DoneEvent()

        except LLMProviderConfigError as e:
            logger.error(
                "Provider configuration error on resume", error=str(e), session_id=session_id
            )
            yield ErrorEvent(message=str(e), code="PROVIDER_CONFIG_ERROR")
        except AgentDefinitionUnavailableError as e:
            logger.warning(
                "Session agent definition unavailable on resume",
                error=str(e),
                session_id=session_id,
                agent_name=e.agent_name,
                reason=e.reason,
                scope_keys=e.scope_keys,
            )
            yield ErrorEvent(message=str(e), code="AGENT_NOT_FOUND")
        except Exception as e:
            logger.error(
                "DeepAgents resume error",
                error=str(e),
                session_id=session_id,
                exc_info=True,
            )
            yield ErrorEvent(message=str(e), code="RESUME_ERROR")

    async def rebuild_message_projection(
        self,
        session_id: str,
        thread_id: str,
        scope: dict[str, str] | None = None,
    ) -> int:
        """Rebuild the API message projection from authoritative checkpoint state."""
        checkpointer = await self.storage_backend.get_checkpointer()
        checkpoint = await checkpointer.aget({"configurable": {"thread_id": thread_id}})
        if checkpoint is None:
            return 0

        checkpoint_messages = checkpoint.get("channel_values", {}).get("messages", [])
        if not isinstance(checkpoint_messages, list):
            return 0
        if not checkpoint_messages:
            existing_messages = await self.storage_backend.list_messages_for_session(
                session_id,
                scope,
            )
            return len(existing_messages)

        rebuilt_count = await self.storage_backend.rebuild_message_projection(
            session_id=session_id,
            thread_id=thread_id,
            checkpoint_messages=checkpoint_messages,
            effective_scope=scope,
        )
        return int(rebuilt_count)

    async def _resolve_model(
        self,
        session: Any,
        scope: dict[str, str] | None,
        agent_def: Any | None = None,
        runtime_manifest: Mapping[str, Any] | None = None,
    ) -> ResolvedRuntimeModel:
        """Resolve provider config and build a LangChain BaseChatModel.

        Delegates to RuntimeResolver.resolve_runtime_model_for_session().
        """
        if runtime_manifest is not None:
            return await self._get_runtime_resolver().resolve_runtime_model_from_manifest(
                runtime_manifest,
                session=session,
                agent_def=agent_def,
            )
        return await self._get_runtime_resolver().resolve_runtime_model_for_session(
            session=session, scope=scope, agent_def=agent_def
        )

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


class SessionAgentManager:
    """Manages DeepAgent services per session.

    Creates and caches agent services for each session.
    Tracks active streaming operations for abort functionality.
    """

    def __init__(
        self,
        settings: Settings,
        storage_backend: Any | None = None,
        runtime_resolver: RuntimeResolver | None = None,
        config_store: ConfigStore | None = None,
        mcp_oauth_repository: Any | None = None,
        mcp_readiness_repository: Any | None = None,
    ) -> None:
        """Initialize the session manager.

        Args:
            settings: Application settings.
            storage_backend: Initialized storage backend shared with the app lifespan.
            runtime_resolver: Shared runtime resolver instance.
            config_store: Shared config store instance.
        """
        self.settings = settings
        self._storage_backend = storage_backend
        self._runtime_resolver = runtime_resolver
        self._config_store = config_store
        self._mcp_oauth_repository = mcp_oauth_repository
        self._mcp_readiness_repository = mcp_readiness_repository
        self._services: dict[str, DeepAgentStreamingService] = {}
        self._project_paths: dict[str, str] = {}
        self._service_access: dict[str, float] = {}
        self._service_cache_evictions = 0
        self._active_runtimes: dict[str, list[Any]] = {}
        self._sandbox_backends: dict[str, Any] = {}
        self._sandbox_events: dict[str, asyncio.Queue[SandboxLifecycleEvent]] = {}
        self._sandbox_correlations: dict[str, dict[str, Any]] = {}
        self._sandbox_start_history: dict[str, deque[float]] = {}
        self._sandbox_emitted_lifecycle_phases: dict[str, set[str]] = {}
        self._sandbox_backend_type: str = settings.sandbox_backend

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
        self._evict_session_services()
        existing = self._services.get(session_id)
        if existing is not None:
            self._service_access[session_id] = time.monotonic()
            return existing
        service = DeepAgentStreamingService(
            settings=self.settings,
            runtime_resolver=self._runtime_resolver,
            config_store=self._config_store,
            mcp_oauth_repository=self._mcp_oauth_repository,
            mcp_readiness_repository=self._mcp_readiness_repository,
        )
        if self._storage_backend is not None:
            service.storage_backend = self._storage_backend
        self._services[session_id] = service
        self._project_paths[session_id] = project_path
        self._service_access[session_id] = time.monotonic()
        self._evict_session_services()
        RUNTIME_CACHE_SIZE.labels(cache="session_service").set(len(self._services))
        logger.info(
            "Session registered with DeepAgents",
            session_id=session_id,
            project_path=project_path,
        )
        return service

    def get_service(self, session_id: str) -> DeepAgentStreamingService | None:
        """Get the agent service for a session."""
        self._evict_session_services()
        service = self._services.get(session_id)
        if service is not None:
            self._service_access[session_id] = time.monotonic()
        return service

    def get_project_path(self, session_id: str) -> str | None:
        """Get the project path for a session."""
        if session_id in self._services:
            self._service_access[session_id] = time.monotonic()
        return self._project_paths.get(session_id)

    def _evict_session_services(self) -> None:
        """Evict expired/oldest idle services and their sandbox resources."""
        now = time.monotonic()
        ttl = self.settings.session_service_cache_ttl_seconds
        candidates = sorted(
            (
                (last_access, session_id)
                for session_id, last_access in self._service_access.items()
                if not self._active_runtimes.get(session_id)
            ),
        )
        expired = [session_id for last_access, session_id in candidates if now - last_access > ttl]
        for session_id in expired:
            self.unregister_session(session_id)
            self._service_cache_evictions += 1
            RUNTIME_CACHE_EVICTIONS_TOTAL.labels(cache="session_service", reason="ttl").inc()

        while len(self._services) > self.settings.session_service_cache_max_entries:
            idle = sorted(
                (
                    (last_access, session_id)
                    for session_id, last_access in self._service_access.items()
                    if not self._active_runtimes.get(session_id)
                ),
            )
            if not idle:
                break
            self.unregister_session(idle[0][1])
            self._service_cache_evictions += 1
            RUNTIME_CACHE_EVICTIONS_TOTAL.labels(cache="session_service", reason="capacity").inc()

    def get_service_cache_stats(self) -> dict[str, int]:
        """Return safe cache cardinality/eviction metrics."""
        return {
            "size": len(self._services),
            "evictions": self._service_cache_evictions,
        }

    def get_runtime(self, session_id: str) -> Any | None:
        """Get the active runtime for a session, if any."""
        runtimes = self._active_runtimes.get(session_id) or []
        return runtimes[-1] if runtimes else None

    def active_runtime_count(self, session_id: str) -> int:
        """Return the number of active runtime turns for a session."""
        return len(self._active_runtimes.get(session_id) or [])

    def register_runtime(self, session_id: str, runtime: Any) -> None:
        """Register an active runtime for abort tracking."""
        runtimes = self._active_runtimes.setdefault(session_id, [])
        runtimes.append(runtime)
        logger.debug(
            "Runtime registered for abort tracking",
            session_id=session_id,
            active_runtime_count=len(runtimes),
        )

    def unregister_runtime(self, session_id: str, runtime: Any | None = None) -> None:
        """Unregister a runtime when streaming completes."""
        runtimes = self._active_runtimes.get(session_id)
        if not runtimes:
            logger.debug("Runtime unregistered", session_id=session_id, active_runtime_count=0)
            return

        if runtime is None:
            runtimes.pop()
        else:
            try:
                runtimes.remove(runtime)
            except ValueError:
                logger.debug(
                    "Runtime unregister skipped for non-current runtime",
                    session_id=session_id,
                    active_runtime_count=len(runtimes),
                )
                return

        if runtimes:
            logger.debug(
                "Runtime unregistered",
                session_id=session_id,
                active_runtime_count=len(runtimes),
            )
        else:
            self._active_runtimes.pop(session_id, None)
            logger.debug("Runtime unregistered", session_id=session_id, active_runtime_count=0)

    async def abort_session(self, session_id: str, thread_id: str | None = None) -> bool:
        """Abort the current operation for a session.

        Returns:
            True if abort was signaled, False if no active runtime.
        """
        runtime = self.get_runtime(session_id)
        if runtime:
            success = bool(await runtime.abort(thread_id))
            logger.info("Session abort signaled", session_id=session_id, success=success)
            return success
        logger.warning("No active runtime to abort", session_id=session_id)
        return False

    def register_sandbox_backend(
        self,
        session_id: str,
        backend: Any,
        *,
        run_id: str | None = None,
        agent_name: str | None = None,
        scope: Mapping[str, str] | None = None,
    ) -> None:
        """Register a sandbox backend for lifecycle tracking.

        The backend's ``terminate()`` method will be called when the session
        is unregistered, cleaning up any K8s Sandbox CRs or Docker containers.

        Args:
            session_id: Unique session identifier.
            backend: The sandbox backend instance (must have a ``terminate()`` method).
            run_id: Durable run identifier, if available.
            agent_name: Agent definition bound to the session.
            scope: Trusted builder-authorized effective scope.
        """
        correlation = self._sandbox_correlation(
            session_id=session_id,
            backend=backend,
            run_id=run_id,
            agent_name=agent_name,
            scope=scope,
        )
        quota = getattr(backend, "quota", None)
        if quota is not None:
            self._enforce_sandbox_quota(
                session_id=session_id,
                quota_key=str(correlation["quota_key"]),
                quota=quota,
            )

        self._sandbox_correlations[session_id] = correlation
        self._sandbox_backends[session_id] = backend
        sandbox_id = getattr(backend, "id", str(id(backend)))
        self._emit_sandbox_event(
            session_id,
            SandboxLifecycleEvent(
                sandbox_id=sandbox_id,
                phase="provisioned",
                sandbox_backend=self._sandbox_backend_type,
                is_warm_pool_hit=getattr(backend, "_warm_pool", None) is not None,
                metadata=self._sandbox_runtime_metadata(backend, session_id=session_id),
            ),
        )
        self._sandbox_emitted_lifecycle_phases.setdefault(session_id, set()).add("provisioned")
        logger.debug("Sandbox backend registered", session_id=session_id)

    def _sandbox_correlation(
        self,
        *,
        session_id: str,
        backend: Any,
        run_id: str | None,
        agent_name: str | None,
        scope: Mapping[str, str] | None,
    ) -> dict[str, Any]:
        profile = getattr(backend, "profile", None)
        scope_keys = sorted((scope or {}).keys())
        scope_fingerprint = _scope_fingerprint(scope)
        quota_scope = scope_fingerprint or "global"
        quota_profile = str(profile or "default")
        return {
            "session_id": session_id,
            "run_id": run_id,
            "agent_name": agent_name,
            "profile": profile,
            "scope_keys": scope_keys,
            "scope_fingerprint": scope_fingerprint,
            "quota_key": f"{quota_profile}:{quota_scope}",
        }

    def _enforce_sandbox_quota(self, *, session_id: str, quota_key: str, quota: Any) -> None:
        max_concurrent = getattr(quota, "max_concurrent_sessions", None)
        if max_concurrent is not None:
            active = sum(
                1
                for active_session_id, correlation in self._sandbox_correlations.items()
                if active_session_id != session_id and correlation.get("quota_key") == quota_key
            )
            if active >= max_concurrent:
                raise SandboxQuotaExceededError(
                    "Lambda MicroVM sandbox quota exceeded: "
                    f"max_concurrent_sessions={max_concurrent} for {quota_key}"
                )

        max_starts = getattr(quota, "max_session_starts_per_minute", None)
        if max_starts is None:
            return

        now = time.monotonic()
        history = self._sandbox_start_history.setdefault(quota_key, deque())
        cutoff = now - 60.0
        while history and history[0] < cutoff:
            history.popleft()
        if len(history) >= max_starts:
            raise SandboxQuotaExceededError(
                "Lambda MicroVM sandbox quota exceeded: "
                f"max_session_starts_per_minute={max_starts} for {quota_key}"
            )
        history.append(now)

    def _sandbox_runtime_metadata(
        self,
        backend: Any,
        *,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        """Return token-free backend runtime metadata when the backend exposes it."""
        try:
            metadata = getattr(backend, "runtime_metadata", {})
        except Exception as exc:
            logger.debug("Sandbox runtime metadata unavailable", error=str(exc))
            return {}
        safe_metadata = dict(metadata) if isinstance(metadata, dict) else {}
        if session_id is not None:
            correlation = self._sandbox_correlations.get(session_id)
            if correlation is not None:
                safe_metadata["correlation"] = {
                    key: value
                    for key, value in correlation.items()
                    if key != "quota_key" and value is not None
                }
        return safe_metadata

    def snapshot_sandbox_backend(
        self,
        session_id: str,
        phase: str = "runtime_snapshot",
    ) -> SandboxLifecycleEvent | None:
        """Build a lifecycle event with the current backend metadata snapshot."""
        backend = self._sandbox_backends.get(session_id)
        if backend is None:
            return None
        metadata = self._sandbox_runtime_metadata(backend, session_id=session_id)
        if not metadata:
            return None
        return SandboxLifecycleEvent(
            sandbox_id=getattr(backend, "id", str(id(backend))),
            phase=phase,
            sandbox_backend=self._sandbox_backend_type,
            metadata=metadata,
        )

    def snapshot_sandbox_backend_events(
        self,
        session_id: str,
        *,
        include_runtime_snapshot: bool = True,
    ) -> list[SandboxLifecycleEvent]:
        """Build lifecycle events for newly observed backend phases."""
        backend = self._sandbox_backends.get(session_id)
        if backend is None:
            return []
        metadata = self._sandbox_runtime_metadata(backend, session_id=session_id)
        if not metadata:
            return []

        sandbox_id = getattr(backend, "id", str(id(backend)))
        emitted = self._sandbox_emitted_lifecycle_phases.setdefault(session_id, set())
        events: list[SandboxLifecycleEvent] = []
        lifecycle_phases = metadata.get("lifecycle_phases")
        if isinstance(lifecycle_phases, list):
            for raw_phase in lifecycle_phases:
                phase = str(raw_phase)
                if phase in emitted:
                    continue
                emitted.add(phase)
                event = SandboxLifecycleEvent(
                    sandbox_id=sandbox_id,
                    phase=phase,
                    sandbox_backend=self._sandbox_backend_type,
                    metadata=metadata,
                )
                self._log_sandbox_lifecycle_event(event)
                events.append(event)

        if include_runtime_snapshot:
            event = SandboxLifecycleEvent(
                sandbox_id=sandbox_id,
                phase="runtime_snapshot",
                sandbox_backend=self._sandbox_backend_type,
                metadata=metadata,
            )
            self._log_sandbox_lifecycle_event(event)
            events.append(event)
        return events

    def _teardown_phase_from_metadata(self, metadata: Mapping[str, Any]) -> str:
        teardown_status = metadata.get("teardown_status")
        if teardown_status == "pending":
            return "teardown_pending"
        if teardown_status == "failed":
            return "teardown_failed"
        if teardown_status in {"complete", "skipped"}:
            return "teardown_complete"
        aws_state = metadata.get("aws_state") or metadata.get("status")
        if aws_state == "TERMINATED":
            return "teardown_complete"
        return "teardown_complete"

    def _log_sandbox_lifecycle_event(self, event: SandboxLifecycleEvent) -> None:
        metadata = event.metadata or {}
        correlation = metadata.get("correlation")
        if not isinstance(correlation, Mapping):
            correlation = {}
        fields = {
            "session_id": correlation.get("session_id"),
            "run_id": correlation.get("run_id"),
            "agent_name": correlation.get("agent_name"),
            "sandbox_profile": metadata.get("profile") or correlation.get("profile"),
            "sandbox_backend": event.sandbox_backend,
            "sandbox_id": event.sandbox_id,
            "microvm_id": metadata.get("microvm_id"),
            "image": metadata.get("image"),
            "image_version": metadata.get("image_version"),
            "region": metadata.get("region"),
            "aws_state": metadata.get("aws_state") or metadata.get("status"),
            "execution_role_fingerprint": metadata.get("execution_role_fingerprint"),
            "scope_fingerprint": correlation.get("scope_fingerprint"),
            "teardown_status": metadata.get("teardown_status"),
            "teardown_attempt": metadata.get("teardown_attempt"),
            "teardown_error_code": metadata.get("teardown_error_code"),
        }
        fields = {key: value for key, value in fields.items() if value is not None}
        if event.phase in {"teardown_pending", "teardown_failed"}:
            logger.warning("Sandbox lifecycle event", phase=event.phase, **fields)
        else:
            logger.info("Sandbox lifecycle event", phase=event.phase, **fields)

    def release_sandbox_backend(self, session_id: str) -> None:
        """Release only the sandbox backend and quota state for a session."""
        backend = self._sandbox_backends.pop(session_id, None)
        if backend is None:
            self._sandbox_correlations.pop(session_id, None)
            self._sandbox_emitted_lifecycle_phases.pop(session_id, None)
            logger.debug("Sandbox backend release skipped", session_id=session_id)
            return

        sandbox_id = getattr(backend, "id", str(id(backend)))
        self._emit_sandbox_event(
            session_id,
            SandboxLifecycleEvent(
                sandbox_id=sandbox_id,
                phase="teardown_started",
                sandbox_backend=self._sandbox_backend_type,
                metadata=self._sandbox_runtime_metadata(backend, session_id=session_id),
            ),
        )
        self._sandbox_emitted_lifecycle_phases.setdefault(session_id, set()).add("teardown_started")
        if hasattr(backend, "terminate"):
            try:
                backend.terminate()
                metadata = self._sandbox_runtime_metadata(
                    backend,
                    session_id=session_id,
                )
                phase = self._teardown_phase_from_metadata(metadata)
                self._emit_sandbox_event(
                    session_id,
                    SandboxLifecycleEvent(
                        sandbox_id=sandbox_id,
                        phase=phase,
                        sandbox_backend=self._sandbox_backend_type,
                        metadata=metadata,
                    ),
                )
                logger.info("Sandbox backend released", session_id=session_id, phase=phase)
            except Exception as e:
                metadata = self._sandbox_runtime_metadata(backend, session_id=session_id)
                metadata["teardown_status"] = "failed"
                metadata["teardown_error_code"] = e.__class__.__name__
                metadata["teardown_error_message"] = str(e)
                self._emit_sandbox_event(
                    session_id,
                    SandboxLifecycleEvent(
                        sandbox_id=sandbox_id,
                        phase="teardown_failed",
                        sandbox_backend=self._sandbox_backend_type,
                        metadata=metadata,
                    ),
                )
                logger.warning(
                    "Sandbox backend terminate failed", session_id=session_id, error=str(e)
                )

        self._sandbox_correlations.pop(session_id, None)
        self._sandbox_emitted_lifecycle_phases.pop(session_id, None)

    def unregister_session(self, session_id: str) -> None:
        """Unregister a session and clean up resources."""
        self._services.pop(session_id, None)
        self._project_paths.pop(session_id, None)
        self._service_access.pop(session_id, None)
        self._active_runtimes.pop(session_id, None)

        self.release_sandbox_backend(session_id)
        self._sandbox_events.pop(session_id, None)
        self._sandbox_emitted_lifecycle_phases.pop(session_id, None)
        RUNTIME_CACHE_SIZE.labels(cache="session_service").set(len(self._services))
        logger.info("Session unregistered", session_id=session_id)

    def _emit_sandbox_event(self, session_id: str, event: SandboxLifecycleEvent) -> None:
        """Queue a sandbox lifecycle event for the session."""
        if session_id not in self._sandbox_events:
            self._sandbox_events[session_id] = asyncio.Queue(maxsize=20)
        try:
            self._sandbox_events[session_id].put_nowait(event)
            self._log_sandbox_lifecycle_event(event)
        except asyncio.QueueFull:
            pass

    def drain_sandbox_events(self, session_id: str) -> list[SandboxLifecycleEvent]:
        """Drain any pending sandbox lifecycle events."""
        queue = self._sandbox_events.get(session_id)
        if queue is None:
            return []
        events: list[SandboxLifecycleEvent] = []
        while not queue.empty():
            try:
                events.append(queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        return events


# Global manager instance
_agent_manager: SessionAgentManager | None = None


def get_session_agent_manager(settings: Settings) -> SessionAgentManager:
    """Get or create the global session agent manager."""
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = SessionAgentManager(settings)
    return _agent_manager
