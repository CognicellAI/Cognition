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
        set_mcp_readiness_repository,
        set_session_agent_manager_dep,
        set_storage_backend_dep,
    )
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.storage.artifact_store import MemoryArtifactStore
    from server.app.storage.config_registry import MemoryConfigRegistry
    from server.app.storage.config_store import (
        DefaultConfigStore,
        set_default_config_store,
    )
    from server.app.storage.mcp_readiness import MemoryMcpReadinessRepository

    with tempfile.TemporaryDirectory() as tmpdir:
        storage = SqliteStorageBackend(
            connection_string=f"{tmpdir}/test.db",
            workspace_path=tmpdir,
        )
        await storage.initialize()

        set_storage_backend_dep(storage)
        set_artifact_store(MemoryArtifactStore())
        set_mcp_readiness_repository(MemoryMcpReadinessRepository())

        settings = get_settings()
        previous_runtime_settings = (
            settings.unsafe_local_execution,
            settings.allow_host_tools,
            settings.allow_api_python_tools,
            list(settings.callback_allowed_origins),
        )
        # Existing unit tests intentionally exercise the standalone local runtime.
        # Production defaults remain strict; this fixture opts the test deployment in.
        settings.unsafe_local_execution = True
        settings.allow_host_tools = True
        settings.allow_api_python_tools = True
        settings.callback_allowed_origins = ["https://example.com"]
        set_session_agent_manager_dep(SessionAgentManager(settings))

        config_reg = MemoryConfigRegistry()
        agents_dir = Path(tmpdir) / ".cognition" / "agents"
        agents_dir.mkdir(parents=True)
        for name, prompt in (
            ("default", "Explicitly provisioned shared test Agent."),
            ("readonly", "Explicitly provisioned shared read-only test Agent."),
        ):
            (agents_dir / f"{name}.yaml").write_text(
                f"name: {name}\nsystem_prompt: {prompt}\nmode: primary\n"
            )
        config_store = DefaultConfigStore(
            config_reg,
            workspace_path=Path(tmpdir),
        )
        await config_store.upsert_agent(
            "hidden-agent",
            {},
            {
                "name": "hidden-agent",
                "system_prompt": "Explicitly provisioned hidden test Agent.",
                "mode": "primary",
                "hidden": True,
            },
            "api",
        )
        set_config_store(config_store)
        set_default_config_store(config_store)

        yield storage

        (
            settings.unsafe_local_execution,
            settings.allow_host_tools,
            settings.allow_api_python_tools,
            settings.callback_allowed_origins,
        ) = previous_runtime_settings
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
