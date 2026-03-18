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
    Column("message_count", Integer, default=0),
    Column("agent_name", String(100), nullable=False, default="default"),
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
    Column("definition", JSON, nullable=False),
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

# Unique constraint: (entity_type, name, scope) — one row per scoped entity
Index(
    "idx_config_entities_lookup",
    config_entities_table.c.entity_type,
    config_entities_table.c.name,
    config_entities_table.c.scope,
    unique=True,
)
Index("idx_config_entities_type", config_entities_table.c.entity_type)

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
    "config_entities_table",
    "config_changes_table",
    "create_all_tables",
    "drop_all_tables",
    "get_table_names",
    "get_column_names",
]
