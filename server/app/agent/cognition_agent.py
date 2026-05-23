"""Agent factory for creating Deep Agents with SandboxBackend support.

This module creates deep agents using settings-driven sandbox backends:
- Local: CognitionLocalSandboxBackend (development) — shell execution via LocalSandbox
- Docker: CognitionDockerSandboxBackend (production) — isolated container execution
- Kubernetes: CognitionKubernetesSandboxBackend (production) — K8s-native sandbox pods

Both backends provide:
- File operations (ls, read, write, edit) via FilesystemBackend
- Search operations (glob, grep)
- Multi-step ReAct loop with automatic tool chaining

Agent Caching:
Only the compiled agent graph is cached — it is immutable after compilation and safe
to share across sessions. The sandbox backend is stateful (holds sandbox CR, connection)
and must be created fresh per session to prevent cross-session termination bugs.
Use invalidate_agent_cache() or clear_agent_cache() to force recompilation.
Cache keys are RuntimeContext instances that track which config inputs affect the
compiled graph, enabling targeted invalidation instead of all-or-nothing clears.
"""

from __future__ import annotations

import importlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, cast

import structlog
from deepagents import create_deep_agent as _create_deep_agent

logger = structlog.get_logger(__name__)

from server.app.agent.mcp_client import McpServerConfig, create_mcp_client  # noqa: E402
from server.app.agent.middleware import (  # noqa: E402
    CognitionObservabilityMiddleware,
    CognitionStreamingMiddleware,
    ToolArgumentValidationMiddleware,
    ToolSecurityMiddleware,
    TrustedRuntimeContextMiddleware,
)
from server.app.agent.prompts import SYSTEM_PROMPT  # noqa: E402
from server.app.agent.sandbox_backend import create_sandbox_backend  # noqa: E402
from server.app.agent.tools import BrowserTool, InspectPackageTool, SearchTool  # noqa: E402
from server.app.settings import Settings, get_settings  # noqa: E402
from server.app.storage.config_store import ConfigStore  # noqa: E402

DeepAgentResponseFormat = Any
create_deep_agent: Any = _create_deep_agent


@dataclass
class CognitionContext:
    """Invocation context passed to each agent run.

    LangGraph threads this through ``runtime.context`` so nodes and middleware
    can scope Store namespaces to the builder-authorized effective scope
    without needing to pass scope explicitly through every tool call.

    Attributes:
        effective_scope: Builder-defined scope key-value pairs (e.g.
            ``{"tenant": "acme", "project": "ios", "end_user": "user_123"}``).
            Cognition does not hardcode a vocabulary — builders own scope keys.
        session_id: Cognition session identifier, when available.
        thread_id: LangGraph checkpoint thread id, when available.
        agent_name: Agent definition bound to the session, when available.
        metadata: Builder-provided session metadata. This is not a secret channel.
    """

    effective_scope: dict[str, str] = field(default_factory=dict)
    session_id: str | None = None
    thread_id: str | None = None
    agent_name: str | None = None
    metadata: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_scope(
        cls,
        scope: dict[str, str] | None,
        *,
        session_id: str | None = None,
        thread_id: str | None = None,
        agent_name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> CognitionContext:
        return cls(
            effective_scope=dict(scope or {}),
            session_id=session_id,
            thread_id=thread_id,
            agent_name=agent_name,
            metadata=dict(metadata or {}),
        )


@dataclass(frozen=True)
class RuntimeContext:
    """Tracks which config inputs produced a cached agent graph.

    Replaces the MD5 hash approach. Because the dataclass is frozen and
    contains only hashable fields, it can be used directly as a dict key.
    Targeted invalidation is now possible: clearing all entries whose
    ``scope`` matches a given dict, or whose ``agent_name`` matches.
    """

    project_path: str
    model_key: str
    store_type: str
    system_prompt: str
    memory: tuple[str, ...]
    skills: tuple[str, ...]
    subagent_count: int
    async_subagents: tuple[tuple[str, str, str, str], ...]
    interrupt_on: tuple[tuple[str, str], ...]
    permissions: tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...]
    response_format: str
    tool_token_limit_before_evict: int | None
    context_policy: str
    middleware_count: int
    tools_count: int
    sandbox_backend: str
    scope: tuple[tuple[str, str], ...]

    @classmethod
    def from_params(
        cls,
        project_path: Path,
        model: Any,
        store: Any,
        system_prompt: str | None,
        memory: Sequence[str] | None,
        skills: Sequence[str] | None,
        subagents: Sequence[Any] | None,
        async_subagents: Sequence[Any] | None,
        interrupt_on: Mapping[str, Any] | None,
        permissions: Sequence[Any] | None,
        response_format: str | type[Any] | None,
        tool_token_limit_before_evict: int | None,
        context_policy: Any | None,
        middleware: Sequence[Any] | None,
        tools: Sequence[Any] | None,
        settings: Settings,
        scope: dict[str, str] | None,
    ) -> RuntimeContext:
        return cls(
            project_path=str(project_path.resolve()),
            model_key=_model_cache_key(model),
            store_type=store.__class__.__name__ if store else "None",
            system_prompt=system_prompt or "default",
            memory=tuple(sorted(memory)) if memory else (),
            skills=tuple(sorted(skills)) if skills else (),
            subagent_count=len(subagents) if subagents else 0,
            async_subagents=_async_subagents_cache_key(async_subagents),
            interrupt_on=_mapping_cache_key(interrupt_on),
            permissions=_permissions_cache_key(permissions),
            response_format=(
                response_format
                if isinstance(response_format, str)
                else getattr(response_format, "__name__", "None")
            )
            if response_format
            else "None",
            tool_token_limit_before_evict=tool_token_limit_before_evict,
            context_policy=_json_cache_key(context_policy),
            middleware_count=len(middleware) if middleware else 0,
            tools_count=len(tools) if tools else 0,
            sandbox_backend=settings.sandbox_backend,
            scope=tuple(sorted((scope or {}).items())),
        )


_agent_cache: dict[RuntimeContext, Any] = {}


def _model_cache_key(model: Any) -> str:
    if model is None:
        return "None"
    type_name = type(model).__name__
    model_id = (
        getattr(model, "model_name", None)
        or getattr(model, "model_id", None)
        or getattr(model, "model", None)
    )
    if model_id:
        return f"{type_name}:{model_id}"
    return type_name


def _mapping_cache_key(mapping: Mapping[str, Any] | None) -> tuple[tuple[str, str], ...]:
    if not mapping:
        return ()
    return tuple(
        sorted(
            (
                str(key),
                json.dumps(value, sort_keys=True, default=str),
            )
            for key, value in mapping.items()
        )
    )


def _json_cache_key(value: Any | None) -> str:
    if value is None:
        return "None"
    if hasattr(value, "model_dump"):
        value = value.model_dump(exclude_none=True)
    return json.dumps(value, sort_keys=True, default=str)


def _async_subagent_dict(async_subagent: Any) -> dict[str, Any]:
    if hasattr(async_subagent, "model_dump"):
        return cast(dict[str, Any], async_subagent.model_dump(exclude_none=True))
    if isinstance(async_subagent, Mapping):
        return dict(async_subagent)
    return {
        "name": getattr(async_subagent, "name", ""),
        "description": getattr(async_subagent, "description", ""),
        "graph_id": getattr(async_subagent, "graph_id", ""),
        "url": getattr(async_subagent, "url", ""),
    }


def _async_subagents_cache_key(
    async_subagents: Sequence[Any] | None,
) -> tuple[tuple[str, str, str, str], ...]:
    if not async_subagents:
        return ()
    return tuple(
        sorted(
            (
                str(data.get("name", "")),
                str(data.get("description", "")),
                str(data.get("graph_id", "")),
                str(data.get("url", "")),
            )
            for data in (_async_subagent_dict(item) for item in async_subagents)
        )
    )


def _resolve_async_subagents(async_subagents: Sequence[Any] | None) -> list[dict[str, Any]]:
    if not async_subagents:
        return []
    resolved: list[dict[str, Any]] = []
    for item in async_subagents:
        data = _async_subagent_dict(item)
        spec = {
            "name": str(data["name"]),
            "description": str(data["description"]),
            "graph_id": str(data["graph_id"]),
        }
        if data.get("url"):
            spec["url"] = str(data["url"])
        resolved.append(spec)
    return resolved


def _context_policy_enables_summarization_tool(context_policy: Any | None) -> bool:
    if context_policy is None:
        return False
    if hasattr(context_policy, "summarization_enabled"):
        return bool(context_policy.summarization_enabled)
    if isinstance(context_policy, Mapping):
        return bool(context_policy.get("summarization_enabled", False))
    return False


def _permission_dict(permission: Any) -> dict[str, Any]:
    if hasattr(permission, "model_dump"):
        return cast(dict[str, Any], permission.model_dump())
    if isinstance(permission, Mapping):
        return dict(permission)
    return {
        "operations": list(getattr(permission, "operations", [])),
        "paths": list(getattr(permission, "paths", [])),
        "mode": getattr(permission, "mode", "allow"),
    }


def _permissions_cache_key(
    permissions: Sequence[Any] | None,
) -> tuple[tuple[tuple[str, ...], tuple[str, ...], str], ...]:
    if not permissions:
        return ()
    normalized = []
    for permission in permissions:
        data = _permission_dict(permission)
        normalized.append(
            (
                tuple(sorted(str(op) for op in data.get("operations", []))),
                tuple(sorted(str(path) for path in data.get("paths", []))),
                str(data.get("mode", "allow")),
            )
        )
    return tuple(sorted(normalized))


def _resolve_filesystem_permissions(permissions: Sequence[Any] | None) -> list[Any] | None:
    if not permissions:
        return None

    from deepagents.middleware.filesystem import FilesystemPermission

    return [
        FilesystemPermission(**_permission_dict(permission))
        for permission in permissions
    ]


def get_cached_agent(ctx: RuntimeContext) -> Any | None:
    return _agent_cache.get(ctx)


def cache_agent(ctx: RuntimeContext, agent: Any) -> None:
    _agent_cache[ctx] = agent


def invalidate_agent_cache(ctx: RuntimeContext) -> None:
    _agent_cache.pop(ctx, None)


def invalidate_agent_cache_for_scope(scope: dict[str, str]) -> int:
    scope_items = tuple(sorted(scope.items()))
    to_remove = [ctx for ctx in _agent_cache if ctx.scope == scope_items]
    for ctx in to_remove:
        del _agent_cache[ctx]
    logger.info("Agent cache cleared on config change", scope=scope, cleared=len(to_remove))
    return len(to_remove)


def clear_agent_cache() -> None:
    _agent_cache.clear()


def get_agent_cache_stats() -> dict[str, int]:
    return {"size": len(_agent_cache)}


class CognitionAgentResult(NamedTuple):
    agent: Any
    sandbox_backend: Any | None = None


@dataclass
class CognitionAgentParams:
    """Parameter object for create_cognition_agent().

    Replaces the previous 17-arg signature with a single structured object.
    Callers construct this explicitly, making it clear what inputs affect the
    agent graph.
    """

    project_path: str | Path
    model: Any = None
    store: Any = None
    checkpointer: Any = None
    system_prompt: str | None = None
    memory: Sequence[str] | None = None
    skills: Sequence[str] | None = None
    subagents: Sequence[Any] | None = None
    async_subagents: Sequence[Any] | None = None
    interrupt_on: Mapping[str, Any] | None = None
    permissions: Sequence[Any] | None = None
    response_format: str | type[Any] | None = None
    tool_token_limit_before_evict: int | None = None
    context_policy: Any | None = None
    middleware: Sequence[Any] | None = None
    tools: Sequence[Any] | None = None
    settings: Settings | None = None
    mcp_configs: Sequence[McpServerConfig] | None = None
    scope: dict[str, str] | None = None
    config_store: ConfigStore | None = None


def _create_sandbox(
    project_path: Path,
    sandbox_id: str,
    settings: Settings,
    k8s_labels: dict[str, str] | None,
) -> Any:
    return create_sandbox_backend(
        root_dir=project_path,
        sandbox_id=sandbox_id,
        sandbox_backend=settings.sandbox_backend,
        docker_image=settings.docker_image,
        docker_network=settings.docker_network,
        docker_memory_limit=settings.docker_memory_limit,
        docker_cpu_limit=settings.docker_cpu_limit,
        docker_host_workspace="",
        k8s_template=settings.k8s_sandbox_template,
        k8s_namespace=settings.k8s_sandbox_namespace,
        k8s_router_url=settings.k8s_sandbox_router_url,
        k8s_ttl=settings.k8s_sandbox_ttl,
        k8s_warm_pool=settings.k8s_sandbox_warm_pool,
        labels=k8s_labels or None,
    )


def _inject_subagent_middleware(subagents: list[Any], middleware: list[Any]) -> list[Any]:
    """Inject Cognition security/observability middleware into subagent specs.

    Without this, blocked tools can be called through subagents without audit
    logging or prevention — a security bypass.
    """
    security_middleware = [
        m
        for m in middleware
        if isinstance(m, (ToolSecurityMiddleware, CognitionObservabilityMiddleware))
    ]
    if not security_middleware:
        return subagents

    result: list[Any] = []
    for s in subagents:
        if isinstance(s, dict):
            if "graph_id" in s:
                result.append(s)
                continue
            existing = s.get("middleware") or []
            existing_types = {type(m).__name__ for m in existing}
            for m in security_middleware:
                if type(m).__name__ not in existing_types:
                    existing = list(existing) + [m]
            result.append({**s, "middleware": existing})
        else:
            result.append(s)
    return result


async def create_cognition_agent(params: CognitionAgentParams) -> CognitionAgentResult:
    """Create a Deep Agent for the Cognition system.

    Args:
        params: Structured agent configuration. See CognitionAgentParams.

    Returns:
        Configured Deep Agent ready to handle coding tasks with multi-step support.
    """
    settings = params.settings or get_settings()
    project_path = Path(params.project_path).resolve()

    sandbox_id = f"cognition-{project_path.name}"
    k8s_labels: dict[str, str] | None = None
    if params.scope:
        k8s_labels = {}
        for key, value in params.scope.items():
            safe_key = key.replace("_", "-")
            k8s_labels[f"cognition.io/{safe_key}"] = str(value)
        k8s_labels["cognition.io/session"] = sandbox_id

    config_store = params.config_store
    if config_store is None:
        try:
            from server.app.api.dependencies import get_config_store

            config_store = get_config_store()
        except RuntimeError:
            config_store = None

    runtime_ctx = RuntimeContext.from_params(
        project_path=project_path,
        model=getattr(params.model, "model_name", None)
        or getattr(params.model, "model_id", None)
        or getattr(params.model, "model", None)
        or str(params.model),
        store=params.store,
        system_prompt=params.system_prompt,
        memory=params.memory,
        skills=params.skills,
        subagents=params.subagents,
        async_subagents=params.async_subagents,
        interrupt_on=params.interrupt_on,
        permissions=params.permissions,
        response_format=params.response_format,
        tool_token_limit_before_evict=params.tool_token_limit_before_evict,
        context_policy=params.context_policy,
        middleware=params.middleware,
        tools=params.tools,
        settings=settings,
        scope=params.scope,
    )

    cached_agent = get_cached_agent(runtime_ctx)
    if cached_agent is not None:
        sandbox_backend = _create_sandbox(project_path, sandbox_id, settings, k8s_labels)
        return CognitionAgentResult(agent=cached_agent, sandbox_backend=sandbox_backend)

    sandbox_backend = _create_sandbox(project_path, sandbox_id, settings, k8s_labels)

    defaults_resolved = False
    agent_defaults: Any = None

    async def _defaults() -> Any:
        nonlocal defaults_resolved, agent_defaults
        if not defaults_resolved and config_store is not None:
            agent_defaults = await config_store.get_global_agent_defaults(params.scope)
            defaults_resolved = True
        return agent_defaults

    if params.memory is not None:
        agent_memory = list(params.memory)
    else:
        defaults = await _defaults()
        agent_memory = defaults.memory if defaults else ["AGENTS.md"]

    if params.skills is not None:
        attached_skill_names = list(params.skills)
    else:
        defaults = await _defaults()
        attached_skill_names = defaults.skills if defaults else []

    agent_skills = ["/skills/api/"] if attached_skill_names else []

    from deepagents.backends.protocol import BackendProtocol

    backend: BackendProtocol
    if config_store is not None:
        from deepagents.backends.composite import CompositeBackend

        from server.app.agent.artifacts_backend import ArtifactBackend
        from server.app.agent.skills_backend import ConfigRegistrySkillsBackend
        from server.app.api.dependencies import get_artifact_store

        reg = getattr(config_store, "config_registry", None)
        if reg is not None:
            db_skills_backend = ConfigRegistrySkillsBackend(
                registry=reg,
                scope=params.scope,
                allowed_skill_names=attached_skill_names,
            )
            try:
                artifact_store = get_artifact_store()
            except RuntimeError:
                artifact_store = None

            routes: dict[str, BackendProtocol] = {"/skills/api/": db_skills_backend}
            if artifact_store is not None:
                artifact_backend = ArtifactBackend(
                    artifact_store=artifact_store,
                    scope=params.scope,
                )
                routes.update({
                    "/scratch/": artifact_backend,
                    "/artifacts/": artifact_backend,
                    "/contracts/": artifact_backend,
                    "/evals/": artifact_backend,
                    "/memories/": artifact_backend,
                    "/policies/": artifact_backend,
                })
            backend = CompositeBackend(
                default=sandbox_backend,
                routes=routes,
            )
        else:
            backend = sandbox_backend
    else:
        backend = sandbox_backend

    if params.subagents is not None:
        raw_subagents = list(params.subagents)
    else:
        defaults = await _defaults()
        raw_subagents = list(defaults.subagents) if defaults else []

    if params.async_subagents is not None:
        raw_async_subagents = list(params.async_subagents)
    else:
        defaults = await _defaults()
        raw_async_subagents = list(defaults.async_subagents) if defaults else []

    agent_interrupt_on: dict[str, Any]
    agent_permissions: Sequence[Any] | None
    agent_response_format: str | type[Any] | None = params.response_format
    agent_context_policy = params.context_policy

    if params.permissions is not None:
        agent_permissions = list(params.permissions)
    else:
        defaults = await _defaults()
        agent_permissions = list(defaults.permissions) if defaults and defaults.permissions else None

    if params.interrupt_on is not None:
        agent_interrupt_on = dict(params.interrupt_on)
    else:
        defaults = await _defaults()
        if defaults:
            agent_interrupt_on = dict(defaults.interrupt_on)
        else:
            agent_interrupt_on = {}

    defaults = await _defaults()
    if defaults:
        if agent_response_format is None:
            agent_response_format = defaults.response_format
        if agent_context_policy is None:
            agent_context_policy = defaults.context_policy

    if params.system_prompt:
        prompt = params.system_prompt
    else:
        if config_store is not None:
            prov_defaults = await config_store.get_global_provider_defaults(params.scope)
            prompt_type = prov_defaults.system_prompt_type
            prompt_value = prov_defaults.system_prompt_value
            from server.app.models import PromptConfig

            try:
                prompt = PromptConfig(type=prompt_type, value=prompt_value).get_prompt_text()
            except (FileNotFoundError, RuntimeError):
                prompt = SYSTEM_PROMPT
        else:
            prompt = SYSTEM_PROMPT

    agent_middleware = list(params.middleware) if params.middleware else []
    blocked_tools = list(settings.blocked_tools) if hasattr(settings, "blocked_tools") else []

    built_in_tools = [BrowserTool(), SearchTool(), InspectPackageTool()]
    agent_tools = list(params.tools) if params.tools else []
    agent_tools.extend(built_in_tools)

    if params.mcp_configs:
        from server.app.agent.mcp_client import (
            _build_mcp_callbacks,
            _build_mcp_interceptors,
        )

        enabled_configs = [c for c in params.mcp_configs if c.enabled]
        if enabled_configs:
            try:
                mcp_callbacks = _build_mcp_callbacks()
                mcp_interceptors = _build_mcp_interceptors(params.scope)
                mcp_client = create_mcp_client(
                    enabled_configs,
                    callbacks=mcp_callbacks,
                    tool_interceptors=mcp_interceptors,
                )
                mcp_tools = await mcp_client.get_tools()
                agent_tools.extend(mcp_tools)
                logger.info("MCP tools loaded", count=len(mcp_tools))
            except Exception as e:
                logger.error("Failed to initialize MCP tools", error=str(e))

    if _context_policy_enables_summarization_tool(agent_context_policy):
        try:
            from deepagents.middleware.summarization import (
                create_summarization_tool_middleware,
            )

            agent_middleware.append(
                create_summarization_tool_middleware(params.model, backend)
            )
        except Exception as e:
            logger.warning("Failed to add Deep Agents summarization tool", error=str(e))

    agent_middleware.extend(
        [
            CognitionObservabilityMiddleware(),
            CognitionStreamingMiddleware(),
            TrustedRuntimeContextMiddleware(),
            ToolSecurityMiddleware(blocked_tools=blocked_tools),
            ToolArgumentValidationMiddleware(),
        ]
    )

    agent_subagents = [
        {**s, "description": s.get("description", "")} if isinstance(s, dict) else s
        for s in raw_subagents
    ]
    agent_subagents.extend(_resolve_async_subagents(raw_async_subagents))

    agent_subagents = _inject_subagent_middleware(agent_subagents, agent_middleware)

    logger.debug(
        "creating_deep_agent",
        skills=agent_skills,
        backend_type=type(backend).__name__,
        has_composite_routes=hasattr(backend, "routes")
        and getattr(backend, "routes", None) is not None,
    )

    resolved_response_format: DeepAgentResponseFormat = _resolve_response_format(
        agent_response_format
    )

    create_kwargs = cast(
        Any,
        {
            "model": params.model,
            "tools": agent_tools,
            "system_prompt": prompt,
            "backend": backend,
            "checkpointer": params.checkpointer,
            "store": params.store,
            "context_schema": CognitionContext,
            "memory": agent_memory,
            "skills": agent_skills,
            "subagents": cast(Any, agent_subagents),
            "interrupt_on": cast(Any, agent_interrupt_on),
            "response_format": resolved_response_format,
            "middleware": agent_middleware,
        },
    )
    if agent_permissions is not None:
        create_kwargs["permissions"] = _resolve_filesystem_permissions(agent_permissions)

    agent = cast(Any, create_deep_agent)(**create_kwargs)

    result = CognitionAgentResult(agent=agent, sandbox_backend=sandbox_backend)
    cache_agent(runtime_ctx, agent)

    return result


def _resolve_response_format(response_format: str | type[Any] | None) -> type[Any] | None:
    if response_format is None or isinstance(response_format, type):
        return response_format

    module_path, _, attr = response_format.rpartition(".")
    if not module_path or not attr:
        raise ValueError("response_format must be a dotted Python path to a Pydantic model class")

    module = importlib.import_module(module_path)
    resolved = getattr(module, attr)
    if not isinstance(resolved, type):
        raise TypeError(f"response_format must resolve to a class, got: {response_format}")
    return resolved
