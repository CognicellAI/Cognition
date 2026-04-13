"""Agent Definition Registry for P3 Multi-Agent Registry.

This module implements the AgentDefinitionRegistry class that manages both
built-in agents (hardcoded) and user-defined agents (loaded from .cognition/agents/).

The registry supports:
- Built-in agents: default, readonly (shipped with the server)
- User-defined agents: YAML files (.cognition/agents/*.yaml) or Markdown files (.cognition/agents/*.md)
- ConfigStore-seeded agents: loaded from the DB via seed_from_store()

Agents can be in three modes:
- primary: user-selectable at session creation
- subagent: only available for delegation via task tool
- all: both primary and subagent
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from server.app.agent.cognition_agent import SYSTEM_PROMPT
from server.app.agent.definition import (
    AgentConfig,
    AgentDefinition,
    load_agent_definition,
    load_agent_definition_from_markdown,
)

if TYPE_CHECKING:
    from server.app.storage.config_models import ConfigChangeEvent

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
            response_format=None,
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
            response_format=None,
            middleware=[],
            config=AgentConfig(),
        )
        self._agents["readonly"] = readonly_agent

        hitl_test_agent = AgentDefinition(
            name="hitl_test",
            system_prompt=SYSTEM_PROMPT
            + """

HITL TESTING MODE: You should actively use tools when a task requires changing files or executing commands.
If the user asks you to create, edit, or execute something, do not refuse or describe the steps first.
Attempt the exact protected tool call immediately so that human-in-the-loop approval can be exercised.
""",
            description="Manual HITL verification agent. Attempts protected tool calls immediately so interrupt_on can be tested.",
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
            response_format=None,
            middleware=[],
            config=AgentConfig(),
        )
        self._agents["hitl_test"] = hitl_test_agent

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

    async def seed_from_store(self, config_store: Any, scope: dict[str, str] | None = None) -> None:
        """Load non-native agent definitions from the ConfigStore.

        Called during startup (after file-based agents are loaded) and on
        config change events. Built-in (native=True) agents are never
        overwritten by registry rows.

        Args:
            config_store: The ConfigStore instance to query.
            scope: Optional scope for config resolution (default: global).
        """
        try:
            rows = [
                agent.model_dump(mode="json")
                for agent in await config_store.list_agent_definitions(
                    include_hidden=True,
                    scope=scope,
                )
                if not agent.native
            ]
        except Exception as e:
            logger.warning(f"Failed to seed agents from ConfigStore: {e}")
            return

        loaded = 0
        for data in rows:
            name = data.get("name")
            if not name:
                continue
            # Skip the global defaults sentinel
            if name.startswith("__"):
                continue
            # Never overwrite built-in agents from DB
            existing = self._agents.get(name)
            if existing and existing.native:
                continue
            try:
                definition = AgentDefinition.model_validate(data)
                definition.native = False
                self._agents[name] = definition
                loaded += 1
            except Exception as e:
                logger.warning(f"Failed to load agent '{name}' from registry: {e}")

        logger.info(f"Seeded {loaded} agents from ConfigStore")

    async def on_config_change(self, event: ConfigChangeEvent) -> None:
        """Invalidate cached agent definitions when config changes.

        Subscribes to the ConfigChangeDispatcher. When an "agent" entity
        changes, the registry reloads from the ConfigStore.

        Args:
            event: The config change event describing what changed.
        """
        if event.entity_type != "agent":
            return

        name = event.name
        if name.startswith("__"):
            return

        if event.operation == "delete":
            self.remove(name)
            logger.info(f"Removed agent '{name}' from registry (config delete)")
        else:
            try:
                from server.app.storage.config_store import get_default_config_store

                store = get_default_config_store()
                if store is None:
                    return
                data = await store.get_agent_raw(name, event.scope)
                if data:
                    definition = AgentDefinition.model_validate(data)
                    definition.native = False
                    try:
                        self.put(name, definition)
                        logger.info(f"Reloaded agent '{name}' from ConfigStore")
                    except ValueError:
                        pass
            except Exception as e:
                logger.warning(f"Failed to reload agent '{name}' on config change: {e}")

    def get_all(self, include_hidden: bool = False) -> list[AgentDefinition]:
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

    def put(self, name: str, definition: AgentDefinition) -> None:
        """Register or replace an agent definition.

        Built-in (native=True) agents cannot be overwritten.

        Args:
            name: The agent name.
            definition: The agent definition to register.

        Raises:
            ValueError: If a native agent with this name already exists.
        """
        existing = self._agents.get(name)
        if existing and existing.native:
            raise ValueError(f"Cannot overwrite built-in agent '{name}'")
        self._agents[name] = definition

    def remove(self, name: str) -> bool:
        """Remove a non-native agent definition.

        Built-in (native=True) agents cannot be removed.

        Args:
            name: The agent name to remove.

        Returns:
            True if the agent was removed, False if not found or native.
        """
        existing = self._agents.get(name)
        if existing is None:
            return False
        if existing.native:
            return False
        del self._agents[name]
        return True

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
