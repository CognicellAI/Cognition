"""Centralized database schema definitions using SQLAlchemy Core.

This module defines all database tables using SQLAlchemy Core, providing
a single source of truth for the database schema across SQLite, PostgreSQL,
and any future backends.

Usage:
    from server.app.storage.schema import metadata, create_all_tables

    # Create all tables
    await create_all_tables(engine)

    # Or access individual tables
    from server.app.storage.schema import sessions_table, messages_table
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine.interfaces import Dialect
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import func
from sqlalchemy.types import TypeDecorator, TypeEngine


class _JsonbOrJson(TypeDecorator):
    """Emits JSONB on PostgreSQL and plain JSON on all other dialects.

    JSONB is required so that a B-tree UNIQUE index can be created on the
    ``scope`` column in Postgres.  SQLite stores JSON as TEXT and can
    unique-index it without any special type.
    """

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect: Dialect) -> TypeEngine[Any]:
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())

    def process_bind_param(self, value: object, dialect: Dialect) -> object:
        return value

    def process_result_value(self, value: object, dialect: Dialect) -> object:
        return value


# Central metadata object that holds all table definitions
metadata = MetaData()

# Encrypted MCP OAuth SDK state. Partition keys are deployment-keyed digests of
# exact effective scope + Agent identity + canonical server URI; no raw scope or
# OAuth material is stored in indexable columns.
mcp_oauth_state_table = Table(
    "mcp_oauth_state",
    metadata,
    Column("partition_key", String(64), primary_key=True),
    Column("tokens_ciphertext", Text),
    Column("client_info_ciphertext", Text),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
)

# Sessions table - stores conversation session metadata
sessions_table = Table(
    "sessions",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("workspace_path", Text, nullable=False),
    Column("title", Text),
    Column("thread_id", String(36), nullable=False),
    Column("status", String(20), nullable=False),
    Column(
        "config",
        JSON,
        nullable=False,
        default=dict,
    ),
    Column("scopes", JSON, default=dict),
    Column("scope_key", String(64), nullable=False),
    Column("metadata", JSON, default=dict),
    Column("message_count", Integer, default=0),
    Column("agent_name", String(100), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
)

# Index on workspace_path for session listing by workspace
Index("idx_sessions_workspace", sessions_table.c.workspace_path)
Index(
    "idx_sessions_scope_page",
    sessions_table.c.scope_key,
    sessions_table.c.updated_at,
    sessions_table.c.id,
)

# Messages table - stores all conversation messages
messages_table = Table(
    "messages",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "session_id",
        String(36),
        nullable=False,
    ),
    Column("role", String(20), nullable=False),
    Column("content", Text),
    Column("parent_id", String(36)),
    # Enriched fields (P2-5)
    Column("tool_calls", JSON),
    Column("tool_call_id", String(36)),
    Column("token_count", Integer),
    Column("model_used", Text),
    Column("metadata", JSON),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

# Index on session_id + created_at for efficient message retrieval
Index("idx_messages_session", messages_table.c.session_id, messages_table.c.created_at)


# Protocol-neutral durable task. A task may own multiple execution attempts.
runtime_tasks_table = Table(
    "runtime_tasks",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("context_id", String(100), nullable=False),
    Column("session_id", String(36), nullable=False),
    Column("agent_name", String(200), nullable=False),
    Column("status", String(30), nullable=False),
    Column("effective_scope", _JsonbOrJson(), nullable=False, default=dict),
    Column("scope_key", String(64), nullable=False),
    Column("current_run_id", String(36)),
    Column("last_run_id", String(36)),
    Column("idempotency_key", String(200)),
    Column("status_reason", Text),
    Column("metadata", _JsonbOrJson(), nullable=False, default=dict),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
)

Index("idx_runtime_tasks_scope", runtime_tasks_table.c.agent_name, runtime_tasks_table.c.scope_key)
Index(
    "idx_runtime_tasks_scope_page",
    runtime_tasks_table.c.agent_name,
    runtime_tasks_table.c.scope_key,
    runtime_tasks_table.c.created_at,
    runtime_tasks_table.c.id,
)
Index("idx_runtime_tasks_context", runtime_tasks_table.c.context_id, runtime_tasks_table.c.created_at)
Index("idx_runtime_tasks_status", runtime_tasks_table.c.status, runtime_tasks_table.c.updated_at)
Index(
    "uq_runtime_tasks_idempotency",
    runtime_tasks_table.c.agent_name,
    runtime_tasks_table.c.scope_key,
    runtime_tasks_table.c.idempotency_key,
    unique=True,
)


# Durable run table - one execution attempt inside a session.
session_runs_table = Table(
    "session_runs",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", String(36), nullable=False),
    Column("thread_id", String(100), nullable=False),
    Column("task_id", String(36)),
    Column("status", String(30), nullable=False),
    Column("effective_scope", _JsonbOrJson(), nullable=False, default=dict),
    Column("scope_key", String(64), nullable=False),
    Column("agent_revision", Integer, nullable=False),
    Column("runtime_manifest", _JsonbOrJson(), nullable=False, default=dict),
    Column("manifest_digest", String(64), nullable=False),
    Column("idempotency_key", String(200)),
    Column("attempt", Integer, nullable=False, default=1),
    Column("parent_run_id", String(36)),
    Column("started_at", DateTime(timezone=True)),
    Column("last_activity_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    Column("error_code", String(100)),
    Column("status_reason", Text),
    Column("trace_id", String(100)),
    Column("metadata", _JsonbOrJson(), nullable=False, default=dict),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
)

Index("idx_session_runs_session", session_runs_table.c.session_id, session_runs_table.c.created_at)
Index(
    "idx_session_runs_scope_session",
    session_runs_table.c.scope_key,
    session_runs_table.c.session_id,
    session_runs_table.c.created_at,
)
Index("idx_session_runs_status", session_runs_table.c.session_id, session_runs_table.c.status)
Index("idx_session_runs_task", session_runs_table.c.task_id, session_runs_table.c.created_at)
Index(
    "idx_session_runs_idempotency",
    session_runs_table.c.session_id,
    session_runs_table.c.idempotency_key,
)


# Append-only runtime events used by builders and observability integrations.
session_events_table = Table(
    "session_events",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("session_id", String(36), nullable=False),
    Column("run_id", String(36), nullable=False),
    Column("task_id", String(36)),
    Column("sequence", Integer, nullable=False),
    Column("event_type", String(100), nullable=False),
    Column("visibility", String(30), nullable=False),
    Column("payload", _JsonbOrJson(), nullable=False, default=dict),
    Column("effective_scope", _JsonbOrJson(), nullable=False, default=dict),
    Column("scope_key", String(64), nullable=False),
    Column("trace_id", String(100)),
    Column("span_id", String(100)),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    UniqueConstraint("session_id", "sequence", name="uq_session_events_sequence"),
)

Index("idx_session_events_session_sequence", session_events_table.c.session_id, session_events_table.c.sequence)
Index(
    "idx_session_events_scope_session_sequence",
    session_events_table.c.scope_key,
    session_events_table.c.session_id,
    session_events_table.c.sequence,
)
Index("idx_session_events_run_sequence", session_events_table.c.run_id, session_events_table.c.sequence)
Index("idx_session_events_task_sequence", session_events_table.c.task_id, session_events_table.c.sequence)
Index(
    "idx_session_events_session_run_sequence",
    session_events_table.c.session_id,
    session_events_table.c.run_id,
    session_events_table.c.sequence,
)
Index(
    "idx_session_events_visibility_sequence",
    session_events_table.c.session_id,
    session_events_table.c.visibility,
    session_events_table.c.sequence,
)
Index("idx_session_events_type_created", session_events_table.c.event_type, session_events_table.c.created_at)

# ---------------------------------------------------------------------------
# ConfigRegistry tables
# ---------------------------------------------------------------------------

# config_entities — single source of truth for all hot-reloadable config.
# entity_type: "provider" | "tool" | "skill" | "agent" | "mcp_server"
# name:        entity identifier (e.g. "openai-gpt4o", "default")
# scope:       JSON dict of scope key-values (empty = global)
# definition:  JSON blob of the entity's Pydantic model fields
# source:      "file" (bootstrapped) or "api" (written via API)
config_entities_table = Table(
    "config_entities",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entity_type", String(50), nullable=False),
    Column("name", String(200), nullable=False),
    Column("scope", _JsonbOrJson(), nullable=False, default=dict),
    Column("scope_key", String(64), nullable=False),
    Column("definition", JSON, nullable=False),
    Column("revision", Integer, nullable=False, default=1),
    Column("definition_digest", String(64), nullable=False),
    Column("source", String(10), nullable=False, default="file"),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    ),
)

# One row per canonical exact scope. The stored JSON remains the collision
# verification source of truth.
Index(
    "idx_config_entities_lookup",
    config_entities_table.c.entity_type,
    config_entities_table.c.name,
    config_entities_table.c.scope_key,
    unique=True,
)
Index(
    "idx_config_entities_scope_list",
    config_entities_table.c.entity_type,
    config_entities_table.c.scope_key,
    config_entities_table.c.name,
)

# config_changes — append-only changelog used for cache invalidation.
# SQLite: polled by InProcessDispatcher (no-op; changes happen in same process).
# Postgres: NOTIFY "cognition_config_changes" is also sent on every insert.
config_changes_table = Table(
    "config_changes",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("entity_type", String(50), nullable=False),
    Column("name", String(200), nullable=False),
    Column("scope", _JsonbOrJson(), nullable=False, default=dict),
    Column("scope_key", String(64), nullable=False),
    Column("operation", String(10), nullable=False),  # "upsert" | "delete"
    Column(
        "changed_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column("processed", Boolean, nullable=False, default=False),
)

Index("idx_config_changes_changed_at", config_changes_table.c.changed_at)
Index("idx_config_changes_unprocessed", config_changes_table.c.processed)

# ---------------------------------------------------------------------------
# Artifacts table — blob-store through SQL
# ---------------------------------------------------------------------------
# Flat, denormalized key-value rows. Each version is an independent row.
# No foreign keys. run_id, checkpoint_id, artifact_type are tags, not constraints.
# The natural key is (id, scope, version).

artifacts_table = Table(
    "artifacts",
    metadata,
    Column("id", String(200), primary_key=False, nullable=False),
    Column("version", Integer, primary_key=False, nullable=False),
    Column("name", String(200), nullable=False),
    Column("artifact_type", String(50), nullable=False, default="scratch"),
    Column("content", Text, default=""),
    Column("content_type", String(100), default="text/plain"),
    Column("parent_version", Integer),
    Column("run_id", String(100)),
    Column("checkpoint_id", String(100)),
    Column("visibility", String(20), default="private"),
    Column("scope", _JsonbOrJson(), nullable=False, default=dict),
    Column("scope_key", String(64), nullable=False),
    Column("source", String(10), nullable=False, default="api"),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
)

Index(
    "idx_artifacts_lookup",
    artifacts_table.c.id,
    artifacts_table.c.scope_key,
    artifacts_table.c.version,
    unique=True,
)
Index(
    "idx_artifacts_scope_page",
    artifacts_table.c.scope_key,
    artifacts_table.c.updated_at,
    artifacts_table.c.id,
)
Index("idx_artifacts_type", artifacts_table.c.artifact_type)
Index("idx_artifacts_run_id", artifacts_table.c.run_id)
Index("idx_artifacts_name", artifacts_table.c.name)


async def create_all_tables(engine: AsyncEngine) -> None:
    """Create all defined tables in the database.

    Args:
        engine: SQLAlchemy async engine connected to the database.

    Example:
        >>> from sqlalchemy.ext.asyncio import create_async_engine
        >>> engine = create_async_engine("postgresql+asyncpg://...")
        >>> await create_all_tables(engine)
    """
    async with engine.begin() as conn:
        await conn.run_sync(metadata.create_all)


async def drop_all_tables(engine: AsyncEngine) -> None:
    """Drop all defined tables from the database.

    WARNING: This will delete all data. Use with caution, primarily for testing.

    Args:
        engine: SQLAlchemy async engine connected to the database.
    """
    async with engine.begin() as conn:
        await conn.run_sync(metadata.drop_all)


def get_table_names() -> list[str]:
    """Get list of all table names in the schema.

    Returns:
        List of table names.
    """
    return list(metadata.tables.keys())


def get_column_names(table_name: str) -> list[str]:
    """Get list of column names for a specific table.

    Args:
        table_name: Name of the table.

    Returns:
        List of column names.

    Raises:
        KeyError: If table doesn't exist.
    """
    table = metadata.tables[table_name]
    return [col.name for col in table.columns]


# Export all public symbols
__all__ = [
    "metadata",
    "sessions_table",
    "messages_table",
    "runtime_tasks_table",
    "session_runs_table",
    "session_events_table",
    "config_entities_table",
    "config_changes_table",
    "artifacts_table",
    "create_all_tables",
    "drop_all_tables",
    "get_table_names",
    "get_column_names",
]
