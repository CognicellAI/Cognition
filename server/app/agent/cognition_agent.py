"""Agent factory for creating Deep Agents with SandboxBackend support.

This module creates deep agents using the CognitionLocalSandboxBackend, which provides:
- Isolated command execution via LocalSandbox
- File operations (ls, read, write, edit) via FilesystemBackend
- Search operations (glob, grep)
- Multi-step ReAct loop with automatic tool chaining
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional, Sequence, cast

from deepagents import create_deep_agent

from server.app.agent.middleware import (
    CognitionObservabilityMiddleware,
    CognitionStreamingMiddleware,
)
from server.app.agent.sandbox_backend import CognitionLocalSandboxBackend
from server.app.settings import Settings, get_settings


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
    - CognitionLocalSandboxBackend for filesystem and command execution
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

    # Create the sandbox backend
    sandbox_id = f"cognition-{project_path.name}"
    backend = CognitionLocalSandboxBackend(
        root_dir=project_path,
        sandbox_id=sandbox_id,
    )

    # Use provided values or defaults from settings
    prompt = system_prompt if system_prompt else (settings.llm_system_prompt or SYSTEM_PROMPT)
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

    return agent
