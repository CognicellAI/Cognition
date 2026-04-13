"""Pytest configuration and shared fixtures."""

import tempfile
from pathlib import Path

import pytest

from server.app.settings import Settings, get_settings
from server.app.storage.sqlite import SqliteStorageBackend


@pytest.fixture(autouse=True)
async def setup_storage_backend():
    """Automatically set up storage backend and DI providers for all tests."""
    from server.app.agent.agent_definition_registry import AgentDefinitionRegistry
    from server.app.api.dependencies import (
        set_config_store,
        set_session_agent_manager_dep,
        set_storage_backend_dep,
    )
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.storage.config_registry import MemoryConfigRegistry
    from server.app.storage.config_store import DefaultConfigStore

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SqliteStorageBackend(
            connection_string=f"{tmpdir}/test.db",
            workspace_path=tmpdir,
        )
        await storage.initialize()

        set_storage_backend_dep(storage)

        settings = get_settings()
        set_session_agent_manager_dep(SessionAgentManager(settings))

        config_reg = MemoryConfigRegistry()
        agent_def_reg = AgentDefinitionRegistry()
        set_config_store(DefaultConfigStore(config_reg, agent_def_reg))

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
