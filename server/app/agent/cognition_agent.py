"""Agent factory for creating Deep Agents with SandboxBackend support.

This module creates deep agents using the CognitionSandboxBackend, which provides:
- Isolated command execution via LocalSandbox
- File operations (ls, read, write, edit)
- Search operations (glob, grep)
- Multi-step ReAct loop with automatic tool chaining
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent

from server.app.agent.sandbox_backend import CognitionSandboxBackend


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
    store: Any = None,  # LangGraph store, unused with sandbox backend
    system_prompt: str | None = None,
) -> Any:
    """Create a Deep Agent for the Cognition system.

    This factory creates an agent with:
    - CognitionSandboxBackend for filesystem and command execution
    - Multi-step ReAct loop with write_todos support
    - State checkpointing via thread_id
    - Automatic tool chaining

    Args:
        project_path: Path to the project workspace directory.
        model: LLM model to use. If None, uses default from settings.
        store: Optional LangGraph store (currently unused with sandbox backend).
        system_prompt: Optional custom system prompt. Uses default if not provided.

    Returns:
        Configured Deep Agent ready to handle coding tasks with multi-step support.
    """
    project_path = Path(project_path).resolve()

    # Create the sandbox backend with command execution support
    # This backend provides the `execute` tool to the agent
    sandbox_id = f"cognition-{project_path.name}"
    backend = CognitionSandboxBackend(
        root_dir=project_path,
        sandbox_id=sandbox_id,
    )

    # Use provided system prompt or default
    prompt = system_prompt if system_prompt else SYSTEM_PROMPT

    # Create the agent with multi-step support
    # The backend provides:
    # - File operations: ls, read, write, edit, glob, grep
    # - Command execution: execute tool for shell commands
    # - Automatic ReAct loop via deepagents
    agent = create_deep_agent(
        model=model,
        system_prompt=prompt,
        backend=backend,
    )

    return agent
