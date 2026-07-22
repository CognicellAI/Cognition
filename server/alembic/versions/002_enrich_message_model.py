"""Enrich message model with additional fields.

Revision ID: 002
Revises: 001
Create Date: 2025-02-18 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add enriched fields to messages table."""
    existing = {
        column["name"] for column in sa.inspect(op.get_bind()).get_columns("messages")
    }
    with op.batch_alter_table("messages") as batch_op:
        if "tool_calls" not in existing:
            batch_op.add_column(sa.Column("tool_calls", sa.Text(), nullable=True))
        if "tool_call_id" not in existing:
            batch_op.add_column(sa.Column("tool_call_id", sa.Text(), nullable=True))
        if "token_count" not in existing:
            batch_op.add_column(sa.Column("token_count", sa.Integer(), nullable=True))
        if "model_used" not in existing:
            batch_op.add_column(sa.Column("model_used", sa.Text(), nullable=True))
        if "metadata" not in existing:
            batch_op.add_column(sa.Column("metadata", sa.Text(), nullable=True))


def downgrade() -> None:
    """Remove enriched fields from messages table."""
    # Remove new columns from messages table
    with op.batch_alter_table("messages") as batch_op:
        batch_op.drop_column("metadata")
        batch_op.drop_column("model_used")
        batch_op.drop_column("token_count")
        batch_op.drop_column("tool_call_id")
        batch_op.drop_column("tool_calls")
