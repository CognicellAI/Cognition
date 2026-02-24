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

from sqlalchemy import (
    JSON,
    Column,
    DateTime,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.sql import func

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
    "create_all_tables",
    "drop_all_tables",
    "get_table_names",
    "get_column_names",
]
