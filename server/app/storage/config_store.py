"""ConfigStore: unified configuration interface for Cognition.

ConfigStore is the single persistence-facing interface for all hot-reloadable
agent configuration. It combines CRUD persistence with built-in, file-backed,
and DB-backed agent definition resolution.

Layer: 2 (Persistence)

Design:
- ``ConfigStore`` Protocol defines the unified async interface.
- ``DefaultConfigStore`` implements it by delegating to a ``ConfigRegistry``
  for DB CRUD and maintaining its own in-memory agent definition cache for
  built-in + file + DB agent definitions.
- Route handlers and services receive ConfigStore via FastAPI Depends().

The old direct registry globals are replaced by this single
dependency-injected interface.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, cast, runtime_checkable

from server.app.agent.definition import (
    AgentConfig,
    AgentDefinition,
    load_agent_definition,
    load_agent_definition_from_markdown,
)
from server.app.agent.prompts import SYSTEM_PROMPT
from server.app.storage.config_models import (
    ConfigChange,
    ConfigChangeEvent,
    EntityType,
    GlobalAgentDefaults,
    GlobalProviderDefaults,
    McpServerRegistration,
    ProviderConfig,
    SkillDefinition,
    ToolRegistration,
)

logger = logging.getLogger(__name__)

_default_store: DefaultConfigStore | None = None


def set_default_config_store(store: DefaultConfigStore) -> None:
    """Set the global DefaultConfigStore instance."""
    global _default_store
    _default_store = store


def get_default_config_store() -> DefaultConfigStore | None:
    """Get the global DefaultConfigStore instance, or None if not initialized."""
    return _default_store


@runtime_checkable
class ConfigStore(Protocol):
    """Unified async interface for all hot-reloadable agent configuration.

    ConfigStore is the CQRS "read/write" side optimized for the REST API,
    file watcher, and any caller that needs configuration *data*.

    All read methods perform scope resolution: the most-specific matching row
    wins over a global row.
    """

    # ------------------------------------------------------------------
    # Provider CRUD
    # ------------------------------------------------------------------

    async def get_provider(
        self, provider_id: str, scope: dict[str, str] | None = None
    ) -> ProviderConfig | None: ...

    async def list_providers(self, scope: dict[str, str] | None = None) -> list[ProviderConfig]: ...

    async def upsert_provider(self, config: ProviderConfig) -> None: ...

    async def upsert_provider_from_dict(self, data: dict[str, Any]) -> None: ...

    async def delete_provider(
        self, provider_id: str, scope: dict[str, str] | None = None
    ) -> bool: ...

    # ------------------------------------------------------------------
    # Tool CRUD
    # ------------------------------------------------------------------

    async def get_tool(
        self, name: str, scope: dict[str, str] | None = None
    ) -> ToolRegistration | None: ...

    async def list_tools(self, scope: dict[str, str] | None = None) -> list[ToolRegistration]: ...

    async def upsert_tool(self, tool: ToolRegistration) -> None: ...

    async def upsert_tool_from_dict(self, data: dict[str, Any]) -> None: ...

    async def delete_tool(self, name: str, scope: dict[str, str] | None = None) -> bool: ...

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    async def get_skill(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SkillDefinition | None: ...

    async def list_skills(self, scope: dict[str, str] | None = None) -> list[SkillDefinition]: ...

    async def upsert_skill(self, skill: SkillDefinition) -> None: ...

    async def upsert_skill_from_dict(self, data: dict[str, Any]) -> None: ...

    async def delete_skill(self, name: str, scope: dict[str, str] | None = None) -> bool: ...

    # ------------------------------------------------------------------
    # Agent CRUD
    # ------------------------------------------------------------------

    async def upsert_agent(
        self,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "api",
    ) -> None: ...

    async def get_agent_raw(
        self, name: str, scope: dict[str, str] | None = None
    ) -> dict[str, Any] | None: ...

    async def delete_agent(self, name: str, scope: dict[str, str] | None = None) -> bool: ...

    async def get_agent_definition(
        self, name: str, scope: dict[str, str] | None = None
    ) -> AgentDefinition | None:
        """Resolve an agent definition by name.

        Checks built-in/file agents first, then DB-seeded agents.
        Returns None if not found.
        """
        ...

    async def list_agent_definitions(
        self, include_hidden: bool = False, scope: dict[str, str] | None = None
    ) -> list[AgentDefinition]:
        """List all agent definitions (built-in + file + DB-seeded)."""
        ...

    async def is_valid_primary(self, name: str, scope: dict[str, str] | None = None) -> bool:
        """Check if an agent name is a valid primary agent."""
        ...

    # ------------------------------------------------------------------
    # MCP server CRUD
    # ------------------------------------------------------------------

    async def list_mcp_servers(
        self, scope: dict[str, str] | None = None
    ) -> list[McpServerRegistration]: ...

    async def upsert_mcp_server(self, server: McpServerRegistration) -> None: ...

    async def delete_mcp_server(self, name: str, scope: dict[str, str] | None = None) -> bool: ...

    # ------------------------------------------------------------------
    # Global defaults
    # ------------------------------------------------------------------

    async def get_global_provider_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalProviderDefaults: ...

    async def set_global_provider_defaults(
        self, defaults: GlobalProviderDefaults, scope: dict[str, str] | None = None
    ) -> None: ...

    async def get_global_agent_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalAgentDefaults: ...

    async def set_global_agent_defaults(
        self, defaults: GlobalAgentDefaults, scope: dict[str, str] | None = None
    ) -> None: ...

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    async def seed_if_absent(
        self,
        entity_type: EntityType,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "file",
    ) -> bool: ...

    # ------------------------------------------------------------------
    # Change log
    # ------------------------------------------------------------------

    async def get_changes_since(self, since: datetime) -> list[ConfigChange]: ...

    async def mark_changes_processed(self, change_ids: list[int]) -> None: ...


class DefaultConfigStore:
    """Default ConfigStore implementation.

    Delegates to a ConfigRegistry for DB CRUD and keeps an in-memory cache of
    built-in, file-backed, and DB-backed agent definitions.
    """

    def __init__(
        self,
        config_registry: Any,
        workspace_path: str | Path | None = None,
    ) -> None:
        self._config_registry = config_registry
        self._workspace_path = Path(workspace_path) if workspace_path else Path.cwd()
        self._agent_definitions: dict[str, AgentDefinition] = {}
        self._init_builtin_agents()
        self.reload_file_agents()

    @property
    def config_registry(self) -> Any:
        return self._config_registry

    def _init_builtin_agents(self) -> None:
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
        self._agent_definitions["default"] = default_agent

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
            interrupt_on={"write_file": True, "edit_file": True, "execute": True},
            response_format=None,
            middleware=[],
            config=AgentConfig(),
        )
        self._agent_definitions["readonly"] = readonly_agent

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
            interrupt_on={"write_file": True, "edit_file": True, "execute": True},
            response_format=None,
            middleware=[],
            config=AgentConfig(),
        )
        self._agent_definitions["hitl_test"] = hitl_test_agent

    def reload_file_agents(self) -> None:
        self._agent_definitions = {
            name: agent for name, agent in self._agent_definitions.items() if agent.native
        }
        agents_dir = self._workspace_path / ".cognition" / "agents"
        if not agents_dir.exists():
            return

        for yaml_path in agents_dir.glob("*.yaml"):
            try:
                definition = load_agent_definition(yaml_path)
                definition.native = False
                self._agent_definitions[definition.name] = definition
            except Exception as exc:
                logger.warning("Failed to load agent from YAML %s: %s", yaml_path, exc)

        for yml_path in agents_dir.glob("*.yml"):
            try:
                definition = load_agent_definition(yml_path)
                definition.native = False
                self._agent_definitions[definition.name] = definition
            except Exception as exc:
                logger.warning("Failed to load agent from YAML %s: %s", yml_path, exc)

        for md_path in agents_dir.glob("*.md"):
            try:
                definition = load_agent_definition_from_markdown(md_path)
                self._agent_definitions[definition.name] = definition
            except Exception as exc:
                logger.warning("Failed to load agent from Markdown %s: %s", md_path, exc)

    async def seed_agent_definitions(self, scope: dict[str, str] | None = None) -> None:
        rows = await self._config_registry.list_agents(scope)
        for definition in rows:
            if definition.name.startswith("__"):
                continue
            existing = self._agent_definitions.get(definition.name)
            if existing and existing.native:
                continue
            self._agent_definitions[definition.name] = definition

    async def on_config_change(self, event: ConfigChangeEvent) -> None:
        if event.entity_type != "agent":
            return
        if event.name.startswith("__"):
            return
        if event.operation == "delete":
            existing = self._agent_definitions.get(event.name)
            if existing and not existing.native:
                del self._agent_definitions[event.name]
            return

        data = await self._config_registry.get_agent_raw(event.name, event.scope)
        if not data:
            return
        definition = AgentDefinition.model_validate(data)
        definition.native = False
        existing = self._agent_definitions.get(event.name)
        if existing and existing.native:
            return
        self._agent_definitions[event.name] = definition

    # ------------------------------------------------------------------
    # Provider CRUD
    # ------------------------------------------------------------------

    async def get_provider(
        self, provider_id: str, scope: dict[str, str] | None = None
    ) -> ProviderConfig | None:
        return cast(
            ProviderConfig | None, await self._config_registry.get_provider(provider_id, scope)
        )

    async def list_providers(self, scope: dict[str, str] | None = None) -> list[ProviderConfig]:
        return cast(list[ProviderConfig], await self._config_registry.list_providers(scope))

    async def upsert_provider(self, config: ProviderConfig) -> None:
        await self._config_registry.upsert_provider(config)

    async def upsert_provider_from_dict(self, data: dict[str, Any]) -> None:
        provider = ProviderConfig.model_validate(data)
        await self._config_registry.upsert_provider(provider)

    async def delete_provider(self, provider_id: str, scope: dict[str, str] | None = None) -> bool:
        return bool(await self._config_registry.delete_provider(provider_id, scope))

    # ------------------------------------------------------------------
    # Tool CRUD
    # ------------------------------------------------------------------

    async def get_tool(
        self, name: str, scope: dict[str, str] | None = None
    ) -> ToolRegistration | None:
        return cast(ToolRegistration | None, await self._config_registry.get_tool(name, scope))

    async def list_tools(self, scope: dict[str, str] | None = None) -> list[ToolRegistration]:
        return cast(list[ToolRegistration], await self._config_registry.list_tools(scope))

    async def upsert_tool(self, tool: ToolRegistration) -> None:
        await self._config_registry.upsert_tool(tool)

    async def upsert_tool_from_dict(self, data: dict[str, Any]) -> None:
        tool = ToolRegistration.model_validate(data)
        await self._config_registry.upsert_tool(tool)

    async def delete_tool(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return bool(await self._config_registry.delete_tool(name, scope))

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    async def get_skill(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SkillDefinition | None:
        return cast(SkillDefinition | None, await self._config_registry.get_skill(name, scope))

    async def list_skills(self, scope: dict[str, str] | None = None) -> list[SkillDefinition]:
        return cast(list[SkillDefinition], await self._config_registry.list_skills(scope))

    async def upsert_skill(self, skill: SkillDefinition) -> None:
        await self._config_registry.upsert_skill(skill)

    async def upsert_skill_from_dict(self, data: dict[str, Any]) -> None:
        skill = SkillDefinition.model_validate(data)
        await self._config_registry.upsert_skill(skill)

    async def delete_skill(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return bool(await self._config_registry.delete_skill(name, scope))

    # ------------------------------------------------------------------
    # Agent CRUD (raw dict for DB)
    # ------------------------------------------------------------------

    async def upsert_agent(
        self,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "api",
    ) -> None:
        await self._config_registry.upsert_agent(name, scope, definition, source)
        try:
            agent_def = AgentDefinition.model_validate(definition)
            agent_def.native = False
            existing = self._agent_definitions.get(name)
            if existing and existing.native:
                return
            self._agent_definitions[name] = agent_def
        except Exception as e:
            logger.warning(f"Failed to update in-memory agent definition after upsert: {e}")

    async def get_agent_raw(
        self, name: str, scope: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return cast(dict[str, Any] | None, await self._config_registry.get_agent_raw(name, scope))

    async def delete_agent(self, name: str, scope: dict[str, str] | None = None) -> bool:
        result = bool(await self._config_registry.delete_agent(name, scope))
        if result:
            existing = self._agent_definitions.get(name)
            if existing and not existing.native:
                del self._agent_definitions[name]
        return result

    # ------------------------------------------------------------------
    # Agent definition resolution (unified)
    # ------------------------------------------------------------------

    async def get_agent_definition(
        self, name: str, scope: dict[str, str] | None = None
    ) -> AgentDefinition | None:
        return self._agent_definitions.get(name)

    async def list_agent_definitions(
        self, include_hidden: bool = False, scope: dict[str, str] | None = None
    ) -> list[AgentDefinition]:
        agents = list(self._agent_definitions.values())
        if not include_hidden:
            agents = [agent for agent in agents if not agent.hidden]
        return sorted(agents, key=lambda agent: agent.name)

    async def is_valid_primary(self, name: str, scope: dict[str, str] | None = None) -> bool:
        agent = self._agent_definitions.get(name)
        if agent is None or agent.hidden:
            return False
        return agent.mode in ("primary", "all")

    # ------------------------------------------------------------------
    # MCP server CRUD
    # ------------------------------------------------------------------

    async def list_mcp_servers(
        self, scope: dict[str, str] | None = None
    ) -> list[McpServerRegistration]:
        return cast(
            list[McpServerRegistration], await self._config_registry.list_mcp_servers(scope)
        )

    async def upsert_mcp_server(self, server: McpServerRegistration) -> None:
        await self._config_registry.upsert_mcp_server(server)

    async def delete_mcp_server(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return bool(await self._config_registry.delete_mcp_server(name, scope))

    # ------------------------------------------------------------------
    # Global defaults
    # ------------------------------------------------------------------

    async def get_global_provider_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalProviderDefaults:
        return cast(
            GlobalProviderDefaults,
            await self._config_registry.get_global_provider_defaults(scope),
        )

    async def set_global_provider_defaults(
        self, defaults: GlobalProviderDefaults, scope: dict[str, str] | None = None
    ) -> None:
        await self._config_registry.set_global_provider_defaults(defaults, scope)

    async def get_global_agent_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalAgentDefaults:
        return cast(
            GlobalAgentDefaults, await self._config_registry.get_global_agent_defaults(scope)
        )

    async def set_global_agent_defaults(
        self, defaults: GlobalAgentDefaults, scope: dict[str, str] | None = None
    ) -> None:
        await self._config_registry.set_global_agent_defaults(defaults, scope)

    # ------------------------------------------------------------------
    # Seeding
    # ------------------------------------------------------------------

    async def seed_if_absent(
        self,
        entity_type: EntityType,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "file",
    ) -> bool:
        return bool(
            await self._config_registry.seed_if_absent(entity_type, name, scope, definition, source)
        )

    # ------------------------------------------------------------------
    # Change log
    # ------------------------------------------------------------------

    async def get_changes_since(self, since: datetime) -> list[ConfigChange]:
        return cast(list[ConfigChange], await self._config_registry.get_changes_since(since))

    async def mark_changes_processed(self, change_ids: list[int]) -> None:
        await self._config_registry.mark_changes_processed(change_ids)


__all__ = [
    "ConfigStore",
    "DefaultConfigStore",
    "set_default_config_store",
    "get_default_config_store",
]
