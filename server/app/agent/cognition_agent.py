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
Cache keys are scope-aware so per-user/per-project config overrides each get
their own compiled graph.
"""

from __future__ import annotations

import hashlib
import importlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, NamedTuple, cast

import structlog
from deepagents import create_deep_agent as _create_deep_agent

logger = structlog.get_logger(__name__)

from server.app.agent.mcp_adapter import create_mcp_tools  # noqa: E402
from server.app.agent.mcp_client import McpManager, McpServerConfig  # noqa: E402
from server.app.agent.middleware import (  # noqa: E402
    CognitionObservabilityMiddleware,
    CognitionStreamingMiddleware,
    ToolSecurityMiddleware,
)
from server.app.agent.sandbox_backend import create_sandbox_backend  # noqa: E402
from server.app.agent.tools import BrowserTool, InspectPackageTool, SearchTool  # noqa: E402
from server.app.settings import Settings, get_settings  # noqa: E402

DeepAgentResponseFormat = Any
create_deep_agent: Any = _create_deep_agent

# Global agent cache: cache_key -> compiled_agent
_agent_cache: dict[str, Any] = {}


@dataclass
class CognitionContext:
    """Invocation context passed to each agent run.

    LangGraph threads this through ``runtime.context`` so nodes and middleware
    can scope Store namespaces to the requesting user/org/project without
    needing to pass scope explicitly through every tool call.

    Attributes:
        user_id: Primary user identifier for Store namespace isolation.
        org_id: Optional organisation identifier for org-shared namespaces.
        project_id: Optional project identifier for project-scoped namespaces.
        extra: Additional scope dimensions from session.scopes.
    """

    user_id: str = "anonymous"
    org_id: str | None = None
    project_id: str | None = None
    extra: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_scope(cls, scope: dict[str, str] | None) -> CognitionContext:
        """Build a CognitionContext from a session scope dict.

        Maps well-known keys (``user``, ``org``, ``project``) to typed
        attributes; all other keys are stored in ``extra``.

        Args:
            scope: Session scope dict, e.g. ``{"user": "alice", "org": "acme"}``.

        Returns:
            CognitionContext with scope dimensions filled in.
        """
        if not scope:
            return cls()
        return cls(
            user_id=scope.get("user", "anonymous"),
            org_id=scope.get("org"),
            project_id=scope.get("project"),
            extra={k: v for k, v in scope.items() if k not in ("user", "org", "project")},
        )


def _model_cache_key(model: Any) -> str:
    """Return a stable string that identifies the model instance.

    Uses model_name or model_id attributes if available so that two
    ChatOpenAI instances pointing at different models produce different
    cache keys (the type name alone is not sufficient).
    """
    if model is None:
        return "None"
    type_name = type(model).__name__
    # LangChain models expose model_name or model_id
    model_id = (
        getattr(model, "model_name", None)
        or getattr(model, "model_id", None)
        or getattr(model, "model", None)
    )
    if model_id:
        return f"{type_name}:{model_id}"
    return type_name


def _generate_cache_key(
    project_path: str | Path,
    model: Any,
    store: Any,
    system_prompt: str | None,
    memory: Sequence[str] | None,
    skills: Sequence[str] | None,
    subagents: Sequence[Any] | None,
    interrupt_on: Mapping[str, Any] | None,
    response_format: str | None,
    tool_token_limit_before_evict: int | None,
    middleware: Sequence[Any] | None,
    tools: Sequence[Any] | None,
    settings: Settings | None,
    scope: dict[str, str] | None = None,
) -> str:
    """Generate a cache key for agent configuration.

    The cache key is a hash of all configuration parameters that affect
    the compiled agent structure. Includes scope so per-user overrides
    each get their own entry.

    Args:
        All parameters passed to create_cognition_agent
        scope: Optional scope dict for multi-tenant cache isolation.

    Returns:
        MD5 hash string representing the configuration
    """
    # Build a string representation of the configuration
    config_parts = [
        str(Path(project_path).resolve()),
        _model_cache_key(model),
        str(store.__class__.__name__) if store else "None",
        str(system_prompt) if system_prompt else "default",
        str(sorted(memory)) if memory else "None",
        str(sorted(skills)) if skills else "None",
        str(len(subagents)) if subagents else "0",
        str(sorted(interrupt_on.keys())) if interrupt_on else "None",
        str(response_format) if response_format else "None",
        str(tool_token_limit_before_evict) if tool_token_limit_before_evict is not None else "None",
        str(len(middleware)) if middleware else "0",
        str(len(tools)) if tools else "0",
        str(settings.sandbox_backend) if settings else "default",
        # Scope isolation: sort keys so dict order doesn't affect hash
        str(sorted((scope or {}).items())),
    ]

    config_str = "|".join(config_parts)
    return hashlib.md5(config_str.encode()).hexdigest()


def get_cached_agent(cache_key: str) -> Any | None:
    """Get a cached agent by cache key.

    Args:
        cache_key: The configuration hash key

    Returns:
        Cached compiled agent or None if not found
    """
    return _agent_cache.get(cache_key)


def cache_agent(cache_key: str, agent: Any) -> None:
    """Cache a compiled agent.

    Args:
        cache_key: The configuration hash key
        agent: The compiled agent to cache
    """
    _agent_cache[cache_key] = agent


def invalidate_agent_cache(cache_key: str) -> None:
    """Invalidate a specific cached agent.

    Args:
        cache_key: The configuration hash key to invalidate
    """
    _agent_cache.pop(cache_key, None)


def invalidate_agent_cache_for_scope(scope: dict[str, str]) -> int:
    """Invalidate all cached agents whose key encodes the given scope.

    Because cache keys are MD5 hashes we cannot reverse them; instead we
    clear *all* cache entries. This is conservative but safe — agents will
    be recompiled on next request. Call this from on_config_change handlers.

    Args:
        scope: Scope that changed (used for logging only).

    Returns:
        Number of cache entries cleared.
    """
    count = len(_agent_cache)
    _agent_cache.clear()
    logger.info("Agent cache cleared on config change", scope=scope, cleared=count)
    return count


def clear_agent_cache() -> None:
    """Clear all cached agents."""
    _agent_cache.clear()


def get_agent_cache_stats() -> dict[str, int]:
    """Get cache statistics.

    Returns:
        Dict with 'size' key indicating number of cached agents
    """
    return {"size": len(_agent_cache)}


SYSTEM_PROMPT = """You are Cognition, an expert AI coding assistant.

Your goal is to help users write, edit, and understand code. You have access to a filesystem and can execute commands.

Key capabilities:
- Read and write files in the workspace
- List directory contents
- Search files using glob patterns and grep
- Execute shell commands (tests, git, etc.)

Best practices:
1. Always check what files exist before making changes
2. Read relevant files before editing
3. Use read_file, write_file, and edit_file for repository changes rather than shell-based text editing
4. The workspace root is the configured session workspace exposed via COGNITION_WORKSPACE_ROOT; use that concrete path instead of hardcoding /workspace or literal shell variables
5. Inspect the real repository layout before assuming nested paths like gateway/src
6. If the target repository is not already present, clone it under the session workspace before inspecting or editing files
7. Use git commands with explicit paths or working directories instead of shell builtins like cd only when you already know the repo root; otherwise inspect the repo first
8. Run tests after making changes
9. Explain your reasoning before taking actions

The current working directory is the project root. All file paths are relative to this root."""


class CognitionAgentResult(NamedTuple):
    """Result of creating a Cognition agent.

    Attributes:
        agent: The compiled LangGraph agent.
        sandbox_backend: The sandbox backend used by the agent, if any.
            Callers should register this with SessionAgentManager for
            lifecycle tracking (terminate on session deletion).
    """

    agent: Any
    sandbox_backend: Any | None = None


async def create_cognition_agent(
    project_path: str | Path,
    model: Any = None,
    store: Any = None,
    checkpointer: Any = None,
    system_prompt: str | None = None,
    memory: Sequence[str] | None = None,
    skills: Sequence[str] | None = None,
    subagents: Sequence[Any] | None = None,
    interrupt_on: Mapping[str, Any] | None = None,
    response_format: str | type[Any] | None = None,
    tool_token_limit_before_evict: int | None = None,
    middleware: Sequence[Any] | None = None,
    tools: Sequence[Any] | None = None,
    settings: Settings | None = None,
    mcp_configs: Sequence[McpServerConfig] | None = None,
    scope: dict[str, str] | None = None,
) -> CognitionAgentResult:
    """Create a Deep Agent for the Cognition system.

    This factory creates an agent with:
    - Settings-driven sandbox backend (local or Docker) for execution
    - FilesystemBackend for file operations (shared across both backends)
    - Multi-step ReAct loop with planning support
    - State checkpointing via thread_id
    - Automatic tool chaining
    - Configurable memory, skills, and subagents
    - Observability and streaming middleware
    - Optional custom tools

    Args:
        project_path: Path to the project workspace directory.
        model: LLM model to use. If None, uses default from ConfigRegistry.
        store: Optional LangGraph store.
        checkpointer: Optional LangGraph checkpoint saver for state persistence.
        system_prompt: Optional custom system prompt. Uses ConfigRegistry default if not provided.
        memory: Optional list of memory files (e.g. AGENTS.md).
        skills: Optional list of skill directory paths.
        subagents: Optional list of subagent definitions.
        interrupt_on: Optional dict mapping tool names to human-approval requirement.
        response_format: Optional dotted path or Pydantic model class for structured output.
        tool_token_limit_before_evict: Optional Deep Agents offload threshold.
        middleware: Optional additional middleware to apply.
        tools: Optional additional tools to register.
        settings: Optional settings override.
        mcp_configs: Optional MCP server configurations.
        scope: Optional scope dict for cache isolation and config resolution.

    Returns:
        Configured Deep Agent ready to handle coding tasks with multi-step support.
    """
    settings = settings or get_settings()
    project_path = Path(project_path).resolve()

    # Sandbox ID and K8s labels are derived from project/scope and needed
    # before the cache check so that cache hits can create a fresh backend.
    sandbox_id = f"cognition-{project_path.name}"

    k8s_labels: dict[str, str] | None = None
    if scope:
        k8s_labels = {}
        if "user" in scope:
            k8s_labels["cognition.io/user"] = scope["user"]
        if "org" in scope:
            k8s_labels["cognition.io/org"] = scope["org"]
        if "project" in scope:
            k8s_labels["cognition.io/project"] = scope["project"]
        k8s_labels["cognition.io/session"] = sandbox_id

    # Try to get config registry once - needed for defaults and skills backend
    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
    except RuntimeError:
        reg = None

    # Generate cache key and check cache
    cache_key = _generate_cache_key(
        project_path=project_path,
        model=model,
        store=store,
        system_prompt=system_prompt,
        memory=memory,
        skills=skills,
        subagents=subagents,
        interrupt_on=interrupt_on,
        response_format=response_format
        if isinstance(response_format, str)
        else getattr(response_format, "__name__", None),
        tool_token_limit_before_evict=tool_token_limit_before_evict,
        middleware=middleware,
        tools=tools,
        settings=settings,
        scope=scope,
    )

    # Check if we have a cached compiled agent for this configuration.
    # Only the compiled agent graph is cached — it is immutable and safe to share.
    # The sandbox backend is stateful and must be created per-session to prevent
    # cross-session termination bugs (see GH-72).
    cached_agent = get_cached_agent(cache_key)
    if cached_agent is not None:
        # Cached agent graph is reusable (immutable), but sandbox backend must be
        # fresh per-session to avoid cross-session termination (GH-72).
        sandbox_backend = create_sandbox_backend(
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

        # Wrap sandbox backend with CompositeBackend for DB-backed skills
        from deepagents.backends.protocol import BackendProtocol

        backend: BackendProtocol
        if reg:
            from deepagents.backends.composite import CompositeBackend

            from server.app.agent.skills_backend import ConfigRegistrySkillsBackend

            db_skills_backend = ConfigRegistrySkillsBackend(registry=reg, scope=scope)
            backend = CompositeBackend(
                default=sandbox_backend,
                routes={"/skills/api/": db_skills_backend},
            )
        else:
            backend = sandbox_backend

        return CognitionAgentResult(agent=cached_agent, sandbox_backend=sandbox_backend)

    # Create the sandbox backend using settings-driven factory

    sandbox_backend = create_sandbox_backend(
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

    # Resolve agent defaults from ConfigRegistry (falls back to hardcoded defaults)
    agent_memory: list[str]
    agent_skills: list[str]
    agent_subagents: list[Any]
    agent_interrupt_on: dict[str, Any]
    agent_response_format: str | type[Any] | None = response_format
    agent_tool_token_limit_before_evict = tool_token_limit_before_evict

    if memory is not None:
        agent_memory = list(memory)
    else:
        if reg:
            defaults = await reg.get_global_agent_defaults(scope)
            agent_memory = defaults.memory
        else:
            agent_memory = ["AGENTS.md"]

    if skills is not None:
        agent_skills = list(skills)
    else:
        if reg:
            defaults = await reg.get_global_agent_defaults(scope)
            agent_skills = defaults.skills
        else:
            agent_skills = [".cognition/skills/"]

    # Always include API skills route for ConfigRegistry-backed skills
    if "/skills/api/" not in agent_skills:
        agent_skills = agent_skills + ["/skills/api/"]

    # Wrap sandbox backend with CompositeBackend to support DB-backed skills.
    # The route is intentionally narrow so normal repo file access under /workspace
    # still flows to the sandbox backend unchanged.
    from deepagents.backends.protocol import BackendProtocol

    backend: BackendProtocol
    if reg:
        from deepagents.backends.composite import CompositeBackend

        from server.app.agent.skills_backend import ConfigRegistrySkillsBackend

        db_skills_backend = ConfigRegistrySkillsBackend(registry=reg, scope=scope)
        backend = CompositeBackend(
            default=sandbox_backend,
            routes={"/skills/api/": db_skills_backend},
        )
    else:
        backend = sandbox_backend

    if subagents is not None:
        raw_subagents = list(subagents)
    else:
        if reg:
            defaults = await reg.get_global_agent_defaults(scope)
            raw_subagents = list(defaults.subagents)
        else:
            raw_subagents = []

    # Normalize subagent specs: ensure required 'description' key is present
    agent_subagents = [
        {**s, "description": s.get("description", "")} if isinstance(s, dict) else s
        for s in raw_subagents
    ]

    if interrupt_on is not None:
        agent_interrupt_on = dict(interrupt_on)
    else:
        if reg:
            defaults = await reg.get_global_agent_defaults(scope)
            agent_interrupt_on = dict(defaults.interrupt_on)
            if agent_response_format is None:
                agent_response_format = defaults.response_format
            if agent_tool_token_limit_before_evict is None:
                agent_tool_token_limit_before_evict = defaults.tool_token_limit_before_evict
        else:
            agent_interrupt_on = {}

    # Resolve system prompt from ConfigRegistry
    if system_prompt:
        prompt = system_prompt
    else:
        if reg:
            prov_defaults = await reg.get_global_provider_defaults(scope)
            prompt_type = prov_defaults.system_prompt_type
            prompt_value = prov_defaults.system_prompt_value
            from server.app.models import PromptConfig

            try:
                prompt = PromptConfig(type=prompt_type, value=prompt_value).get_prompt_text()
            except (FileNotFoundError, RuntimeError):
                prompt = SYSTEM_PROMPT
        else:
            prompt = SYSTEM_PROMPT

    # Initialize middleware stack
    agent_middleware = list(middleware) if middleware else []

    # Get blocked tools from settings
    blocked_tools = list(settings.blocked_tools) if hasattr(settings, "blocked_tools") else []

    # Initialize built-in tools
    built_in_tools = [BrowserTool(), SearchTool(), InspectPackageTool()]
    agent_tools = list(tools) if tools else []
    agent_tools.extend(built_in_tools)

    # Initialize MCP tools if configurations provided
    if mcp_configs:
        mcp_manager = McpManager()
        for config in mcp_configs:
            if config.enabled:
                try:
                    mcp_manager.add_server(config)
                except ValueError as e:
                    logger.warning("Failed to add MCP server", server=config.name, error=str(e))

        try:
            await mcp_manager.connect_all()
            all_mcp_tools = await mcp_manager.get_all_tools()
            for server_name, tool_infos in all_mcp_tools.items():
                mcp_tools = create_mcp_tools(mcp_manager.clients[server_name], tool_infos)
                agent_tools.extend(mcp_tools)
                logger.info("Added MCP tools", server=server_name, count=len(mcp_tools))
        except Exception as e:
            logger.error("Failed to initialize MCP tools", error=str(e))
            # Continue without MCP tools rather than failing entirely

    agent_middleware.extend(
        [
            CognitionObservabilityMiddleware(),
            CognitionStreamingMiddleware(),
            ToolSecurityMiddleware(blocked_tools=blocked_tools),
        ]
    )

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

    # Create the agent with multi-step support
    create_kwargs = cast(
        Any,
        {
            "model": model,
            "tools": agent_tools,
            "system_prompt": prompt,
            "backend": backend,
            "checkpointer": checkpointer,
            "store": store,
            "context_schema": CognitionContext,
            "memory": agent_memory,
            "skills": agent_skills,
            "subagents": cast(Any, agent_subagents),
            "interrupt_on": cast(Any, agent_interrupt_on),
            "response_format": resolved_response_format,
            "middleware": agent_middleware,
        },
    )
    if agent_tool_token_limit_before_evict is not None:
        create_kwargs["tool_token_limit_before_evict"] = agent_tool_token_limit_before_evict

    agent = cast(Any, create_deep_agent)(**create_kwargs)

    result = CognitionAgentResult(agent=agent, sandbox_backend=sandbox_backend)

    # Cache only the compiled agent graph — it is immutable and safe to share.
    # The sandbox backend is NOT cached because it is stateful and must be
    # created fresh per-session (see GH-72).
    cache_agent(cache_key, agent)

    return result


def _resolve_response_format(response_format: str | type[Any] | None) -> type[Any] | None:
    """Resolve a dotted response format path to a model class."""
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
