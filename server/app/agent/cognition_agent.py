"""Agent factory for creating Deep Agents with SandboxBackend support.

This module creates deep agents using settings-driven sandbox backends:
- Local: CognitionLocalSandboxBackend (development) — shell execution via LocalSandbox
- Docker: CognitionDockerSandboxBackend (production) — isolated container execution

Both backends provide:
- File operations (ls, read, write, edit) via FilesystemBackend
- Search operations (glob, grep)
- Multi-step ReAct loop with automatic tool chaining

Agent Caching:
Compiled agents are cached per configuration to avoid recompilation overhead.
Use invalidate_agent_cache() or clear_agent_cache() to force recompilation.
Cache keys are scope-aware so per-user/per-project config overrides each get
their own compiled graph.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, cast

import structlog
from deepagents import create_deep_agent

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

# Global agent cache: cache_key -> compiled_agent
_agent_cache: dict[str, Any] = {}


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
3. Use edit_file for precise changes rather than rewriting entire files
4. Run tests after making changes
5. Explain your reasoning before taking actions

The current working directory is the project root. All file paths are relative to this root."""


async def create_cognition_agent(
    project_path: str | Path,
    model: Any = None,
    store: Any = None,  # LangGraph store
    checkpointer: Any = None,  # LangGraph checkpointer
    system_prompt: str | None = None,
    memory: Sequence[str] | None = None,
    skills: Sequence[str] | None = None,
    subagents: Sequence[Any] | None = None,
    interrupt_on: Mapping[str, Any] | None = None,
    middleware: Sequence[Any] | None = None,
    tools: Sequence[Any] | None = None,
    settings: Settings | None = None,
    mcp_configs: Sequence[McpServerConfig] | None = None,
    scope: dict[str, str] | None = None,
) -> Any:
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
        middleware=middleware,
        tools=tools,
        settings=settings,
        scope=scope,
    )

    # Check if we have a cached agent for this configuration
    cached_agent = get_cached_agent(cache_key)
    if cached_agent is not None:
        return cached_agent

    # Create the sandbox backend using settings-driven factory
    sandbox_id = f"cognition-{project_path.name}"
    sandbox_backend = create_sandbox_backend(
        root_dir=project_path,
        sandbox_id=sandbox_id,
        sandbox_backend=settings.sandbox_backend,
        docker_image=settings.docker_image,
        docker_network=settings.docker_network,
        docker_memory_limit=settings.docker_memory_limit,
        docker_cpu_limit=settings.docker_cpu_limit,
        docker_host_workspace="",  # docker_host_workspace removed from Settings (niche Docker-in-Docker only)
    )

    # Resolve agent defaults from ConfigRegistry (falls back to hardcoded defaults)
    agent_memory: list[str]
    agent_skills: list[str]
    agent_subagents: list[Any]
    agent_interrupt_on: dict[str, Any]

    # Try to get config registry once - needed for defaults and skills backend
    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
    except RuntimeError:
        reg = None

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
    # Typed as the common BackendProtocol so both branches satisfy the union.
    from deepagents.backends.protocol import BackendProtocol

    backend: BackendProtocol
    if reg:
        from deepagents.backends.composite import CompositeBackend

        from server.app.agent.skills_backend import ConfigRegistrySkillsBackend

        # Create DB-backed skills backend for API-created skills
        db_skills_backend = ConfigRegistrySkillsBackend(registry=reg, scope=scope)

        # CompositeBackend routes skill paths to DB backend, everything else to sandbox
        backend = CompositeBackend(
            default=sandbox_backend,  # Filesystem for tools, execute(), etc.
            routes={"/skills/api/": db_skills_backend},  # DB for API-created skills
        )
    else:
        # Fallback to sandbox backend only if ConfigRegistry not available
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

    # Create the agent with multi-step support
    agent = create_deep_agent(
        model=model,
        tools=agent_tools,
        system_prompt=prompt,
        backend=backend,
        checkpointer=checkpointer,
        memory=agent_memory,
        skills=agent_skills,
        subagents=cast(Any, agent_subagents),
        interrupt_on=cast(Any, agent_interrupt_on),
        middleware=agent_middleware,
    )

    # Cache the compiled agent
    cache_agent(cache_key, agent)

    return agent
