"""Initial schema for sessions and messages.

Uses centralized schema definitions from server.app.storage.schema.

Revision ID: 001
Revises:
Create Date: 2025-02-18 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# Import centralized schema
from server.app.storage.schema import metadata

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create initial tables from centralized schema."""
    # Use SQLAlchemy's create_all to generate DDL for the target database
    # This ensures consistency across SQLite, PostgreSQL, etc.
    metadata.create_all(op.get_bind())

    # Note: checkpoints and checkpoint_writes tables are managed by LangGraph
    # and are created separately by the checkpointer implementations


def downgrade() -> None:
    """Drop all tables defined in schema."""
    metadata.drop_all(op.get_bind())
