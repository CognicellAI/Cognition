"""Add immutable object metadata to durable artifact manifests.

Revision ID: 006
Revises: 005
Create Date: 2026-08-03 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: str | None = "005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add S3 object identity and integrity metadata to artifact rows."""
    existing = {
        str(column["name"])
        for column in sa.inspect(op.get_bind()).get_columns("artifacts")
    }
    with op.batch_alter_table("artifacts") as batch_op:
        if "path" not in existing:
            batch_op.add_column(
                sa.Column("path", sa.String(length=1024), nullable=False, server_default="")
            )
        if "object_key" not in existing:
            batch_op.add_column(sa.Column("object_key", sa.String(length=1024), nullable=True))
        if "content_checksum" not in existing:
            batch_op.add_column(sa.Column("content_checksum", sa.String(length=64), nullable=True))
        if "content_size" not in existing:
            batch_op.add_column(sa.Column("content_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    """Remove durable-object metadata from artifact rows."""
    with op.batch_alter_table("artifacts") as batch_op:
        batch_op.drop_column("content_size")
        batch_op.drop_column("content_checksum")
        batch_op.drop_column("object_key")
        batch_op.drop_column("path")
