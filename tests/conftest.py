"""Pytest configuration and shared fixtures."""

import tempfile
from pathlib import Path

import pytest

from server.app.settings import Settings
from server.app.storage import set_storage_backend
from server.app.storage.sqlite import SqliteStorageBackend


@pytest.fixture(autouse=True)
async def setup_storage_backend():
    """Automatically set up storage backend for all tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        # Create and initialize storage backend
        storage = SqliteStorageBackend(
            connection_string=f"{tmpdir}/test.db",
            workspace_path=tmpdir,
        )
        await storage.initialize()

        # Set as global storage backend
        set_storage_backend(storage)

        yield storage

        # Cleanup
        await storage.close()


@pytest.fixture
def temp_settings():
    """Create temporary settings for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        settings = Settings(
            workspace_path=Path(tmpdir),
            llm_provider="mock",
        )
        yield settings
