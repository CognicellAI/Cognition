"""Durable, exact-scope MCP readiness observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncEngine

from server.app.storage.common import effective_scope_key
from server.app.storage.schema import mcp_readiness_table


class McpReadinessObservation(BaseModel):
    """One redacted MCP discovery observation for a pinned Agent revision."""

    model_config = ConfigDict(frozen=True)

    agent_name: str
    agent_revision: int = Field(ge=1)
    server_alias: str
    required: bool
    status: Literal["ready", "unavailable"]
    tool_count: int = Field(default=0, ge=0)
    schema_digest: str | None = None
    failure_category: str | None = None
    observed_at: datetime
    fresh_until: datetime

    def public_status(self, now: datetime | None = None) -> Literal[
        "ready", "unavailable", "unknown"
    ]:
        """Return ``unknown`` after the observation freshness deadline."""
        current = now or datetime.now(UTC)
        deadline = self.fresh_until
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=UTC)
        return self.status if current <= deadline else "unknown"


class McpReadinessRepository(Protocol):
    """Persistence contract for scoped MCP readiness observations."""

    async def initialize(self) -> None: ...

    async def close(self) -> None: ...

    async def record(
        self, observation: McpReadinessObservation, effective_scope: Mapping[str, str]
    ) -> None: ...

    async def list_for_agent(
        self,
        *,
        agent_name: str,
        agent_revision: int,
        effective_scope: Mapping[str, str],
    ) -> Sequence[McpReadinessObservation]: ...


class MemoryMcpReadinessRepository:
    """Process-local readiness persistence for in-memory development."""

    def __init__(self) -> None:
        self._rows: dict[tuple[str, str, int, str], McpReadinessObservation] = {}

    async def initialize(self) -> None:
        return None

    async def close(self) -> None:
        self._rows.clear()

    async def record(
        self, observation: McpReadinessObservation, effective_scope: Mapping[str, str]
    ) -> None:
        key = (
            effective_scope_key(dict(effective_scope)),
            observation.agent_name,
            observation.agent_revision,
            observation.server_alias,
        )
        self._rows[key] = observation.model_copy(deep=True)

    async def list_for_agent(
        self,
        *,
        agent_name: str,
        agent_revision: int,
        effective_scope: Mapping[str, str],
    ) -> Sequence[McpReadinessObservation]:
        scope_key = effective_scope_key(dict(effective_scope))
        return [
            row.model_copy(deep=True)
            for (row_scope, row_agent, row_revision, _), row in self._rows.items()
            if row_scope == scope_key
            and row_agent == agent_name
            and row_revision == agent_revision
        ]


class SqlMcpReadinessRepository:
    """SQLite/PostgreSQL readiness repository."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def initialize(self) -> None:
        async with self._engine.begin() as connection:
            await connection.run_sync(
                lambda sync_connection: mcp_readiness_table.create(
                    sync_connection, checkfirst=True
                )
            )

    async def close(self) -> None:
        await self._engine.dispose()

    async def record(
        self, observation: McpReadinessObservation, effective_scope: Mapping[str, str]
    ) -> None:
        values = {
            "scope_key": effective_scope_key(dict(effective_scope)),
            **observation.model_dump(),
        }
        dialect = self._engine.dialect.name
        if dialect == "sqlite":
            statement: Any = sqlite_insert(mcp_readiness_table).values(**values)
        elif dialect == "postgresql":
            statement = postgres_insert(mcp_readiness_table).values(**values)
        else:  # pragma: no cover - factory admits supported backends only
            raise RuntimeError("MCP readiness backend is unsupported")
        statement = statement.on_conflict_do_update(
            index_elements=[
                mcp_readiness_table.c.scope_key,
                mcp_readiness_table.c.agent_name,
                mcp_readiness_table.c.agent_revision,
                mcp_readiness_table.c.server_alias,
            ],
            set_={
                "required": observation.required,
                "status": observation.status,
                "tool_count": observation.tool_count,
                "schema_digest": observation.schema_digest,
                "failure_category": observation.failure_category,
                "observed_at": observation.observed_at,
                "fresh_until": observation.fresh_until,
            },
        )
        async with self._engine.begin() as connection:
            await connection.execute(statement)

    async def list_for_agent(
        self,
        *,
        agent_name: str,
        agent_revision: int,
        effective_scope: Mapping[str, str],
    ) -> Sequence[McpReadinessObservation]:
        statement = select(mcp_readiness_table).where(
            mcp_readiness_table.c["scope_key"]
            == effective_scope_key(dict(effective_scope)),
            mcp_readiness_table.c["agent_name"] == agent_name,
            mcp_readiness_table.c["agent_revision"] == agent_revision,
        )
        async with self._engine.connect() as connection:
            rows = (await connection.execute(statement)).mappings().all()
        return [
            McpReadinessObservation.model_validate(
                {key: value for key, value in row.items() if key != "scope_key"}
            )
            for row in rows
        ]


__all__ = [
    "McpReadinessObservation",
    "McpReadinessRepository",
    "MemoryMcpReadinessRepository",
    "SqlMcpReadinessRepository",
]
