"""Database migration utilities.

Provides programmatic access to Alembic migrations for auto-migration
on startup. This ensures databases are always up-to-date without
requiring manual intervention.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import Any

import structlog
from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import create_async_engine

logger = structlog.get_logger(__name__)


class MigrationError(Exception):
    """Error during database migration."""

    pass


def get_alembic_config(database_url: str) -> Config:
    """Create Alembic configuration for the given database URL.

    Args:
        database_url: Database connection URL.

    Returns:
        Configured Alembic Config object.
    """
    # Find alembic.ini relative to this file
    # Alembic files are in server/alembic/
    alembic_dir = Path(__file__).parent.parent / "alembic"

    config = Config()
    config.set_main_option("script_location", str(alembic_dir))
    config.set_main_option("sqlalchemy.url", database_url)

    return config


def needs_migration(database_url: str) -> bool:
    """Check if database needs migration.

    Compares the current database revision with the latest
    available migration script.

    Args:
        database_url: Database connection URL.

    Returns:
        True if migration is needed, False otherwise.
    """
    try:
        config = get_alembic_config(database_url)
        script = ScriptDirectory.from_config(config)

        # Create engine to check current revision
        engine = create_engine(database_url)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            current_rev = context.get_current_revision()

        # Get latest available revision
        latest_rev = script.get_current_head()

        logger.debug(
            "Migration check",
            current=current_rev,
            latest=latest_rev,
            needs_migration=current_rev != latest_rev,
        )

        return current_rev != latest_rev

    except Exception as e:
        # If we can't check (e.g., database doesn't exist), assume migration needed
        logger.debug("Could not check migration status, assuming needed", error=str(e))
        return True


def run_migrations_sync(database_url: str) -> None:
    """Run Alembic migrations synchronously.

    Args:
        database_url: Database connection URL.

    Raises:
        MigrationError: If migration fails.
    """
    try:
        logger.info("Running database migrations", database_url=database_url[:50] + "...")

        config = get_alembic_config(database_url)
        command.upgrade(config, "head")

        logger.info("Database migrations completed successfully")

    except Exception as e:
        logger.error("Migration failed", error=str(e))
        raise MigrationError(f"Failed to run migrations: {e}") from e


async def run_migrations_async(database_url: str) -> None:
    """Run Alembic migrations asynchronously.

    This is a wrapper around the synchronous migration function
    that runs in a thread pool to not block the event loop.

    Args:
        database_url: Database connection URL.

    Raises:
        MigrationError: If migration fails.
    """
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, run_migrations_sync, database_url)


def get_migration_status(database_url: str) -> dict[str, Any]:
    """Get current migration status for the database.

    Args:
        database_url: Database connection URL.

    Returns:
        Dictionary with current revision, latest revision, and pending count.
    """
    try:
        config = get_alembic_config(database_url)
        script = ScriptDirectory.from_config(config)

        engine = create_engine(database_url)

        with engine.connect() as connection:
            context = MigrationContext.configure(connection)
            current_rev = context.get_current_revision()

        latest_rev = script.get_current_head()

        # Count pending migrations
        if current_rev is None:
            pending = len(list(script.walk_revisions()))
        else:
            pending = sum(1 for _ in script.walk_revisions(current_rev, latest_rev))

        return {
            "current_revision": current_rev,
            "latest_revision": latest_rev,
            "pending_count": pending,
            "is_current": current_rev == latest_rev,
        }

    except Exception as e:
        logger.error("Failed to get migration status", error=str(e))
        return {
            "current_revision": None,
            "latest_revision": None,
            "pending_count": None,
            "is_current": False,
            "error": str(e),
        }
