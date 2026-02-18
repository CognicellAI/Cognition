"""Enrich message model with additional fields.

Revision ID: 002
Revises: 001
Create Date: 2025-02-18 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Add enriched fields to messages table."""
    # Add new columns to messages table
    with op.batch_alter_table("messages") as batch_op:
        batch_op.add_column(sa.Column("tool_calls", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("tool_call_id", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("token_count", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("model_used", sa.Text(), nullable=True))
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
