"""Agent factory for creating Deep Agents."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from deepagents import create_deep_agent
from langgraph.store.base import BaseStore

from server.app.sandbox import LocalSandbox


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

The current working directory is the project root. All file paths are relative to this root."""


def create_cognition_agent(
    project_path: str | Path,
    model: Any = None,
    store: BaseStore | None = None,
) -> Any:
    """Create a Deep Agent for the Cognition system.

    This factory creates an agent with:
    - LocalSandbox for filesystem access and command execution
    - CompositeBackend routing workspace to LocalSandbox and memories to Store

    Args:
        project_path: Path to the project workspace directory.
        model: LLM model to use. If None, uses default from settings.
        store: Optional store for persistent memories across sessions.

    Returns:
        Configured Deep Agent ready to handle coding tasks.
    """
    from deepagents.backends import CompositeBackend, StoreBackend

    project_path = Path(project_path).resolve()

    # Create composite backend:
    # - Default (workspace): LocalSandbox for files and commands
    # - /memories/: StoreBackend for persistent cross-session knowledge
    def backend_factory(runtime: Any) -> CompositeBackend:
        routes = {}
        if store:
            routes["/memories/"] = StoreBackend(runtime)

        return CompositeBackend(
            default=LocalSandbox(root_dir=project_path),
            routes=routes,
        )

    # Create the agent
    agent = create_deep_agent(
        model=model,
        system_prompt=SYSTEM_PROMPT,
        backend=backend_factory,
        store=store,
    )

    return agent
