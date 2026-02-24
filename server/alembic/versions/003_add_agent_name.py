"""Add agent_name column to sessions table.

Revision ID: 003
Revises: 002
Create Date: 2025-02-24 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add agent_name column to sessions table."""
    op.add_column(
        "sessions",
        sa.Column("agent_name", sa.String(length=100), nullable=False, server_default="default"),
    )


def downgrade() -> None:
    """Remove agent_name column from sessions table."""
    op.drop_column("sessions", "agent_name")
