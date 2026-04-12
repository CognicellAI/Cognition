"""ConfigStore: unified configuration interface for Cognition.

ConfigStore is the single persistence-facing interface for all hot-reloadable
agent configuration. It combines the CRUD capabilities of ConfigRegistry with
the agent definition management of AgentDefinitionRegistry into one Protocol.

Layer: 2 (Persistence)

Design:
- ``ConfigStore`` Protocol defines the unified async interface.
- ``DefaultConfigStore`` implements it by delegating to a ``ConfigRegistry``
  (for DB CRUD) and an ``AgentDefinitionRegistry`` (for built-in + file + DB
  agent definitions).
- Route handlers and services receive ConfigStore via FastAPI Depends().

The old ``get_config_registry()`` / ``get_agent_definition_registry()`` globals
are replaced by this single dependency-injected interface.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from server.app.agent.definition import AgentDefinition
from server.app.storage.config_models import (
    ConfigChange,
    EntityType,
    GlobalAgentDefaults,
    GlobalProviderDefaults,
    McpServerRegistration,
    ProviderConfig,
    SkillDefinition,
    ToolRegistration,
)

logger = logging.getLogger(__name__)


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

    async def delete_tool(self, name: str, scope: dict[str, str] | None = None) -> bool: ...

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    async def get_skill(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SkillDefinition | None: ...

    async def list_skills(self, scope: dict[str, str] | None = None) -> list[SkillDefinition]: ...

    async def upsert_skill(self, skill: SkillDefinition) -> None: ...

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

    Delegates to a ConfigRegistry for DB CRUD and an
    AgentDefinitionRegistry for agent definition resolution.
    """

    def __init__(
        self,
        config_registry: Any,
        agent_definition_registry: Any | None = None,
    ) -> None:
        self._config_registry = config_registry
        self._agent_definition_registry = agent_definition_registry

    @property
    def config_registry(self) -> Any:
        return self._config_registry

    @property
    def agent_definition_registry(self) -> Any | None:
        return self._agent_definition_registry

    # ------------------------------------------------------------------
    # Provider CRUD
    # ------------------------------------------------------------------

    async def get_provider(
        self, provider_id: str, scope: dict[str, str] | None = None
    ) -> ProviderConfig | None:
        return await self._config_registry.get_provider(provider_id, scope)

    async def list_providers(self, scope: dict[str, str] | None = None) -> list[ProviderConfig]:
        return await self._config_registry.list_providers(scope)

    async def upsert_provider(self, config: ProviderConfig) -> None:
        await self._config_registry.upsert_provider(config)

    async def delete_provider(self, provider_id: str, scope: dict[str, str] | None = None) -> bool:
        return await self._config_registry.delete_provider(provider_id, scope)

    # ------------------------------------------------------------------
    # Tool CRUD
    # ------------------------------------------------------------------

    async def get_tool(
        self, name: str, scope: dict[str, str] | None = None
    ) -> ToolRegistration | None:
        return await self._config_registry.get_tool(name, scope)

    async def list_tools(self, scope: dict[str, str] | None = None) -> list[ToolRegistration]:
        return await self._config_registry.list_tools(scope)

    async def upsert_tool(self, tool: ToolRegistration) -> None:
        await self._config_registry.upsert_tool(tool)

    async def delete_tool(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._config_registry.delete_tool(name, scope)

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    async def get_skill(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SkillDefinition | None:
        return await self._config_registry.get_skill(name, scope)

    async def list_skills(self, scope: dict[str, str] | None = None) -> list[SkillDefinition]:
        return await self._config_registry.list_skills(scope)

    async def upsert_skill(self, skill: SkillDefinition) -> None:
        await self._config_registry.upsert_skill(skill)

    async def delete_skill(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._config_registry.delete_skill(name, scope)

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
        if self._agent_definition_registry:
            try:
                agent_def = AgentDefinition.model_validate(definition)
                agent_def.native = False
                existing = self._agent_definition_registry.get(name)
                if existing and existing.native:
                    return
                self._agent_definition_registry._agents[name] = agent_def
            except Exception as e:
                logger.warning(f"Failed to update in-memory agent definition after upsert: {e}")

    async def get_agent_raw(
        self, name: str, scope: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        return await self._config_registry.get_agent_raw(name, scope)

    async def delete_agent(self, name: str, scope: dict[str, str] | None = None) -> bool:
        result = await self._config_registry.delete_agent(name, scope)
        if result and self._agent_definition_registry:
            existing = self._agent_definition_registry.get(name)
            if existing and not existing.native:
                self._agent_definition_registry._agents.pop(name, None)
        return result

    # ------------------------------------------------------------------
    # Agent definition resolution (unified)
    # ------------------------------------------------------------------

    async def get_agent_definition(
        self, name: str, scope: dict[str, str] | None = None
    ) -> AgentDefinition | None:
        if self._agent_definition_registry:
            return self._agent_definition_registry.get(name)
        return None

    async def list_agent_definitions(
        self, include_hidden: bool = False, scope: dict[str, str] | None = None
    ) -> list[AgentDefinition]:
        if self._agent_definition_registry:
            return self._agent_definition_registry.get_all(include_hidden=include_hidden)
        return []

    async def is_valid_primary(self, name: str, scope: dict[str, str] | None = None) -> bool:
        if self._agent_definition_registry:
            return self._agent_definition_registry.is_valid_primary(name)
        return False

    # ------------------------------------------------------------------
    # MCP server CRUD
    # ------------------------------------------------------------------

    async def list_mcp_servers(
        self, scope: dict[str, str] | None = None
    ) -> list[McpServerRegistration]:
        return await self._config_registry.list_mcp_servers(scope)

    async def upsert_mcp_server(self, server: McpServerRegistration) -> None:
        await self._config_registry.upsert_mcp_server(server)

    async def delete_mcp_server(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._config_registry.delete_mcp_server(name, scope)

    # ------------------------------------------------------------------
    # Global defaults
    # ------------------------------------------------------------------

    async def get_global_provider_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalProviderDefaults:
        return await self._config_registry.get_global_provider_defaults(scope)

    async def set_global_provider_defaults(
        self, defaults: GlobalProviderDefaults, scope: dict[str, str] | None = None
    ) -> None:
        await self._config_registry.set_global_provider_defaults(defaults, scope)

    async def get_global_agent_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalAgentDefaults:
        return await self._config_registry.get_global_agent_defaults(scope)

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
        return await self._config_registry.seed_if_absent(
            entity_type, name, scope, definition, source
        )

    # ------------------------------------------------------------------
    # Change log
    # ------------------------------------------------------------------

    async def get_changes_since(self, since: datetime) -> list[ConfigChange]:
        return await self._config_registry.get_changes_since(since)

    async def mark_changes_processed(self, change_ids: list[int]) -> None:
        await self._config_registry.mark_changes_processed(change_ids)


__all__ = ["ConfigStore", "DefaultConfigStore"]
