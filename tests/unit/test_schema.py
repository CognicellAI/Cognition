"""Tests for centralized database schema definitions."""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

from server.app.storage.schema import (
    create_all_tables,
    drop_all_tables,
    get_column_names,
    get_table_names,
    messages_table,
    metadata,
    sessions_table,
)


class TestSchemaDefinitions:
    """Test centralized schema definitions."""

    def test_metadata_has_tables(self) -> None:
        """Test that metadata contains expected tables."""
        table_names = get_table_names()

        assert "sessions" in table_names
        assert "messages" in table_names
        assert len(table_names) == 2

    def test_sessions_table_columns(self) -> None:
        """Test sessions table has expected columns."""
        columns = get_column_names("sessions")

        expected_columns = [
            "id",
            "workspace_path",
            "title",
            "thread_id",
            "status",
            "config",
            "scopes",
            "message_count",
            "created_at",
            "updated_at",
        ]

        for col in expected_columns:
            assert col in columns, f"Missing column: {col}"

    def test_messages_table_columns(self) -> None:
        """Test messages table has expected columns."""
        columns = get_column_names("messages")

        expected_columns = [
            "id",
            "session_id",
            "role",
            "content",
            "parent_id",
            "tool_calls",
            "tool_call_id",
            "token_count",
            "model_used",
            "metadata",
            "created_at",
        ]

        for col in expected_columns:
            assert col in columns, f"Missing column: {col}"

    def test_table_access(self) -> None:
        """Test direct table access."""
        assert sessions_table.name == "sessions"
        assert messages_table.name == "messages"

        # Verify primary keys
        assert "id" in [col.name for col in sessions_table.primary_key.columns]
        assert "id" in [col.name for col in messages_table.primary_key.columns]


class TestSchemaCreation:
    """Test schema creation with SQLAlchemy engines."""

    def test_create_tables_sqlite(self) -> None:
        """Test creating tables in SQLite."""
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # Create sync engine for SQLite
            engine = create_engine(f"sqlite:///{db_path}")

            # Create all tables
            metadata.create_all(engine)

            # Verify tables exist using SQLAlchemy's inspector
            from sqlalchemy import inspect

            inspector = inspect(engine)
            tables = inspector.get_table_names()

            assert "sessions" in tables
            assert "messages" in tables

            engine.dispose()

        finally:
            import os

            os.unlink(db_path)


class TestSchemaIndexes:
    """Test schema indexes are defined correctly."""

    def test_sessions_workspace_index(self) -> None:
        """Test index on sessions.workspace_path exists."""
        indexes = [idx.name for idx in sessions_table.indexes]
        assert "idx_sessions_workspace" in indexes

    def test_messages_session_index(self) -> None:
        """Test index on messages.session_id exists."""
        indexes = [idx.name for idx in messages_table.indexes]
        assert "idx_messages_session" in indexes
