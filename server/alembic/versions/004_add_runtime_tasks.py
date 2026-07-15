"""Add protocol-neutral runtime tasks and task correlation.

Revision ID: 004
Revises: 003
Create Date: 2026-07-15 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: str | None = "003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _index_names(table_name: str) -> set[str]:
    inspector = sa.inspect(op.get_bind())
    return {
        name
        for index in inspector.get_indexes(table_name)
        if isinstance((name := index["name"]), str)
    }


def upgrade() -> None:
    """Create durable tasks and correlate runs/events to task identity."""
    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "runtime_tasks" not in tables:
        op.create_table(
            "runtime_tasks",
            sa.Column("id", sa.String(length=36), primary_key=True),
            sa.Column("context_id", sa.String(length=100), nullable=False),
            sa.Column("session_id", sa.String(length=36), nullable=False),
            sa.Column("agent_name", sa.String(length=200), nullable=False),
            sa.Column("status", sa.String(length=30), nullable=False),
            sa.Column("effective_scope", sa.JSON(), nullable=False),
            sa.Column("scope_key", sa.String(length=64), nullable=False),
            sa.Column("current_run_id", sa.String(length=36)),
            sa.Column("last_run_id", sa.String(length=36)),
            sa.Column("idempotency_key", sa.String(length=200)),
            sa.Column("status_reason", sa.Text()),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )

    indexes = _index_names("runtime_tasks")
    if "idx_runtime_tasks_scope" not in indexes:
        op.create_index("idx_runtime_tasks_scope", "runtime_tasks", ["agent_name", "scope_key"])
    if "idx_runtime_tasks_context" not in indexes:
        op.create_index("idx_runtime_tasks_context", "runtime_tasks", ["context_id", "created_at"])
    if "idx_runtime_tasks_status" not in indexes:
        op.create_index("idx_runtime_tasks_status", "runtime_tasks", ["status", "updated_at"])
    if "uq_runtime_tasks_idempotency" not in indexes:
        op.create_index(
            "uq_runtime_tasks_idempotency",
            "runtime_tasks",
            ["agent_name", "scope_key", "idempotency_key"],
            unique=True,
        )

    run_columns = {column["name"] for column in inspector.get_columns("session_runs")}
    if "task_id" not in run_columns:
        op.add_column("session_runs", sa.Column("task_id", sa.String(length=36)))
    run_indexes = _index_names("session_runs")
    if "idx_session_runs_task" not in run_indexes:
        op.create_index("idx_session_runs_task", "session_runs", ["task_id", "created_at"])

    event_columns = {column["name"] for column in inspector.get_columns("session_events")}
    if "task_id" not in event_columns:
        op.add_column("session_events", sa.Column("task_id", sa.String(length=36)))
    event_indexes = _index_names("session_events")
    if "idx_session_events_task_sequence" not in event_indexes:
        op.create_index(
            "idx_session_events_task_sequence",
            "session_events",
            ["task_id", "sequence"],
        )


def downgrade() -> None:
    """Remove runtime task correlation and the task aggregate."""
    event_indexes = _index_names("session_events")
    if "idx_session_events_task_sequence" in event_indexes:
        op.drop_index("idx_session_events_task_sequence", table_name="session_events")
    event_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("session_events")
    }
    if "task_id" in event_columns:
        op.drop_column("session_events", "task_id")

    run_indexes = _index_names("session_runs")
    if "idx_session_runs_task" in run_indexes:
        op.drop_index("idx_session_runs_task", table_name="session_runs")
    run_columns = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("session_runs")
    }
    if "task_id" in run_columns:
        op.drop_column("session_runs", "task_id")

    if "runtime_tasks" in sa.inspect(op.get_bind()).get_table_names():
        op.drop_table("runtime_tasks")
