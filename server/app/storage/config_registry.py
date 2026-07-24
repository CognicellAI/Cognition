"""ConfigRegistry protocol and implementations.

The ConfigRegistry is the single source of truth for all hot-reloadable
agent configuration at runtime: providers, tools, skills, agents, and MCP
servers.

Design:
- Protocol (`ConfigRegistry`) defines the async CRUD interface.
- `SqliteConfigRegistry` implements it using aiosqlite directly (no ORM overhead
  needed for the simple upsert/read patterns here).
- `PostgresConfigRegistry` implements it using asyncpg.
- Scope resolution is built into every read: given a scope dict, we walk from
  most-specific to least-specific and return the first matching row. SQL ORDER
  BY scope_depth DESC LIMIT 1.

Seeding semantics:
- `seed_if_absent(entity_type, name, scope, definition)` inserts only when the
  row does not yet exist. Use this for file bootstrap (DB wins on restarts).
- `upsert(...)` overwrites unconditionally. Use this for API writes.

The `record_change` helper writes to `config_changes` and is called inside
every mutating operation so the dispatcher can pick up the event.
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

import aiosqlite
import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Json
from psycopg_pool import AsyncConnectionPool

from server.app.agent.definition import AgentDefinition
from server.app.storage.common import canonical_json_digest, effective_scope_key
from server.app.storage.config_models import (
    AgentConfigRecord,
    ConfigChange,
    EntityType,
    GlobalAgentDefaults,
    GlobalProviderDefaults,
    McpServerRegistration,
    ProviderConfig,
    SandboxProfile,
    SkillDefinition,
    ToolRegistration,
)

logger = logging.getLogger(__name__)

# Sentinel names for the "global defaults" singleton rows
_GLOBAL_PROVIDER_NAME = "__global__"
_GLOBAL_AGENT_NAME = "__defaults__"


class ConfigRevisionConflictError(Exception):
    """Raised when an exact-scoped conditional configuration write is stale."""


@runtime_checkable
class ConfigRegistry(Protocol):
    """Async CRUD interface for hot-reloadable agent configuration.

    All read methods perform scope resolution: the most-specific matching row
    wins over a global row. An empty scope dict always matches everything and
    acts as the fallback.
    """

    # ------------------------------------------------------------------
    # Provider CRUD
    # ------------------------------------------------------------------

    async def get_provider(
        self, provider_id: str, scope: dict[str, str] | None = None
    ) -> ProviderConfig | None:
        """Return the best-matching provider config for the given scope."""
        ...

    async def list_providers(self, scope: dict[str, str] | None = None) -> list[ProviderConfig]:
        """List all provider configs visible in the given scope."""
        ...

    async def upsert_provider(self, config: ProviderConfig) -> None:
        """Create or replace a provider config row."""
        ...

    async def delete_provider(self, provider_id: str, scope: dict[str, str] | None = None) -> bool:
        """Delete a provider config row. Returns True if row existed."""
        ...

    # ------------------------------------------------------------------
    # Tool CRUD
    # ------------------------------------------------------------------

    async def get_tool(
        self, name: str, scope: dict[str, str] | None = None
    ) -> ToolRegistration | None:
        """Return the best-matching tool registration."""
        ...

    async def list_tools(self, scope: dict[str, str] | None = None) -> list[ToolRegistration]:
        """List all tool registrations visible in the given scope."""
        ...

    async def upsert_tool(self, tool: ToolRegistration) -> None:
        """Create or replace a tool registration."""
        ...

    async def delete_tool(self, name: str, scope: dict[str, str] | None = None) -> bool:
        """Delete a tool registration. Returns True if row existed."""
        ...

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    async def get_skill(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SkillDefinition | None:
        """Return the best-matching skill definition."""
        ...

    async def list_skills(self, scope: dict[str, str] | None = None) -> list[SkillDefinition]:
        """List all skill definitions visible in the given scope."""
        ...

    async def upsert_skill(self, skill: SkillDefinition) -> None:
        """Create or replace a skill definition."""
        ...

    async def delete_skill(self, name: str, scope: dict[str, str] | None = None) -> bool:
        """Delete a skill definition. Returns True if row existed."""
        ...

    # ------------------------------------------------------------------
    # Agent CRUD (raw dict — stored as JSON blob in config_entities)
    # ------------------------------------------------------------------

    async def upsert_agent(
        self,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "api",
        expected_revision: int | None = None,
        create_only: bool = False,
    ) -> AgentConfigRecord:
        """Create or replace an exact-scoped Agent definition."""
        ...

    async def get_agent_record(
        self, name: str, scope: dict[str, str] | None = None
    ) -> AgentConfigRecord | None:
        """Return an Agent only at the complete exact scope."""
        ...

    async def get_agent_raw(
        self, name: str, scope: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        """Return the exact-scoped Agent definition dict, or None."""
        ...

    async def get_agent_raw_with_scope(
        self, name: str, scope: dict[str, str] | None = None
    ) -> tuple[dict[str, Any], dict[str, str]] | None:
        """Return (definition_dict, exact_scope) for an exact-scoped Agent."""
        ...

    async def list_agents(
        self,
        scope: dict[str, str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentDefinition]:
        """List Agent definitions at the complete exact scope."""
        ...

    async def delete_agent(
        self,
        name: str,
        scope: dict[str, str] | None = None,
        expected_revision: int | None = None,
    ) -> bool:
        """Delete an agent definition row. Returns True if row existed."""
        ...

    # ------------------------------------------------------------------
    # MCP server CRUD
    # ------------------------------------------------------------------

    async def list_mcp_servers(
        self, scope: dict[str, str] | None = None
    ) -> list[McpServerRegistration]:
        """List all MCP server registrations visible in the given scope."""
        ...

    async def upsert_mcp_server(self, server: McpServerRegistration) -> None:
        """Create or replace an MCP server registration."""
        ...

    async def delete_mcp_server(self, name: str, scope: dict[str, str] | None = None) -> bool:
        """Delete an MCP server registration. Returns True if row existed."""
        ...

    # ------------------------------------------------------------------
    # Sandbox profile CRUD
    # ------------------------------------------------------------------

    async def get_sandbox_profile(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SandboxProfile | None:
        """Return the best-matching sandbox profile."""
        ...

    async def list_sandbox_profiles(
        self, scope: dict[str, str] | None = None
    ) -> list[SandboxProfile]:
        """List all sandbox profiles visible in the given scope."""
        ...

    async def upsert_sandbox_profile(self, profile: SandboxProfile) -> None:
        """Create or replace a sandbox profile."""
        ...

    async def delete_sandbox_profile(
        self, name: str, scope: dict[str, str] | None = None
    ) -> bool:
        """Delete a sandbox profile. Returns True if row existed."""
        ...

    # ------------------------------------------------------------------
    # Global defaults
    # ------------------------------------------------------------------

    async def get_global_provider_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalProviderDefaults:
        """Return the effective global provider defaults for the scope."""
        ...

    async def set_global_provider_defaults(
        self, defaults: GlobalProviderDefaults, scope: dict[str, str] | None = None
    ) -> None:
        """Upsert the global provider defaults row."""
        ...

    async def get_global_agent_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalAgentDefaults:
        """Return the effective global agent defaults for the scope."""
        ...

    async def set_global_agent_defaults(
        self, defaults: GlobalAgentDefaults, scope: dict[str, str] | None = None
    ) -> None:
        """Upsert the global agent defaults row."""
        ...

    # ------------------------------------------------------------------
    # Seeding (file bootstrap — never overwrites existing API rows)
    # ------------------------------------------------------------------

    async def seed_if_absent(
        self,
        entity_type: EntityType,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "file",
    ) -> bool:
        """Insert row only if no row with (entity_type, name, scope) exists.

        Returns True if a new row was inserted, False if row already existed.
        """
        ...

    # ------------------------------------------------------------------
    # Change log (for dispatcher)
    # ------------------------------------------------------------------

    async def get_changes_since(self, since: datetime) -> list[ConfigChange]:
        """Return all config_changes rows written after `since`."""
        ...

    async def mark_changes_processed(self, change_ids: list[int]) -> None:
        """Mark change log rows as processed."""
        ...


# ---------------------------------------------------------------------------
# Helper: scope depth for ordering
# ---------------------------------------------------------------------------


def _scope_depth(scope: dict[str, str]) -> int:
    """Return the specificity of a scope (number of key-value pairs)."""
    return len(scope)


def _scope_to_json(scope: dict[str, str] | None) -> str:
    """Serialize scope dict to canonical JSON string for DB storage."""
    return json.dumps(scope or {}, sort_keys=True)


def _scope_from_json(raw: str | dict[str, str] | None) -> dict[str, str]:
    """Parse scope from either a JSON string or a pre-decoded dict.

    asyncpg may return JSONB columns as either a string (no codec registered)
    or a dict (codec registered).  This function handles both transparently.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _definition_from_raw(raw: str | dict[str, Any] | None) -> dict[str, Any]:
    """Parse a definition from either a JSON string or a pre-decoded dict.

    Same asyncpg codec ambiguity as _scope_from_json applies to the JSON
    ``definition`` column.
    """
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


# ---------------------------------------------------------------------------
# SQLite implementation
# ---------------------------------------------------------------------------


class SqliteConfigRegistry:
    """SQLite implementation of ConfigRegistry using aiosqlite.

    Shares the same SQLite file as the StorageBackend.

    Uses a single cached aiosqlite connection per instance to avoid spawning
    a new background thread on every operation (aiosqlite threads can't be
    restarted once stopped, which causes ``RuntimeError: threads can only be
    started once`` in test environments with multiple event loops).

    Args:
        db_path: Absolute path to the SQLite database file.
    """

    def __init__(self, db_path: str) -> None:
        self._db_path = db_path
        self._conn: aiosqlite.Connection | None = None
        self._schema_initialized = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def initialize_schema(self) -> None:
        """Create the config_entities and config_changes tables if they don't exist.

        Called once on startup (or in tests). Idempotent — safe to call multiple times.
        Uses synchronous sqlite3 to avoid aiosqlite event-loop threading issues during
        initialization.
        """
        import sqlite3

        conn = sqlite3.connect(self._db_path)
        try:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS config_entities (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '{}',
                    scope_key TEXT NOT NULL,
                    definition TEXT NOT NULL,
                    revision INTEGER NOT NULL DEFAULT 1,
                    definition_digest TEXT NOT NULL,
                    source TEXT NOT NULL DEFAULT 'file',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(entity_type, name, scope_key)
                )
                """
            )
            entity_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(config_entities)")
            }
            if "scope_key" not in entity_columns:
                conn.execute(
                    "ALTER TABLE config_entities ADD COLUMN scope_key TEXT NOT NULL DEFAULT ''"
                )
            if "revision" not in entity_columns:
                conn.execute(
                    "ALTER TABLE config_entities ADD COLUMN revision INTEGER NOT NULL DEFAULT 1"
                )
            if "definition_digest" not in entity_columns:
                conn.execute(
                    "ALTER TABLE config_entities "
                    "ADD COLUMN definition_digest TEXT NOT NULL DEFAULT ''"
                )
            for row_id, raw_scope, raw_definition in conn.execute(
                "SELECT id, scope, definition FROM config_entities"
            ):
                parsed_scope = _scope_from_json(raw_scope)
                parsed_definition = _definition_from_raw(raw_definition)
                conn.execute(
                    """
                    UPDATE config_entities
                    SET scope_key=?, definition_digest=?
                    WHERE id=?
                    """,
                    (
                        effective_scope_key(parsed_scope),
                        canonical_json_digest(parsed_definition),
                        row_id,
                    ),
                )
            conn.execute("DROP INDEX IF EXISTS idx_config_entities_lookup")
            conn.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_config_entities_lookup
                ON config_entities(entity_type, name, scope_key)
                """
            )
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_config_entities_scope_list
                ON config_entities(entity_type, scope_key, name)
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS config_changes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    entity_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    scope TEXT NOT NULL DEFAULT '{}',
                    scope_key TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    changed_at TEXT NOT NULL,
                    processed INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            change_columns = {
                str(row[1]) for row in conn.execute("PRAGMA table_info(config_changes)")
            }
            if "scope_key" not in change_columns:
                conn.execute(
                    "ALTER TABLE config_changes ADD COLUMN scope_key TEXT NOT NULL DEFAULT ''"
                )
                for row_id, raw_scope in conn.execute(
                    "SELECT id, scope FROM config_changes"
                ):
                    conn.execute(
                        "UPDATE config_changes SET scope_key=? WHERE id=?",
                        (effective_scope_key(_scope_from_json(raw_scope)), row_id),
                    )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_config_changes_time ON config_changes(changed_at)"
            )
            conn.commit()
            self._schema_initialized = True
        finally:
            conn.close()

    async def _get_conn(self) -> aiosqlite.Connection:
        """Return a cached aiosqlite connection, creating it on first call."""
        if not self._schema_initialized:
            await self.initialize_schema()
        if self._conn is None:
            self._conn = await aiosqlite.connect(self._db_path)
            self._conn.row_factory = aiosqlite.Row
        return self._conn

    async def close(self) -> None:
        """Close the cached connection. Safe to call multiple times."""
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _upsert_entity(
        self,
        entity_type: str,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str,
        *,
        expected_revision: int | None = None,
        create_only: bool = False,
    ) -> AgentConfigRecord:
        scope_json = _scope_to_json(scope)
        scope_key = effective_scope_key(scope)
        def_json = json.dumps(definition, sort_keys=True, separators=(",", ":"))
        digest = canonical_json_digest(definition)
        now = datetime.now(UTC).isoformat()
        conn = await self._get_conn()
        cursor = await conn.execute(
            """
            SELECT scope, revision
            FROM config_entities
            WHERE entity_type=? AND name=? AND scope_key=?
            """,
            (entity_type, name, scope_key),
        )
        existing = await cursor.fetchone()
        if existing is not None and _scope_from_json(existing["scope"]) != scope:
            raise ConfigRevisionConflictError("Canonical scope hash collision")
        if create_only and existing is not None:
            raise ConfigRevisionConflictError("Entity already exists at this scope")
        current_revision = int(existing["revision"]) if existing is not None else None
        if expected_revision is not None and current_revision != expected_revision:
            raise ConfigRevisionConflictError(
                f"Expected revision {expected_revision}, found {current_revision}"
            )
        revision = (current_revision or 0) + 1
        await conn.execute(
            """
            INSERT INTO config_entities (
                entity_type, name, scope, scope_key, definition, revision,
                definition_digest, source, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_type, name, scope_key)
            DO UPDATE SET definition=excluded.definition,
                          scope=excluded.scope,
                          revision=excluded.revision,
                          definition_digest=excluded.definition_digest,
                          source=excluded.source,
                          updated_at=excluded.updated_at
            """,
            (
                entity_type,
                name,
                scope_json,
                scope_key,
                def_json,
                revision,
                digest,
                source,
                now,
                now,
            ),
        )
        await self._record_change(conn, entity_type, name, scope, "upsert")
        await conn.commit()
        return AgentConfigRecord(
            name=name,
            scope=scope,
            scope_key=scope_key,
            definition=definition,
            revision=revision,
            definition_digest=digest,
            source="file" if source == "file" else "api",
        )

    async def _delete_entity(
        self, entity_type: str, name: str, scope: dict[str, str] | None
    ) -> bool:
        exact_scope = scope or {}
        scope_key = effective_scope_key(exact_scope)
        conn = await self._get_conn()
        existing = await (
            await conn.execute(
                """
                SELECT scope FROM config_entities
                WHERE entity_type=? AND name=? AND scope_key=?
                """,
                (entity_type, name, scope_key),
            )
        ).fetchone()
        if existing is None or _scope_from_json(existing["scope"]) != exact_scope:
            return False
        cursor = await conn.execute(
            "DELETE FROM config_entities WHERE entity_type=? AND name=? AND scope_key=?",
            (entity_type, name, scope_key),
        )
        deleted = cursor.rowcount > 0
        if deleted:
            await self._record_change(conn, entity_type, name, exact_scope, "delete")
        await conn.commit()
        return deleted

    async def _get_entity(
        self,
        entity_type: str,
        name: str,
        scope: dict[str, str] | None,
    ) -> dict[str, Any] | None:
        """Return the most-specific matching row for (entity_type, name, scope).

        Scope resolution: rows with more scope keys win over fewer.
        """
        target_scope = scope or {}
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT scope, definition FROM config_entities WHERE entity_type=? AND name=?",
            (entity_type, name),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return None

        # Find best-matching row: all row scope keys must match target, pick deepest
        best: tuple[int, dict[str, Any]] | None = None
        for row in rows:
            row_scope = _scope_from_json(row["scope"])
            # Row scope must be a subset of target scope
            if all(target_scope.get(k) == v for k, v in row_scope.items()):
                depth = _scope_depth(row_scope)
                if best is None or depth > best[0]:
                    best = (depth, json.loads(row["definition"]))

        return best[1] if best else None

    async def _get_entity_with_scope(
        self,
        entity_type: str,
        name: str,
        scope: dict[str, str] | None,
    ) -> tuple[dict[str, Any], dict[str, str]] | None:
        """Return (definition_dict, matched_scope) for the best-matching row."""
        target_scope = scope or {}
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT scope, definition FROM config_entities WHERE entity_type=? AND name=?",
            (entity_type, name),
        ) as cursor:
            rows = await cursor.fetchall()

        if not rows:
            return None

        best: tuple[int, dict[str, Any], dict[str, str]] | None = None
        for row in rows:
            row_scope = _scope_from_json(row["scope"])
            if all(target_scope.get(k) == v for k, v in row_scope.items()):
                depth = _scope_depth(row_scope)
                if best is None or depth > best[0]:
                    best = (depth, json.loads(row["definition"]), row_scope)

        return (best[1], best[2]) if best else None

    async def _list_entities(
        self,
        entity_type: str,
        scope: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        """Return all rows of entity_type visible in the given scope.

        A row is visible if its scope is a subset of the target scope.
        For each (name), only the most-specific row is returned.
        """
        target_scope = scope or {}
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT name, scope, definition FROM config_entities WHERE entity_type=?",
            (entity_type,),
        ) as cursor:
            rows = await cursor.fetchall()

        # Group by name, pick best-matching scope
        best_by_name: dict[str, tuple[int, dict[str, Any]]] = {}
        for row in rows:
            row_scope = _scope_from_json(row["scope"])
            if all(target_scope.get(k) == v for k, v in row_scope.items()):
                depth = _scope_depth(row_scope)
                name = row["name"]
                if name not in best_by_name or depth > best_by_name[name][0]:
                    best_by_name[name] = (depth, json.loads(row["definition"]))

        return [v[1] for v in best_by_name.values()]

    async def _record_change(
        self,
        conn: aiosqlite.Connection,
        entity_type: str,
        name: str,
        scope: dict[str, str],
        operation: str,
    ) -> None:
        """Append a row to config_changes (called inside an open transaction)."""
        now = datetime.now(UTC).isoformat()
        await conn.execute(
            """
            INSERT INTO config_changes (
                entity_type, name, scope, scope_key, operation, changed_at, processed
            )
            VALUES (?, ?, ?, ?, ?, ?, 0)
            """,
            (
                entity_type,
                name,
                _scope_to_json(scope),
                effective_scope_key(scope),
                operation,
                now,
            ),
        )

    # ------------------------------------------------------------------
    # Provider CRUD
    # ------------------------------------------------------------------

    async def get_provider(
        self, provider_id: str, scope: dict[str, str] | None = None
    ) -> ProviderConfig | None:
        data = await self._get_entity("provider", provider_id, scope)
        if data is None:
            return None
        return ProviderConfig.model_validate(data)

    async def list_providers(self, scope: dict[str, str] | None = None) -> list[ProviderConfig]:
        rows = await self._list_entities("provider", scope)
        # Filter out global defaults entry (which has no 'id' field)
        return [ProviderConfig.model_validate(r) for r in rows if "id" in r]

    async def upsert_provider(self, config: ProviderConfig) -> None:
        data = config.model_dump()
        await self._upsert_entity("provider", config.id, config.scope, data, config.source)

    async def delete_provider(self, provider_id: str, scope: dict[str, str] | None = None) -> bool:
        return await self._delete_entity("provider", provider_id, scope)

    # ------------------------------------------------------------------
    # Tool CRUD
    # ------------------------------------------------------------------

    async def get_tool(
        self, name: str, scope: dict[str, str] | None = None
    ) -> ToolRegistration | None:
        data = await self._get_entity("tool", name, scope)
        if data is None:
            return None
        return ToolRegistration.model_validate(data)

    async def list_tools(self, scope: dict[str, str] | None = None) -> list[ToolRegistration]:
        rows = await self._list_entities("tool", scope)
        return [ToolRegistration.model_validate(r) for r in rows]

    async def upsert_tool(self, tool: ToolRegistration) -> None:
        data = tool.model_dump()
        await self._upsert_entity("tool", tool.name, tool.scope, data, tool.source)

    async def delete_tool(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._delete_entity("tool", name, scope)

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    async def get_skill(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SkillDefinition | None:
        data = await self._get_entity("skill", name, scope)
        if data is None:
            return None
        return SkillDefinition.model_validate(data)

    async def list_skills(self, scope: dict[str, str] | None = None) -> list[SkillDefinition]:
        rows = await self._list_entities("skill", scope)
        return [SkillDefinition.model_validate(r) for r in rows]

    async def upsert_skill(self, skill: SkillDefinition) -> None:
        data = skill.model_dump()
        await self._upsert_entity("skill", skill.name, skill.scope, data, skill.source)

    async def delete_skill(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._delete_entity("skill", name, scope)

    # ------------------------------------------------------------------
    # Agent CRUD
    # ------------------------------------------------------------------

    async def upsert_agent(
        self,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "api",
        expected_revision: int | None = None,
        create_only: bool = False,
    ) -> AgentConfigRecord:
        validated = AgentDefinition.model_validate(definition).model_dump(mode="json")
        return await self._upsert_entity(
            "agent",
            name,
            scope,
            validated,
            source,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    async def get_agent_record(
        self, name: str, scope: dict[str, str] | None = None
    ) -> AgentConfigRecord | None:
        exact_scope = scope or {}
        scope_key = effective_scope_key(exact_scope)
        conn = await self._get_conn()
        row = await (
            await conn.execute(
                """
                SELECT scope, definition, revision, definition_digest, source
                FROM config_entities
                WHERE entity_type='agent' AND name=? AND scope_key=?
                """,
                (name, scope_key),
            )
        ).fetchone()
        if row is None or _scope_from_json(row["scope"]) != exact_scope:
            return None
        return AgentConfigRecord(
            name=name,
            scope=exact_scope,
            scope_key=scope_key,
            definition=_definition_from_raw(row["definition"]),
            revision=int(row["revision"]),
            definition_digest=str(row["definition_digest"]),
            source="file" if row["source"] == "file" else "api",
        )

    async def get_agent_raw(
        self, name: str, scope: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        record = await self.get_agent_record(name, scope)
        return dict(record.definition) if record else None

    async def get_agent_raw_with_scope(
        self, name: str, scope: dict[str, str] | None = None
    ) -> tuple[dict[str, Any], dict[str, str]] | None:
        record = await self.get_agent_record(name, scope)
        return (dict(record.definition), dict(record.scope)) if record else None

    async def list_agents(
        self,
        scope: dict[str, str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentDefinition]:
        exact_scope = scope or {}
        scope_key = effective_scope_key(exact_scope)
        conn = await self._get_conn()
        query = """
            SELECT scope, definition
            FROM config_entities
            WHERE entity_type='agent' AND scope_key=?
            ORDER BY name
        """
        params: list[Any] = [scope_key]
        if limit is not None:
            query += " LIMIT ? OFFSET ?"
            params.extend([limit, offset])
        async with conn.execute(query, params) as cursor:
            rows = await cursor.fetchall()
        return [
            AgentDefinition.model_validate(_definition_from_raw(row["definition"]))
            for row in rows
            if _scope_from_json(row["scope"]) == exact_scope
        ]

    async def delete_agent(
        self,
        name: str,
        scope: dict[str, str] | None = None,
        expected_revision: int | None = None,
    ) -> bool:
        if expected_revision is not None:
            record = await self.get_agent_record(name, scope)
            if record is None or record.revision != expected_revision:
                raise ConfigRevisionConflictError(
                    f"Expected revision {expected_revision} was not current"
                )
        return await self._delete_entity("agent", name, scope)

    # ------------------------------------------------------------------
    # MCP server CRUD (SqliteConfigRegistry)
    # ------------------------------------------------------------------

    async def list_mcp_servers(
        self, scope: dict[str, str] | None = None
    ) -> list[McpServerRegistration]:
        rows = await self._list_entities("mcp_server", scope)
        return [McpServerRegistration.model_validate(r) for r in rows]

    async def upsert_mcp_server(self, server: McpServerRegistration) -> None:
        data = server.model_dump()
        await self._upsert_entity("mcp_server", server.name, server.scope, data, server.source)

    async def delete_mcp_server(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._delete_entity("mcp_server", name, scope)

    # ------------------------------------------------------------------
    # Sandbox profile CRUD (SqliteConfigRegistry)
    # ------------------------------------------------------------------

    async def get_sandbox_profile(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SandboxProfile | None:
        data = await self._get_entity("sandbox_profile", name, scope)
        if data is None:
            return None
        return SandboxProfile.model_validate(data)

    async def list_sandbox_profiles(
        self, scope: dict[str, str] | None = None
    ) -> list[SandboxProfile]:
        rows = await self._list_entities("sandbox_profile", scope)
        return [SandboxProfile.model_validate(r) for r in rows]

    async def upsert_sandbox_profile(self, profile: SandboxProfile) -> None:
        data = profile.model_dump()
        await self._upsert_entity(
            "sandbox_profile",
            profile.name,
            profile.scope,
            data,
            profile.source,
        )

    async def delete_sandbox_profile(
        self, name: str, scope: dict[str, str] | None = None
    ) -> bool:
        return await self._delete_entity("sandbox_profile", name, scope)

    # ------------------------------------------------------------------
    # Global defaults
    # ------------------------------------------------------------------

    async def get_global_provider_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalProviderDefaults:
        data = await self._get_entity("provider", _GLOBAL_PROVIDER_NAME, scope)
        if data is None:
            return GlobalProviderDefaults()
        return GlobalProviderDefaults.model_validate(data)

    async def set_global_provider_defaults(
        self, defaults: GlobalProviderDefaults, scope: dict[str, str] | None = None
    ) -> None:
        await self._upsert_entity(
            "provider",
            _GLOBAL_PROVIDER_NAME,
            scope or {},
            defaults.model_dump(),
            "api",
        )

    async def get_global_agent_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalAgentDefaults:
        data = await self._get_entity("agent", _GLOBAL_AGENT_NAME, scope)
        if data is None:
            return GlobalAgentDefaults()
        return GlobalAgentDefaults.model_validate(data)

    async def set_global_agent_defaults(
        self, defaults: GlobalAgentDefaults, scope: dict[str, str] | None = None
    ) -> None:
        await self._upsert_entity(
            "agent",
            _GLOBAL_AGENT_NAME,
            scope or {},
            defaults.model_dump(),
            "api",
        )

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
        scope_json = _scope_to_json(scope)
        scope_key = effective_scope_key(scope)
        def_json = json.dumps(definition, sort_keys=True, separators=(",", ":"))
        digest = canonical_json_digest(definition)
        now = datetime.now(UTC).isoformat()
        conn = await self._get_conn()
        cursor = await conn.execute(
            """
            SELECT id, scope FROM config_entities
            WHERE entity_type=? AND name=? AND scope_key=?
            """,
            (entity_type, name, scope_key),
        )
        existing = await cursor.fetchone()
        if existing:
            if _scope_from_json(existing["scope"]) != scope:
                raise ConfigRevisionConflictError("Canonical scope hash collision")
            return False
        await conn.execute(
            """
            INSERT INTO config_entities (
                entity_type, name, scope, scope_key, definition, revision,
                definition_digest, source, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
            """,
            (
                entity_type,
                name,
                scope_json,
                scope_key,
                def_json,
                digest,
                source,
                now,
                now,
            ),
        )
        await self._record_change(conn, entity_type, name, scope, "upsert")
        await conn.commit()
        return True

    # ------------------------------------------------------------------
    # Change log
    # ------------------------------------------------------------------

    async def get_changes_since(self, since: datetime) -> list[ConfigChange]:
        since_str = since.isoformat()
        conn = await self._get_conn()
        async with conn.execute(
            "SELECT id, entity_type, name, scope, operation, changed_at FROM config_changes "
            "WHERE changed_at > ? ORDER BY changed_at ASC",
            (since_str,),
        ) as cursor:
            rows = await cursor.fetchall()
        result = []
        for row in rows:
            result.append(
                ConfigChange(
                    id=row["id"],
                    entity_type=row["entity_type"],
                    name=row["name"],
                    scope=_scope_from_json(row["scope"]),
                    operation=row["operation"],
                    changed_at=datetime.fromisoformat(row["changed_at"]),
                )
            )
        return result

    async def mark_changes_processed(self, change_ids: list[int]) -> None:
        if not change_ids:
            return
        placeholders = ",".join("?" * len(change_ids))
        conn = await self._get_conn()
        await conn.execute(
            f"UPDATE config_changes SET processed=1 WHERE id IN ({placeholders})",
            change_ids,
        )
        await conn.commit()


# ---------------------------------------------------------------------------
# Postgres implementation
# ---------------------------------------------------------------------------


class PostgresConfigRegistry:
    """Postgres implementation of ConfigRegistry using psycopg pool.

    Args:
        dsn: Postgres connection string
             (e.g. "postgresql://user:pass@host/db").
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]] | None = None
        self._pool_lock = asyncio.Lock()

    async def _get_pool(self) -> AsyncConnectionPool[psycopg.AsyncConnection[dict[str, Any]]]:
        if self._pool is None:
            async with self._pool_lock:
                if self._pool is None:
                    self._pool = AsyncConnectionPool(
                        self._dsn,
                        open=False,
                        min_size=1,
                        kwargs={
                            "autocommit": True,
                            "prepare_threshold": 0,
                            "row_factory": dict_row,
                        },
                    )
                    await self._pool.open()
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None

    @staticmethod
    def _serialize_scope(scope: dict[str, str]) -> Json:
        return Json(scope)

    @staticmethod
    def _serialize_definition(definition: dict[str, Any]) -> Json:
        return Json(definition)

    @staticmethod
    def _jsonb_param(value: Json) -> psycopg.types.json.Jsonb:
        return psycopg.types.json.Jsonb(value.obj)

    @staticmethod
    async def _fetch_all(
        conn: psycopg.AsyncConnection[dict[str, Any]], query: str, params: tuple[Any, ...]
    ) -> list[dict[str, Any]]:
        cursor = await conn.execute(query, params)
        return list(await cursor.fetchall())

    @staticmethod
    async def _fetch_one(
        conn: psycopg.AsyncConnection[dict[str, Any]], query: str, params: tuple[Any, ...]
    ) -> dict[str, Any] | None:
        cursor = await conn.execute(query, params)
        return await cursor.fetchone()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _upsert_entity(
        self,
        entity_type: str,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str,
        *,
        expected_revision: int | None = None,
        create_only: bool = False,
    ) -> AgentConfigRecord:
        pool = await self._get_pool()
        now = datetime.now(UTC)
        scope_key = effective_scope_key(scope)
        digest = canonical_json_digest(definition)
        scope_param = self._jsonb_param(self._serialize_scope(scope))
        definition_param = self._jsonb_param(self._serialize_definition(definition))
        async with pool.connection() as conn:
            if expected_revision is not None:
                row = await self._fetch_one(
                    conn,
                    """
                    UPDATE config_entities
                    SET definition=%s, revision=revision + 1,
                        definition_digest=%s, source=%s, updated_at=%s
                    WHERE entity_type=%s AND name=%s AND scope_key=%s
                      AND scope=%s AND revision=%s
                    RETURNING revision
                    """,
                    (
                        definition_param,
                        digest,
                        source,
                        now,
                        entity_type,
                        name,
                        scope_key,
                        scope_param,
                        expected_revision,
                    ),
                )
                if row is None:
                    raise ConfigRevisionConflictError(
                        f"Expected revision {expected_revision} was not current"
                    )
            else:
                conflict_action = (
                    "DO NOTHING"
                    if create_only
                    else """
                    DO UPDATE SET scope=EXCLUDED.scope,
                                  definition=EXCLUDED.definition,
                                  revision=config_entities.revision + 1,
                                  definition_digest=EXCLUDED.definition_digest,
                                  source=EXCLUDED.source,
                                  updated_at=EXCLUDED.updated_at
                    """
                )
                row = await self._fetch_one(
                    conn,
                    f"""
                    INSERT INTO config_entities (
                        entity_type, name, scope, scope_key, definition, revision,
                        definition_digest, source, created_at, updated_at
                    )
                    VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s)
                    ON CONFLICT (entity_type, name, scope_key)
                    {conflict_action}
                    RETURNING revision
                    """,
                    (
                        entity_type,
                        name,
                        scope_param,
                        scope_key,
                        definition_param,
                        digest,
                        source,
                        now,
                        now,
                    ),
                )
                if row is None:
                    raise ConfigRevisionConflictError("Entity already exists at this scope")
            await self._record_change(conn, entity_type, name, scope, "upsert")
        revision = int(row["revision"])
        return AgentConfigRecord(
            name=name,
            scope=scope,
            scope_key=scope_key,
            definition=definition,
            revision=revision,
            definition_digest=digest,
            source="file" if source == "file" else "api",
        )

    async def _delete_entity(
        self, entity_type: str, name: str, scope: dict[str, str] | None
    ) -> bool:
        exact_scope = scope or {}
        pool = await self._get_pool()
        async with pool.connection() as conn:
            result = await conn.execute(
                """
                DELETE FROM config_entities
                WHERE entity_type=%s AND name=%s AND scope_key=%s AND scope=%s
                """,
                (
                    entity_type,
                    name,
                    effective_scope_key(exact_scope),
                    self._jsonb_param(self._serialize_scope(exact_scope)),
                ),
            )
            deleted = result.rowcount is not None and result.rowcount > 0
            if deleted:
                await self._record_change(conn, entity_type, name, exact_scope, "delete")
        return deleted

    async def _get_entity(
        self,
        entity_type: str,
        name: str,
        scope: dict[str, str] | None,
    ) -> dict[str, Any] | None:
        target_scope = scope or {}
        pool = await self._get_pool()
        async with pool.connection() as conn:
            rows = await self._fetch_all(
                conn,
                "SELECT scope, definition FROM config_entities WHERE entity_type=%s AND name=%s",
                (entity_type, name),
            )
        if not rows:
            return None

        best: tuple[int, dict[str, Any]] | None = None
        for row in rows:
            row_scope: dict[str, str] = _scope_from_json(row["scope"])
            if all(target_scope.get(k) == v for k, v in row_scope.items()):
                depth = len(row_scope)
                definition: dict[str, Any] = _definition_from_raw(row["definition"])
                if best is None or depth > best[0]:
                    best = (depth, definition)
        return best[1] if best else None

    async def _get_entity_with_scope(
        self,
        entity_type: str,
        name: str,
        scope: dict[str, str] | None,
    ) -> tuple[dict[str, Any], dict[str, str]] | None:
        """Return (definition_dict, matched_scope) for the best-matching row."""
        target_scope = scope or {}
        pool = await self._get_pool()
        async with pool.connection() as conn:
            rows = await self._fetch_all(
                conn,
                "SELECT scope, definition FROM config_entities WHERE entity_type=%s AND name=%s",
                (entity_type, name),
            )
        if not rows:
            return None

        best: tuple[int, dict[str, Any], dict[str, str]] | None = None
        for row in rows:
            row_scope: dict[str, str] = _scope_from_json(row["scope"])
            if all(target_scope.get(k) == v for k, v in row_scope.items()):
                depth = len(row_scope)
                definition: dict[str, Any] = _definition_from_raw(row["definition"])
                if best is None or depth > best[0]:
                    best = (depth, definition, row_scope)
        return (best[1], best[2]) if best else None

    async def _list_entities(
        self,
        entity_type: str,
        scope: dict[str, str] | None,
    ) -> list[dict[str, Any]]:
        target_scope = scope or {}
        pool = await self._get_pool()
        async with pool.connection() as conn:
            rows = await self._fetch_all(
                conn,
                "SELECT name, scope, definition FROM config_entities WHERE entity_type=%s",
                (entity_type,),
            )
        best_by_name: dict[str, tuple[int, dict[str, Any]]] = {}
        for row in rows:
            row_scope: dict[str, str] = _scope_from_json(row["scope"])
            if all(target_scope.get(k) == v for k, v in row_scope.items()):
                depth = len(row_scope)
                name: str = row["name"]
                definition: dict[str, Any] = _definition_from_raw(row["definition"])
                if name not in best_by_name or depth > best_by_name[name][0]:
                    best_by_name[name] = (depth, definition)
        return [v[1] for v in best_by_name.values()]

    async def _record_change(
        self,
        conn: Any,
        entity_type: str,
        name: str,
        scope: dict[str, str],
        operation: str,
    ) -> None:
        now = datetime.now(UTC)
        await conn.execute(
            """
            INSERT INTO config_changes (
                entity_type, name, scope, scope_key, operation, changed_at, processed
            )
            VALUES (%s, %s, %s, %s, %s, %s, false)
            """,
            (
                entity_type,
                name,
                self._jsonb_param(self._serialize_scope(scope)),
                effective_scope_key(scope),
                operation,
                now,
            ),
        )
        # NOTIFY for cross-instance invalidation
        await conn.execute("NOTIFY cognition_config_changes")

    # ------------------------------------------------------------------
    # Provider CRUD
    # ------------------------------------------------------------------

    async def get_provider(
        self, provider_id: str, scope: dict[str, str] | None = None
    ) -> ProviderConfig | None:
        data = await self._get_entity("provider", provider_id, scope)
        return ProviderConfig.model_validate(data) if data else None

    async def list_providers(self, scope: dict[str, str] | None = None) -> list[ProviderConfig]:
        rows = await self._list_entities("provider", scope)
        # Filter out global defaults entry (which has no 'id' field)
        return [ProviderConfig.model_validate(r) for r in rows if "id" in r]

    async def upsert_provider(self, config: ProviderConfig) -> None:
        await self._upsert_entity(
            "provider", config.id, config.scope, config.model_dump(), config.source
        )

    async def delete_provider(self, provider_id: str, scope: dict[str, str] | None = None) -> bool:
        return await self._delete_entity("provider", provider_id, scope)

    # ------------------------------------------------------------------
    # Tool CRUD
    # ------------------------------------------------------------------

    async def get_tool(
        self, name: str, scope: dict[str, str] | None = None
    ) -> ToolRegistration | None:
        data = await self._get_entity("tool", name, scope)
        return ToolRegistration.model_validate(data) if data else None

    async def list_tools(self, scope: dict[str, str] | None = None) -> list[ToolRegistration]:
        rows = await self._list_entities("tool", scope)
        return [ToolRegistration.model_validate(r) for r in rows]

    async def upsert_tool(self, tool: ToolRegistration) -> None:
        await self._upsert_entity("tool", tool.name, tool.scope, tool.model_dump(), tool.source)

    async def delete_tool(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._delete_entity("tool", name, scope)

    # ------------------------------------------------------------------
    # Skill CRUD
    # ------------------------------------------------------------------

    async def get_skill(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SkillDefinition | None:
        data = await self._get_entity("skill", name, scope)
        return SkillDefinition.model_validate(data) if data else None

    async def list_skills(self, scope: dict[str, str] | None = None) -> list[SkillDefinition]:
        rows = await self._list_entities("skill", scope)
        return [SkillDefinition.model_validate(r) for r in rows]

    async def upsert_skill(self, skill: SkillDefinition) -> None:
        await self._upsert_entity(
            "skill", skill.name, skill.scope, skill.model_dump(), skill.source
        )

    async def delete_skill(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._delete_entity("skill", name, scope)

    # Agent CRUD
    async def upsert_agent(
        self,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "api",
        expected_revision: int | None = None,
        create_only: bool = False,
    ) -> AgentConfigRecord:
        validated = AgentDefinition.model_validate(definition).model_dump(mode="json")
        return await self._upsert_entity(
            "agent",
            name,
            scope,
            validated,
            source,
            expected_revision=expected_revision,
            create_only=create_only,
        )

    async def get_agent_record(
        self, name: str, scope: dict[str, str] | None = None
    ) -> AgentConfigRecord | None:
        exact_scope = scope or {}
        scope_key = effective_scope_key(exact_scope)
        pool = await self._get_pool()
        async with pool.connection() as conn:
            row = await self._fetch_one(
                conn,
                """
                SELECT scope, definition, revision, definition_digest, source
                FROM config_entities
                WHERE entity_type='agent' AND name=%s AND scope_key=%s AND scope=%s
                """,
                (
                    name,
                    scope_key,
                    self._jsonb_param(self._serialize_scope(exact_scope)),
                ),
            )
        if row is None:
            return None
        return AgentConfigRecord(
            name=name,
            scope=exact_scope,
            scope_key=scope_key,
            definition=_definition_from_raw(row["definition"]),
            revision=int(row["revision"]),
            definition_digest=str(row["definition_digest"]),
            source="file" if row["source"] == "file" else "api",
        )

    async def get_agent_raw(
        self, name: str, scope: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        record = await self.get_agent_record(name, scope)
        return dict(record.definition) if record else None

    async def get_agent_raw_with_scope(
        self, name: str, scope: dict[str, str] | None = None
    ) -> tuple[dict[str, Any], dict[str, str]] | None:
        record = await self.get_agent_record(name, scope)
        return (dict(record.definition), dict(record.scope)) if record else None

    async def list_agents(
        self,
        scope: dict[str, str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentDefinition]:
        exact_scope = scope or {}
        pool = await self._get_pool()
        async with pool.connection() as conn:
            query = """
                SELECT definition
                FROM config_entities
                WHERE entity_type='agent' AND scope_key=%s AND scope=%s
                ORDER BY name
            """
            params: list[Any] = [
                effective_scope_key(exact_scope),
                self._jsonb_param(self._serialize_scope(exact_scope)),
            ]
            if limit is not None:
                query += " LIMIT %s OFFSET %s"
                params.extend([limit, offset])
            rows = await self._fetch_all(
                conn,
                query,
                tuple(params),
            )
        return [
            AgentDefinition.model_validate(_definition_from_raw(row["definition"]))
            for row in rows
        ]

    async def delete_agent(
        self,
        name: str,
        scope: dict[str, str] | None = None,
        expected_revision: int | None = None,
    ) -> bool:
        exact_scope = scope or {}
        pool = await self._get_pool()
        async with pool.connection() as conn:
            params: list[Any] = [
                name,
                effective_scope_key(exact_scope),
                self._jsonb_param(self._serialize_scope(exact_scope)),
            ]
            revision_clause = ""
            if expected_revision is not None:
                revision_clause = " AND revision=%s"
                params.append(expected_revision)
            result = await conn.execute(
                f"""
                DELETE FROM config_entities
                WHERE entity_type='agent' AND name=%s AND scope_key=%s AND scope=%s
                {revision_clause}
                """,
                tuple(params),
            )
            deleted = result.rowcount is not None and result.rowcount > 0
            if not deleted and expected_revision is not None:
                raise ConfigRevisionConflictError(
                    f"Expected revision {expected_revision} was not current"
                )
            if deleted:
                await self._record_change(conn, "agent", name, exact_scope, "delete")
            return deleted

    # ------------------------------------------------------------------
    # MCP server CRUD
    # ------------------------------------------------------------------

    async def list_mcp_servers(
        self, scope: dict[str, str] | None = None
    ) -> list[McpServerRegistration]:
        rows = await self._list_entities("mcp_server", scope)
        return [McpServerRegistration.model_validate(r) for r in rows]

    async def upsert_mcp_server(self, server: McpServerRegistration) -> None:
        await self._upsert_entity(
            "mcp_server", server.name, server.scope, server.model_dump(), server.source
        )

    async def delete_mcp_server(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return await self._delete_entity("mcp_server", name, scope)

    # ------------------------------------------------------------------
    # Sandbox profile CRUD
    # ------------------------------------------------------------------

    async def get_sandbox_profile(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SandboxProfile | None:
        data = await self._get_entity("sandbox_profile", name, scope)
        return SandboxProfile.model_validate(data) if data else None

    async def list_sandbox_profiles(
        self, scope: dict[str, str] | None = None
    ) -> list[SandboxProfile]:
        rows = await self._list_entities("sandbox_profile", scope)
        return [SandboxProfile.model_validate(r) for r in rows]

    async def upsert_sandbox_profile(self, profile: SandboxProfile) -> None:
        await self._upsert_entity(
            "sandbox_profile",
            profile.name,
            profile.scope,
            profile.model_dump(),
            profile.source,
        )

    async def delete_sandbox_profile(
        self, name: str, scope: dict[str, str] | None = None
    ) -> bool:
        return await self._delete_entity("sandbox_profile", name, scope)

    # ------------------------------------------------------------------
    # Global defaults
    # ------------------------------------------------------------------

    async def get_global_provider_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalProviderDefaults:
        data = await self._get_entity("provider", _GLOBAL_PROVIDER_NAME, scope)
        return GlobalProviderDefaults.model_validate(data) if data else GlobalProviderDefaults()

    async def set_global_provider_defaults(
        self, defaults: GlobalProviderDefaults, scope: dict[str, str] | None = None
    ) -> None:
        await self._upsert_entity(
            "provider", _GLOBAL_PROVIDER_NAME, scope or {}, defaults.model_dump(), "api"
        )

    async def get_global_agent_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalAgentDefaults:
        data = await self._get_entity("agent", _GLOBAL_AGENT_NAME, scope)
        return GlobalAgentDefaults.model_validate(data) if data else GlobalAgentDefaults()

    async def set_global_agent_defaults(
        self, defaults: GlobalAgentDefaults, scope: dict[str, str] | None = None
    ) -> None:
        await self._upsert_entity(
            "agent", _GLOBAL_AGENT_NAME, scope or {}, defaults.model_dump(), "api"
        )

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
        pool = await self._get_pool()
        now = datetime.now(UTC)
        scope_key = effective_scope_key(scope)
        digest = canonical_json_digest(definition)
        async with pool.connection() as conn:
            existing = await self._fetch_one(
                conn,
                """
                SELECT id, scope FROM config_entities
                WHERE entity_type=%s AND name=%s AND scope_key=%s
                """,
                (entity_type, name, scope_key),
            )
            if existing:
                if _scope_from_json(existing["scope"]) != scope:
                    raise ConfigRevisionConflictError("Canonical scope hash collision")
                return False
            await conn.execute(
                """
                INSERT INTO config_entities (
                    entity_type, name, scope, scope_key, definition, revision,
                    definition_digest, source, created_at, updated_at
                )
                VALUES (%s, %s, %s, %s, %s, 1, %s, %s, %s, %s)
                """,
                (
                    entity_type,
                    name,
                    self._jsonb_param(self._serialize_scope(scope)),
                    scope_key,
                    self._jsonb_param(self._serialize_definition(definition)),
                    digest,
                    source,
                    now,
                    now,
                ),
            )
            await self._record_change(conn, entity_type, name, scope, "upsert")
        return True

    # ------------------------------------------------------------------
    # Change log
    # ------------------------------------------------------------------

    async def get_changes_since(self, since: datetime) -> list[ConfigChange]:
        pool = await self._get_pool()
        async with pool.connection() as conn:
            rows = await self._fetch_all(
                conn,
                "SELECT id, entity_type, name, scope, operation, changed_at "
                "FROM config_changes WHERE changed_at > %s ORDER BY changed_at ASC",
                (since,),
            )
        result = []
        for row in rows:
            result.append(
                ConfigChange(
                    id=row["id"],
                    entity_type=row["entity_type"],
                    name=row["name"],
                    scope=dict(row["scope"]),
                    operation=row["operation"],
                    changed_at=row["changed_at"],
                )
            )
        return result

    async def mark_changes_processed(self, change_ids: list[int]) -> None:
        if not change_ids:
            return
        pool = await self._get_pool()
        async with pool.connection() as conn:
            await conn.execute(
                "UPDATE config_changes SET processed=true WHERE id = ANY(%s::int[])",
                (change_ids,),
            )


# ---------------------------------------------------------------------------
# In-memory implementation (for tests / memory backend)
# ---------------------------------------------------------------------------


class MemoryConfigRegistry:
    """In-memory ConfigRegistry for testing and memory-backend deployments.

    Thread-safe within a single async event loop. Not suitable for
    multi-process deployments.
    """

    def __init__(self) -> None:
        # Keyed by (entity_type, name, scope_json)
        self._store: dict[tuple[str, str, str], dict[str, Any]] = {}
        self._records: dict[tuple[str, str, str], AgentConfigRecord] = {}
        self._changes: list[ConfigChange] = []
        self._change_id = 0

    def _key(self, entity_type: str, name: str, scope: dict[str, str]) -> tuple[str, str, str]:
        return (entity_type, name, _scope_to_json(scope))

    def _get_entity(
        self, entity_type: str, name: str, scope: dict[str, str] | None
    ) -> dict[str, Any] | None:
        target = scope or {}
        best: tuple[int, dict[str, Any]] | None = None
        for (et, n, s_json), defn in self._store.items():
            if et != entity_type or n != name:
                continue
            row_scope = _scope_from_json(s_json)
            if all(target.get(k) == v for k, v in row_scope.items()):
                depth = len(row_scope)
                if best is None or depth > best[0]:
                    best = (depth, defn)
        return best[1] if best else None

    def _get_entity_with_scope(
        self, entity_type: str, name: str, scope: dict[str, str] | None
    ) -> tuple[dict[str, Any], dict[str, str]] | None:
        """Return (definition_dict, matched_scope) for the best-matching row."""
        target = scope or {}
        best: tuple[int, dict[str, Any], dict[str, str]] | None = None
        for (et, n, s_json), defn in self._store.items():
            if et != entity_type or n != name:
                continue
            row_scope = _scope_from_json(s_json)
            if all(target.get(k) == v for k, v in row_scope.items()):
                depth = len(row_scope)
                if best is None or depth > best[0]:
                    best = (depth, defn, row_scope)
        return (best[1], best[2]) if best else None

    def _list_entities(
        self, entity_type: str, scope: dict[str, str] | None
    ) -> list[dict[str, Any]]:
        target = scope or {}
        best_by_name: dict[str, tuple[int, dict[str, Any]]] = {}
        for (et, name, s_json), defn in self._store.items():
            if et != entity_type:
                continue
            row_scope = _scope_from_json(s_json)
            if all(target.get(k) == v for k, v in row_scope.items()):
                depth = len(row_scope)
                if name not in best_by_name or depth > best_by_name[name][0]:
                    best_by_name[name] = (depth, defn)
        return [v[1] for v in best_by_name.values()]

    def _upsert(
        self, entity_type: str, name: str, scope: dict[str, str], defn: dict[str, Any], source: str
    ) -> AgentConfigRecord:
        key = self._key(entity_type, name, scope)
        existing = self._records.get(key)
        record = AgentConfigRecord(
            name=name,
            scope=scope,
            scope_key=effective_scope_key(scope),
            definition=defn,
            revision=(existing.revision + 1) if existing else 1,
            definition_digest=canonical_json_digest(defn),
            source="file" if source == "file" else "api",
        )
        self._store[key] = defn
        self._records[key] = record
        self._record_change(entity_type, name, scope, "upsert")
        return record

    def _delete(self, entity_type: str, name: str, scope: dict[str, str] | None) -> bool:
        key = self._key(entity_type, name, scope or {})
        if key not in self._store:
            return False
        del self._store[key]
        self._records.pop(key, None)
        self._record_change(entity_type, name, scope or {}, "delete")
        return True

    def _record_change(
        self, entity_type: str, name: str, scope: dict[str, str], operation: str
    ) -> None:
        self._change_id += 1
        self._changes.append(
            ConfigChange(
                id=self._change_id,
                entity_type=entity_type,  # type: ignore[arg-type]
                name=name,
                scope=scope,
                operation=operation,  # type: ignore[arg-type]
                changed_at=datetime.now(UTC),
            )
        )

    # Provider
    async def get_provider(
        self, provider_id: str, scope: dict[str, str] | None = None
    ) -> ProviderConfig | None:
        d = self._get_entity("provider", provider_id, scope)
        return ProviderConfig.model_validate(d) if d else None

    async def list_providers(self, scope: dict[str, str] | None = None) -> list[ProviderConfig]:
        rows = self._list_entities("provider", scope)
        # Filter out global defaults entry (which has no 'id' field)
        return [ProviderConfig.model_validate(r) for r in rows if "id" in r]

    async def upsert_provider(self, config: ProviderConfig) -> None:
        self._upsert("provider", config.id, config.scope, config.model_dump(), config.source)

    async def delete_provider(self, provider_id: str, scope: dict[str, str] | None = None) -> bool:
        return self._delete("provider", provider_id, scope)

    # Tool
    async def get_tool(
        self, name: str, scope: dict[str, str] | None = None
    ) -> ToolRegistration | None:
        d = self._get_entity("tool", name, scope)
        return ToolRegistration.model_validate(d) if d else None

    async def list_tools(self, scope: dict[str, str] | None = None) -> list[ToolRegistration]:
        return [ToolRegistration.model_validate(r) for r in self._list_entities("tool", scope)]

    async def upsert_tool(self, tool: ToolRegistration) -> None:
        self._upsert("tool", tool.name, tool.scope, tool.model_dump(), tool.source)

    async def delete_tool(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return self._delete("tool", name, scope)

    # Skill
    async def get_skill(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SkillDefinition | None:
        d = self._get_entity("skill", name, scope)
        return SkillDefinition.model_validate(d) if d else None

    async def list_skills(self, scope: dict[str, str] | None = None) -> list[SkillDefinition]:
        return [SkillDefinition.model_validate(r) for r in self._list_entities("skill", scope)]

    async def upsert_skill(self, skill: SkillDefinition) -> None:
        self._upsert("skill", skill.name, skill.scope, skill.model_dump(), skill.source)

    async def delete_skill(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return self._delete("skill", name, scope)

    # Agent CRUD
    async def upsert_agent(
        self,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "api",
        expected_revision: int | None = None,
        create_only: bool = False,
    ) -> AgentConfigRecord:
        key = self._key("agent", name, scope)
        existing = self._records.get(key)
        if create_only and existing is not None:
            raise ConfigRevisionConflictError("Entity already exists at this scope")
        current_revision = existing.revision if existing else None
        if expected_revision is not None and current_revision != expected_revision:
            raise ConfigRevisionConflictError(
                f"Expected revision {expected_revision}, found {current_revision}"
            )
        validated = AgentDefinition.model_validate(definition).model_dump(mode="json")
        return self._upsert("agent", name, scope, validated, source)

    async def get_agent_record(
        self, name: str, scope: dict[str, str] | None = None
    ) -> AgentConfigRecord | None:
        record = self._records.get(self._key("agent", name, scope or {}))
        return record.model_copy(deep=True) if record else None

    async def get_agent_raw(
        self, name: str, scope: dict[str, str] | None = None
    ) -> dict[str, Any] | None:
        record = await self.get_agent_record(name, scope)
        return dict(record.definition) if record else None

    async def get_agent_raw_with_scope(
        self, name: str, scope: dict[str, str] | None = None
    ) -> tuple[dict[str, Any], dict[str, str]] | None:
        record = await self.get_agent_record(name, scope)
        return (dict(record.definition), dict(record.scope)) if record else None

    async def list_agents(
        self,
        scope: dict[str, str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AgentDefinition]:
        exact_scope = scope or {}
        rows = sorted(
            [
                (_name, record.definition)
                for (entity_type, _name, scope_json), record in self._records.items()
                if entity_type == "agent" and _scope_from_json(scope_json) == exact_scope
            ],
            key=lambda item: item[0],
        )
        page = rows[offset:] if limit is None else rows[offset : offset + limit]
        return [
            AgentDefinition.model_validate(definition)
            for _name, definition in page
        ]

    async def delete_agent(
        self,
        name: str,
        scope: dict[str, str] | None = None,
        expected_revision: int | None = None,
    ) -> bool:
        record = await self.get_agent_record(name, scope)
        if expected_revision is not None and (
            record is None or record.revision != expected_revision
        ):
            raise ConfigRevisionConflictError(
                f"Expected revision {expected_revision} was not current"
            )
        return self._delete("agent", name, scope)

    # MCP
    async def list_mcp_servers(
        self, scope: dict[str, str] | None = None
    ) -> list[McpServerRegistration]:
        return [
            McpServerRegistration.model_validate(r)
            for r in self._list_entities("mcp_server", scope)
        ]

    async def upsert_mcp_server(self, server: McpServerRegistration) -> None:
        self._upsert("mcp_server", server.name, server.scope, server.model_dump(), server.source)

    async def delete_mcp_server(self, name: str, scope: dict[str, str] | None = None) -> bool:
        return self._delete("mcp_server", name, scope)

    # Sandbox profile
    async def get_sandbox_profile(
        self, name: str, scope: dict[str, str] | None = None
    ) -> SandboxProfile | None:
        d = self._get_entity("sandbox_profile", name, scope)
        return SandboxProfile.model_validate(d) if d else None

    async def list_sandbox_profiles(
        self, scope: dict[str, str] | None = None
    ) -> list[SandboxProfile]:
        return [
            SandboxProfile.model_validate(r)
            for r in self._list_entities("sandbox_profile", scope)
        ]

    async def upsert_sandbox_profile(self, profile: SandboxProfile) -> None:
        self._upsert(
            "sandbox_profile",
            profile.name,
            profile.scope,
            profile.model_dump(),
            profile.source,
        )

    async def delete_sandbox_profile(
        self, name: str, scope: dict[str, str] | None = None
    ) -> bool:
        return self._delete("sandbox_profile", name, scope)

    # Global defaults
    async def get_global_provider_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalProviderDefaults:
        d = self._get_entity("provider", _GLOBAL_PROVIDER_NAME, scope)
        return GlobalProviderDefaults.model_validate(d) if d else GlobalProviderDefaults()

    async def set_global_provider_defaults(
        self, defaults: GlobalProviderDefaults, scope: dict[str, str] | None = None
    ) -> None:
        self._upsert("provider", _GLOBAL_PROVIDER_NAME, scope or {}, defaults.model_dump(), "api")

    async def get_global_agent_defaults(
        self, scope: dict[str, str] | None = None
    ) -> GlobalAgentDefaults:
        d = self._get_entity("agent", _GLOBAL_AGENT_NAME, scope)
        return GlobalAgentDefaults.model_validate(d) if d else GlobalAgentDefaults()

    async def set_global_agent_defaults(
        self, defaults: GlobalAgentDefaults, scope: dict[str, str] | None = None
    ) -> None:
        self._upsert("agent", _GLOBAL_AGENT_NAME, scope or {}, defaults.model_dump(), "api")

    async def seed_if_absent(
        self,
        entity_type: EntityType,
        name: str,
        scope: dict[str, str],
        definition: dict[str, Any],
        source: str = "file",
    ) -> bool:
        key = self._key(entity_type, name, scope)
        if key in self._store:
            return False
        self._store[key] = definition
        self._records[key] = AgentConfigRecord(
            name=name,
            scope=scope,
            scope_key=effective_scope_key(scope),
            definition=definition,
            revision=1,
            definition_digest=canonical_json_digest(definition),
            source="file" if source == "file" else "api",
        )
        self._record_change(entity_type, name, scope, "upsert")
        return True

    async def get_changes_since(self, since: datetime) -> list[ConfigChange]:
        return [c for c in self._changes if c.changed_at > since]

    async def mark_changes_processed(self, change_ids: list[int]) -> None:
        pass  # No-op for in-memory


__all__ = [
    "ConfigRevisionConflictError",
    "ConfigRegistry",
    "MemoryConfigRegistry",
    "PostgresConfigRegistry",
    "SqliteConfigRegistry",
]
