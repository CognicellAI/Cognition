"""Alembic environment configuration.

Configures Alembic to work with both SQLite and PostgreSQL backends
based on the application settings.
"""

from __future__ import annotations

import asyncio
import os
import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

# Import application settings
from server.app.settings import get_settings

# Alembic Config object
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Add your model's MetaData object here for 'autogenerate' support
# target_metadata = None
# We don't use SQLAlchemy models directly, so we define tables manually in migrations

target_metadata = None


def get_database_url() -> str:
    """Get database URL from application settings.

    Supports both SQLite and PostgreSQL backends.
    """
    settings = get_settings()

    # Check for explicit database URL override
    db_url = os.environ.get("COGNITION_DATABASE_URL")
    if db_url:
        return db_url

    # Construct URL from settings
    backend = getattr(settings, "persistence_backend", "sqlite")
    uri = getattr(settings, "persistence_uri", ".cognition/state.db")

    if backend == "postgres":
        # Use the connection string as-is for PostgreSQL
        return uri
    elif backend == "sqlite":
        # Convert relative path to absolute for SQLite
        db_path = Path(uri)
        if not db_path.is_absolute():
            db_path = settings.workspace_path / uri
        return f"sqlite:///{db_path}"
    else:
        # Default to SQLite for memory backend during migrations
        db_path = settings.workspace_path / ".cognition" / "state.db"
        return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well. By skipping the Engine creation
    we don't even need a DBAPI to be available.
    """
    url = get_database_url()

    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations using the provided connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Run migrations in 'online' mode using async engine."""
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = get_database_url()

    connectable = async_engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
