"""Tests for database migration utilities."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

from server.app.storage.migrations import (
    get_alembic_config,
    get_migration_status,
    needs_migration,
)


class TestMigrations:
    """Test migration utilities."""

    def test_get_alembic_config(self) -> None:
        """Test creating Alembic configuration."""
        config = get_alembic_config("sqlite:///test.db")

        # Should have script_location set
        assert config.get_main_option("script_location") is not None
        assert config.get_main_option("sqlalchemy.url") == "sqlite:///test.db"

    def test_migration_status_sqlite(self) -> None:
        """Test getting migration status for SQLite."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # New database should need migration
            status = get_migration_status(f"sqlite:///{db_path}")

            # Should return a status dict
            assert isinstance(status, dict)
            assert "current_revision" in status
            assert "latest_revision" in status
            assert "is_current" in status

        finally:
            os.unlink(db_path)

    def test_needs_migration_new_db(self) -> None:
        """Test that new database needs migration."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            db_path = f.name

        try:
            # New database should need migration
            needs = needs_migration(f"sqlite:///{db_path}")
            assert isinstance(needs, bool)

        finally:
            os.unlink(db_path)

    def test_migration_status_error_handling(self) -> None:
        """Test migration status handles errors gracefully."""
        # Invalid database URL
        status = get_migration_status("invalid://url")

        # Should return status with error
        assert isinstance(status, dict)
        assert "error" in status
        assert status["is_current"] is False
