"""Pytest configuration and shared fixtures."""

import tempfile
from pathlib import Path

import pytest

from server.app.settings import Settings, get_settings
from server.app.storage.sqlite import SqliteStorageBackend


@pytest.fixture(autouse=True)
def reject_mock_filesystem_paths(monkeypatch: pytest.MonkeyPatch):
    """Fail tests that accidentally use mock representations as real paths."""
    original_mkdir = Path.mkdir

    def guarded_mkdir(
        path: Path,
        mode: int = 0o777,
        parents: bool = False,
        exist_ok: bool = False,
    ) -> None:
        assert "MagicMock" not in str(path), f"Refusing to create mocked path: {path}"
        original_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", guarded_mkdir)


@pytest.fixture(autouse=True)
async def setup_storage_backend():
    """Automatically set up storage backend and DI providers for all tests."""
    from server.app.api.dependencies import (
        set_artifact_store,
        set_config_store,
        set_session_agent_manager_dep,
        set_storage_backend_dep,
    )
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.storage.artifact_store import MemoryArtifactStore
    from server.app.storage.config_registry import MemoryConfigRegistry
    from server.app.storage.config_store import DefaultConfigStore

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SqliteStorageBackend(
            connection_string=f"{tmpdir}/test.db",
            workspace_path=tmpdir,
        )
        await storage.initialize()

        set_storage_backend_dep(storage)
        set_artifact_store(MemoryArtifactStore())

        settings = get_settings()
        set_session_agent_manager_dep(SessionAgentManager(settings))

        config_reg = MemoryConfigRegistry()
        set_config_store(DefaultConfigStore(config_reg))

        yield storage

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
