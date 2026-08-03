"""Tests for centralized database schema definitions."""

from __future__ import annotations

from sqlalchemy import create_engine

from server.app.storage.schema import (
    get_column_names,
    get_table_names,
    messages_table,
    metadata,
    runtime_tasks_table,
    session_events_table,
    session_runs_table,
    sessions_table,
)


class TestSchemaDefinitions:
    """Test centralized schema definitions."""

    def test_metadata_has_tables(self) -> None:
        """Test that metadata contains expected tables."""
        table_names = get_table_names()

        assert "sessions" in table_names
        assert "messages" in table_names
        assert "config_entities" in table_names
        assert "config_changes" in table_names
        assert "artifacts" in table_names
        assert "session_runs" in table_names
        assert "session_events" in table_names
        assert "runtime_tasks" in table_names
        assert "mcp_oauth_state" in table_names
        assert len(table_names) == 9

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

    def test_session_runs_table_columns(self) -> None:
        """Test session_runs table has expected columns."""
        columns = get_column_names("session_runs")

        expected_columns = [
            "id",
            "session_id",
            "thread_id",
            "task_id",
            "status",
            "effective_scope",
            "idempotency_key",
            "attempt",
            "last_activity_at",
            "trace_id",
            "metadata",
            "created_at",
            "updated_at",
        ]

        for col in expected_columns:
            assert col in columns, f"Missing column: {col}"

    def test_session_events_table_columns(self) -> None:
        """Test session_events table has expected columns."""
        columns = get_column_names("session_events")

        expected_columns = [
            "id",
            "session_id",
            "run_id",
            "task_id",
            "sequence",
            "event_type",
            "visibility",
            "payload",
            "effective_scope",
            "trace_id",
            "span_id",
            "created_at",
        ]

        for col in expected_columns:
            assert col in columns, f"Missing column: {col}"

    def test_runtime_tasks_table_columns(self) -> None:
        """Test runtime_tasks has neutral task identity and scope columns."""
        columns = get_column_names("runtime_tasks")
        expected_columns = {
            "id",
            "context_id",
            "session_id",
            "agent_name",
            "status",
            "effective_scope",
            "scope_key",
            "current_run_id",
            "last_run_id",
            "idempotency_key",
            "status_reason",
            "metadata",
            "created_at",
            "updated_at",
        }
        assert expected_columns.issubset(columns)

    def test_table_access(self) -> None:
        """Test direct table access."""
        assert sessions_table.name == "sessions"
        assert messages_table.name == "messages"
        assert session_runs_table.name == "session_runs"
        assert session_events_table.name == "session_events"
        assert runtime_tasks_table.name == "runtime_tasks"

        # Verify primary keys
        assert "id" in [col.name for col in sessions_table.primary_key.columns]
        assert "id" in [col.name for col in messages_table.primary_key.columns]
        assert "id" in [col.name for col in session_runs_table.primary_key.columns]
        assert "id" in [col.name for col in session_events_table.primary_key.columns]
        assert "id" in [col.name for col in runtime_tasks_table.primary_key.columns]


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
            assert "session_runs" in tables
            assert "session_events" in tables
            assert "runtime_tasks" in tables

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

    def test_v013_scoped_runtime_indexes_exist(self) -> None:
        """v0.13 scoped runtime access paths have composite indexes."""
        session_indexes = {idx.name for idx in sessions_table.indexes}
        task_indexes = {idx.name for idx in runtime_tasks_table.indexes}
        run_indexes = {idx.name for idx in session_runs_table.indexes}
        event_indexes = {idx.name for idx in session_events_table.indexes}

        assert "idx_sessions_scope_page" in session_indexes
        assert "idx_runtime_tasks_scope_page" in task_indexes
        assert "uq_runtime_tasks_idempotency" in task_indexes
        assert "idx_session_runs_scope_session" in run_indexes
        assert "idx_session_events_scope_session_sequence" in event_indexes
