"""Agent Definition Registry for P3 Multi-Agent Registry.

This module implements the AgentDefinitionRegistry class that manages both
built-in agents (hardcoded) and user-defined agents (loaded from .cognition/agents/).

The registry supports:
- Built-in agents: default, readonly (shipped with the server)
- User-defined agents: YAML files (.cognition/agents/*.yaml) or Markdown files (.cognition/agents/*.md)

Agents can be in three modes:
- primary: user-selectable at session creation
- subagent: only available for delegation via task tool
- all: both primary and subagent
"""

from __future__ import annotations

import logging
from pathlib import Path

from server.app.agent.cognition_agent import SYSTEM_PROMPT
from server.app.agent.definition import (
    AgentConfig,
    AgentDefinition,
    load_agent_definition,
    load_agent_definition_from_markdown,
)

logger = logging.getLogger(__name__)

# Global registry instance
_registry: AgentDefinitionRegistry | None = None


class AgentDefinitionRegistry:
    """Registry of agent definitions combining built-in and user-defined agents.

    Built-in agents are hardcoded with native=True.
    User-defined agents are loaded from .cognition/agents/ directory.
    User agents override built-in agents when names collide.

    Attributes:
        _agents: Dictionary mapping agent names to AgentDefinition instances.
        _workspace_path: Path to the workspace directory for loading user agents.
    """

    def __init__(self, workspace_path: str | Path | None = None) -> None:
        """Initialize the registry with built-in agents.

        Args:
            workspace_path: Optional path to workspace for loading user agents.
        """
        self._agents: dict[str, AgentDefinition] = {}
        self._workspace_path = Path(workspace_path) if workspace_path else Path.cwd()

        # Initialize with built-in agents
        self._init_builtin_agents()

        # Load user-defined agents from .cognition/agents/
        self._load_user_agents()

    def _init_builtin_agents(self) -> None:
        """Initialize built-in agents that ship with the server.

        Built-in agents:
        - default: Full-access coding agent (current SYSTEM_PROMPT)
        - readonly: Analysis-only agent (write/edit/execute disabled)
        """
        # Built-in "default" agent - full access
        default_agent = AgentDefinition(
            name="default",
            system_prompt=SYSTEM_PROMPT,
            description="Full-access coding assistant. Can read, write, edit files and execute commands.",
            mode="primary",
            hidden=False,
            native=True,
            tools=[],
            skills=[".cognition/skills/"],
            memory=["AGENTS.md"],
            subagents=[],
            interrupt_on={},
            middleware=[],
            config=AgentConfig(),
        )
        self._agents["default"] = default_agent

        # Built-in "readonly" agent - analysis only
        readonly_agent = AgentDefinition(
            name="readonly",
            system_prompt=SYSTEM_PROMPT
            + """

RESTRICTION: You are in READ-ONLY mode. You cannot write files, edit files, or execute commands.
You can only read files, search, and provide analysis.""",
            description="Analysis-only agent. Can read files and search but cannot write or execute.",
            mode="primary",
            hidden=False,
            native=True,
            tools=[],
            skills=[".cognition/skills/"],
            memory=["AGENTS.md"],
            subagents=[],
            interrupt_on={
                "write_file": True,
                "edit_file": True,
                "execute": True,
            },
            middleware=[],
            config=AgentConfig(),
        )
        self._agents["readonly"] = readonly_agent

        logger.debug(f"Initialized {len(self._agents)} built-in agents")

    def _load_user_agents(self) -> None:
        """Load user-defined agents from .cognition/agents/ directory.

        Supports:
        - YAML files (*.yaml, *.yml)
        - Markdown files (*.md) with YAML frontmatter

        User agents override built-in agents when names collide.
        """
        agents_dir = self._workspace_path / ".cognition" / "agents"

        if not agents_dir.exists():
            logger.debug(f"No .cognition/agents/ directory found at {agents_dir}")
            return

        loaded_count = 0

        # Load YAML files
        for yaml_path in agents_dir.glob("*.yaml"):
            try:
                definition = load_agent_definition(yaml_path)
                definition.native = False  # Mark as user-defined
                self._agents[definition.name] = definition
                loaded_count += 1
                logger.debug(f"Loaded user agent from YAML: {definition.name}")
            except Exception as e:
                logger.warning(f"Failed to load agent from {yaml_path}: {e}")

        for yml_path in agents_dir.glob("*.yml"):
            try:
                definition = load_agent_definition(yml_path)
                definition.native = False  # Mark as user-defined
                self._agents[definition.name] = definition
                loaded_count += 1
                logger.debug(f"Loaded user agent from YAML: {definition.name}")
            except Exception as e:
                logger.warning(f"Failed to load agent from {yml_path}: {e}")

        # Load Markdown files
        for md_path in agents_dir.glob("*.md"):
            try:
                definition = load_agent_definition_from_markdown(md_path)
                # native is already False from the loader
                self._agents[definition.name] = definition
                loaded_count += 1
                logger.debug(f"Loaded user agent from Markdown: {definition.name}")
            except Exception as e:
                logger.warning(f"Failed to load agent from {md_path}: {e}")

        logger.info(f"Loaded {loaded_count} user-defined agents from {agents_dir}")

    def list(self, include_hidden: bool = False) -> list[AgentDefinition]:
        """List all registered agents.

        Args:
            include_hidden: If True, include hidden agents in the list.

        Returns:
            List of AgentDefinition instances, sorted by name.
        """
        agents = list(self._agents.values())
        if not include_hidden:
            agents = [a for a in agents if not a.hidden]
        return sorted(agents, key=lambda a: a.name)

    def get(self, name: str) -> AgentDefinition | None:
        """Get an agent definition by name.

        Args:
            name: The agent name to look up.

        Returns:
            AgentDefinition if found, None otherwise.
        """
        return self._agents.get(name)

    def reload(self) -> None:
        """Reload the registry.

        Re-initializes built-in agents and reloads user-defined agents from disk.
        This is useful when files change and you want to pick up new definitions.
        """
        self._agents.clear()
        self._init_builtin_agents()
        self._load_user_agents()
        logger.info("Agent registry reloaded")

    def subagents(self) -> list[AgentDefinition]:
        """Get all agents that can act as subagents.

        Returns:
            List of agents with mode in ("subagent", "all"), sorted by name.
        """
        return sorted(
            [a for a in self._agents.values() if a.mode in ("subagent", "all")],
            key=lambda a: a.name,
        )

    def primaries(self) -> list[AgentDefinition]:
        """Get all agents that can act as primary agents.

        Returns:
            List of agents with mode in ("primary", "all"), sorted by name.
        """
        return sorted(
            [a for a in self._agents.values() if a.mode in ("primary", "all")], key=lambda a: a.name
        )

    def is_valid_primary(self, name: str) -> bool:
        """Check if an agent name is a valid primary agent.

        Args:
            name: The agent name to check.

        Returns:
            True if the agent exists, is not hidden, and has mode primary or all.
        """
        agent = self.get(name)
        if agent is None:
            return False
        if agent.hidden:
            return False
        return agent.mode in ("primary", "all")


def get_agent_definition_registry() -> AgentDefinitionRegistry | None:
    """Get the global agent definition registry instance.

    Returns:
        The global registry instance, or None if not initialized.
    """
    return _registry


def set_agent_definition_registry(registry: AgentDefinitionRegistry) -> None:
    """Set the global agent definition registry instance.

    Args:
        registry: The registry instance to set as global.
    """
    global _registry
    _registry = registry


def initialize_agent_definition_registry(
    workspace_path: str | Path | None = None,
) -> AgentDefinitionRegistry:
    """Initialize and return the global agent definition registry.

    This should be called once during application startup.

    Args:
        workspace_path: Path to the workspace directory for loading user agents.

    Returns:
        The initialized registry instance.
    """
    global _registry
    _registry = AgentDefinitionRegistry(workspace_path)
    return _registry
