"""Add exact-scope identity and pinned runtime manifests.

Revision ID: 005
Revises: 004
Create Date: 2026-07-23 00:00:00.000000
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: str | None = "004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_EMPTY_DIGEST = hashlib.sha256(b"{}").hexdigest()


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return {}
    return value if value is not None else {}


def _digest(value: Any) -> str:
    canonical = json.dumps(
        _json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _columns(table_name: str) -> set[str]:
    return {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns(table_name)
    }


def _indexes(table_name: str) -> set[str]:
    return {
        str(index["name"])
        for index in sa.inspect(op.get_bind()).get_indexes(table_name)
        if index.get("name")
    }


def _add_scope_key(table_name: str, scope_column: str) -> None:
    if "scope_key" not in _columns(table_name):
        op.add_column(
            table_name,
            sa.Column(
                "scope_key",
                sa.String(length=64),
                nullable=False,
                server_default=_EMPTY_DIGEST,
            ),
        )
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(f"SELECT id, {scope_column} AS scope FROM {table_name}")
    ).mappings()
    for row in rows:
        connection.execute(
            sa.text(f"UPDATE {table_name} SET scope_key=:scope_key WHERE id=:id"),
            {"scope_key": _digest(row["scope"]), "id": row["id"]},
        )


def _create_index(
    name: str,
    table_name: str,
    columns: list[str],
    *,
    unique: bool = False,
) -> None:
    if name not in _indexes(table_name):
        op.create_index(name, table_name, columns, unique=unique)


def upgrade() -> None:
    """Backfill exact-scope hashes and Agent/runtime revision identity."""
    _add_scope_key("sessions", "scopes")
    _add_scope_key("session_runs", "effective_scope")
    _add_scope_key("session_events", "effective_scope")
    _add_scope_key("artifacts", "scope")
    _add_scope_key("config_entities", "scope")
    _add_scope_key("config_changes", "scope")

    config_columns = _columns("config_entities")
    if "revision" not in config_columns:
        op.add_column(
            "config_entities",
            sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        )
    if "definition_digest" not in config_columns:
        op.add_column(
            "config_entities",
            sa.Column(
                "definition_digest",
                sa.String(length=64),
                nullable=False,
                server_default=_EMPTY_DIGEST,
            ),
        )
    connection = op.get_bind()
    config_rows = connection.execute(
        sa.text("SELECT id, definition FROM config_entities")
    ).mappings()
    for row in config_rows:
        connection.execute(
            sa.text(
                """
                UPDATE config_entities
                SET revision=COALESCE(revision, 1), definition_digest=:digest
                WHERE id=:id
                """
            ),
            {"digest": _digest(row["definition"]), "id": row["id"]},
        )

    run_columns = _columns("session_runs")
    if "agent_revision" not in run_columns:
        op.add_column(
            "session_runs",
            sa.Column("agent_revision", sa.Integer(), nullable=False, server_default="1"),
        )
    if "runtime_manifest" not in run_columns:
        op.add_column(
            "session_runs",
            sa.Column(
                "runtime_manifest",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'{}'"),
            ),
        )
    if "manifest_digest" not in run_columns:
        op.add_column(
            "session_runs",
            sa.Column(
                "manifest_digest",
                sa.String(length=64),
                nullable=False,
                server_default=_EMPTY_DIGEST,
            ),
        )

    for table_name, index_name in (
        ("config_entities", "idx_config_entities_lookup"),
        ("artifacts", "idx_artifacts_lookup"),
    ):
        if index_name in _indexes(table_name):
            op.drop_index(index_name, table_name=table_name)

    _create_index(
        "idx_config_entities_lookup",
        "config_entities",
        ["entity_type", "name", "scope_key"],
        unique=True,
    )
    _create_index(
        "idx_config_entities_scope_list",
        "config_entities",
        ["entity_type", "scope_key", "name"],
    )
    _create_index(
        "idx_sessions_scope_page",
        "sessions",
        ["scope_key", "updated_at", "id"],
    )
    _create_index(
        "idx_session_runs_scope_session",
        "session_runs",
        ["scope_key", "session_id", "created_at"],
    )
    _create_index(
        "idx_session_events_scope_session_sequence",
        "session_events",
        ["scope_key", "session_id", "sequence"],
    )
    _create_index(
        "idx_artifacts_lookup",
        "artifacts",
        ["id", "scope_key", "version"],
        unique=True,
    )
    _create_index(
        "idx_artifacts_scope_page",
        "artifacts",
        ["scope_key", "updated_at", "id"],
    )


def downgrade() -> None:
    """Remove v0.13 exact-scope and manifest columns."""
    for table_name, index_name in (
        ("config_entities", "idx_config_entities_scope_list"),
        ("sessions", "idx_sessions_scope_page"),
        ("session_runs", "idx_session_runs_scope_session"),
        ("session_events", "idx_session_events_scope_session_sequence"),
        ("artifacts", "idx_artifacts_scope_page"),
    ):
        if index_name in _indexes(table_name):
            op.drop_index(index_name, table_name=table_name)

    for table_name, columns in (
        ("session_runs", ("manifest_digest", "runtime_manifest", "agent_revision")),
        ("config_entities", ("definition_digest", "revision")),
        ("config_changes", ("scope_key",)),
        ("config_entities", ("scope_key",)),
        ("artifacts", ("scope_key",)),
        ("session_events", ("scope_key",)),
        ("session_runs", ("scope_key",)),
        ("sessions", ("scope_key",)),
    ):
        existing = _columns(table_name)
        with op.batch_alter_table(table_name) as batch:
            for column in columns:
                if column in existing:
                    batch.drop_column(column)
