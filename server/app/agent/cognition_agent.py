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
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, cast

from deepagents import create_deep_agent

from server.app.agent.middleware import (
    CognitionObservabilityMiddleware,
    CognitionStreamingMiddleware,
)
from server.app.agent.sandbox_backend import create_sandbox_backend
from server.app.agent.context import ContextManager
from server.app.settings import Settings, get_settings


# Global agent cache: cache_key -> compiled_agent
_agent_cache: dict[str, Any] = {}


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
) -> str:
    """Generate a cache key for agent configuration.

    The cache key is a hash of all configuration parameters that affect
    the compiled agent structure.

    Args:
        All parameters passed to create_cognition_agent

    Returns:
        MD5 hash string representing the configuration
    """
    # Build a string representation of the configuration
    config_parts = [
        str(Path(project_path).resolve()),
        str(type(model).__name__) if model else "None",
        str(store.__class__.__name__) if store else "None",
        str(system_prompt) if system_prompt else "default",
        str(sorted(memory)) if memory else "None",
        str(sorted(skills)) if skills else "None",
        str(len(subagents)) if subagents else "0",
        str(sorted(interrupt_on.keys())) if interrupt_on else "None",
        str(len(middleware)) if middleware else "0",
        str(len(tools)) if tools else "0",
        str(settings.llm_provider) if settings else "default",
        str(settings.llm_model) if settings else "default",
        str(settings.sandbox_backend) if settings else "default",
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
- Break down complex tasks using todos

Best practices:
1. Always check what files exist before making changes
2. Read relevant files before editing
3. Use edit_file for precise changes rather than rewriting entire files
4. Run tests after making changes
5. Explain your reasoning before taking actions

For complex tasks (refactoring, implementing features, debugging):
1. First call write_todos to break down the task into steps
2. Execute each step systematically
3. Mark completion when all todos are done

For simple tasks (single file edits, quick checks), execute directly.

The current working directory is the project root. All file paths are relative to this root."""


def create_cognition_agent(
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
) -> Any:
    """Create a Deep Agent for the Cognition system.

    This factory creates an agent with:
    - Settings-driven sandbox backend (local or Docker) for execution
    - FilesystemBackend for file operations (shared across both backends)
    - Multi-step ReAct loop with write_todos support
    - State checkpointing via thread_id
    - Automatic tool chaining
    - Configurable memory, skills, and subagents
    - Observability and streaming middleware
    - Optional custom tools

    Args:
        project_path: Path to the project workspace directory.
        model: LLM model to use. If None, uses default from settings.
        store: Optional LangGraph store.
        checkpointer: Optional LangGraph checkpoint saver for state persistence.
        system_prompt: Optional custom system prompt. Uses default if not provided.
        memory: Optional list of memory files (e.g. AGENTS.md).
        skills: Optional list of skill directory paths.
        subagents: Optional list of subagent definitions.
        interrupt_on: Optional dict mapping tool names to human-approval requirement.
        middleware: Optional additional middleware to apply.
        tools: Optional additional tools to register.
        settings: Optional settings override.

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
    )

    # Check if we have a cached agent for this configuration
    cached_agent = get_cached_agent(cache_key)
    if cached_agent is not None:
        return cached_agent

    # Create the sandbox backend using settings-driven factory
    sandbox_id = f"cognition-{project_path.name}"
    backend = create_sandbox_backend(
        root_dir=project_path,
        sandbox_id=sandbox_id,
        sandbox_backend=settings.sandbox_backend,
        docker_image=settings.docker_image,
        docker_network=settings.docker_network,
        docker_memory_limit=settings.docker_memory_limit,
        docker_cpu_limit=settings.docker_cpu_limit,
        docker_host_workspace=settings.docker_host_workspace,
    )

    # Initialize context manager (P2-7)
    # This automatically indexes the project and identifies relevant files
    # Note: Using backend.backend to access the underlying ExecutionBackendAdapter's wrapped backend
    # This assumes backend is an ExecutionBackendAdapter which wraps a LocalExecutionBackend
    # Ideally ContextManager should work with the adapter or protocol directly
    try:
        # Access the raw LocalExecutionBackend if possible, or use the adapter
        raw_backend = getattr(backend, "backend", backend)
        context_manager = ContextManager(raw_backend)
        context_manager.index_project()

        # Get relevant context for system prompt
        context_info = context_manager.get_context_summary()

        # Base system prompt with context
        if context_info:
            SYSTEM_PROMPT_WITH_CONTEXT = SYSTEM_PROMPT + f"\n\nProject Context:\n{context_info}"
        else:
            SYSTEM_PROMPT_WITH_CONTEXT = SYSTEM_PROMPT
    except Exception:
        # Fallback if context manager fails
        SYSTEM_PROMPT_WITH_CONTEXT = SYSTEM_PROMPT

    # Use provided values or defaults from settings
    prompt = (
        system_prompt
        if system_prompt
        else (settings.llm_system_prompt or SYSTEM_PROMPT_WITH_CONTEXT)
    )
    agent_memory = list(memory) if memory is not None else settings.agent_memory
    agent_skills = list(skills) if skills is not None else settings.agent_skills
    agent_subagents = list(subagents) if subagents is not None else settings.agent_subagents
    agent_interrupt_on = (
        dict(interrupt_on) if interrupt_on is not None else settings.agent_interrupt_on
    )

    # Initialize middleware stack
    agent_middleware = list(middleware) if middleware else []
    agent_middleware.extend(
        [
            CognitionObservabilityMiddleware(),
            CognitionStreamingMiddleware(),
        ]
    )

    # Create the agent with multi-step support
    agent = create_deep_agent(
        model=model,
        tools=tools,
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
