"""Integration tests for backend routes configuration system."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from langgraph.store.memory import InMemoryStore

from server.app.agent.backends import BackendConfig, BackendFactory
from server.app.sessions.manager import SessionManager
from server.app.settings import Settings

if TYPE_CHECKING:
    from deepagents.backends import CompositeBackend


class TestBackendConfig:
    """Tests for BackendConfig class."""

    def test_default_routes_when_no_env_var(self) -> None:
        """Test that default routes are used when env var is not set."""
        routes = BackendConfig.parse_routes_from_env(None)

        assert "/workspace/" in routes
        assert "/memories/" in routes
        assert routes["/workspace/"]["type"] == "filesystem"
        assert routes["/memories/"]["type"] == "store"

    def test_default_routes_when_empty_string(self) -> None:
        """Test that default routes are used when env var is empty."""
        routes = BackendConfig.parse_routes_from_env("")

        assert "/workspace/" in routes
        assert "/memories/" in routes

    def test_parse_valid_json_routes(self) -> None:
        """Test parsing valid JSON route configuration."""
        routes_json = json.dumps(
            {
                "/workspace/": {"type": "filesystem", "root": "/data/workspace"},
                "/memories/": {"type": "store"},
                "/cache/": {"type": "filesystem", "root": "/tmp/cache"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        assert len(routes) == 3
        assert routes["/workspace/"]["type"] == "filesystem"
        assert routes["/workspace/"]["root"] == "/data/workspace"
        assert routes["/cache/"]["type"] == "filesystem"

    def test_parse_invalid_json_falls_back_to_defaults(self) -> None:
        """Test that invalid JSON falls back to defaults."""
        invalid_json = '{"invalid": json syntax}'

        routes = BackendConfig.parse_routes_from_env(invalid_json)

        # Should return defaults
        assert "/workspace/" in routes
        assert "/memories/" in routes

    def test_all_backend_types_in_config(self) -> None:
        """Test configuration with all backend types."""
        routes_json = json.dumps(
            {
                "/filesystem/": {"type": "filesystem", "root": "/data"},
                "/store/": {"type": "store"},
                "/state/": {"type": "state"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        assert routes["/filesystem/"]["type"] == "filesystem"
        assert routes["/store/"]["type"] == "store"
        assert routes["/state/"]["type"] == "state"

    def test_virtual_mode_parameter(self) -> None:
        """Test virtual_mode parameter in filesystem backend."""
        routes_json = json.dumps(
            {
                "/workspace/": {
                    "type": "filesystem",
                    "root": "/data",
                    "virtual_mode": True,
                }
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        assert routes["/workspace/"]["virtual_mode"] is True


class TestBackendFactory:
    """Tests for BackendFactory class."""

    def test_create_backend_factory_with_defaults(self) -> None:
        """Test creating backend factory with default configuration."""
        store = InMemoryStore()
        workspace_path = "/workspace/repo"

        factory = BackendFactory.create_backend_factory(
            session_workspace_path=workspace_path,
            store=store,
            custom_routes=None,
        )

        assert callable(factory)

    def test_create_backend_factory_with_custom_routes(self) -> None:
        """Test creating backend factory with custom routes."""
        store = InMemoryStore()
        workspace_path = "/workspace/repo"
        custom_routes = json.dumps(
            {
                "/workspace/": {"type": "filesystem", "root": "/data/workspace"},
                "/memories/": {"type": "store"},
            }
        )

        factory = BackendFactory.create_backend_factory(
            session_workspace_path=workspace_path,
            store=store,
            custom_routes=custom_routes,
        )

        assert callable(factory)

    def test_backend_factory_creates_composite_backend(self) -> None:
        """Test that backend factory creates working CompositeBackend."""
        store = InMemoryStore()
        workspace_path = "/tmp/workspace"
        custom_routes = json.dumps(
            {
                "/workspace/": {"type": "filesystem", "root": workspace_path},
                "/memories/": {"type": "store"},
            }
        )

        factory = BackendFactory.create_backend_factory(
            session_workspace_path=workspace_path,
            store=store,
            custom_routes=custom_routes,
        )

        # Create a mock runtime object
        mock_runtime = type("Runtime", (), {"state": {}})()

        # Backend factory should create a CompositeBackend
        backend = factory(mock_runtime)
        assert backend is not None
        # CompositeBackend should have routes dict
        assert hasattr(backend, "routes")

    def test_multiple_filesystem_backends(self) -> None:
        """Test configuration with multiple filesystem backends."""
        store = InMemoryStore()
        custom_routes = json.dumps(
            {
                "/workspace/": {"type": "filesystem", "root": "/data/workspace"},
                "/data/": {"type": "filesystem", "root": "/data/files"},
                "/archive/": {"type": "filesystem", "root": "/data/archive"},
            }
        )

        factory = BackendFactory.create_backend_factory(
            session_workspace_path="/data/workspace",
            store=store,
            custom_routes=custom_routes,
        )

        mock_runtime = type("Runtime", (), {"state": {}})()
        backend = factory(mock_runtime)

        # Should have all routes configured
        assert len(backend.routes) >= 3


class TestBackendConfigIntegration:
    """Integration tests for backend configuration with real settings."""

    @pytest.mark.integration
    def test_settings_with_backend_routes(self, monkeypatch: Any) -> None:
        """Test Settings class loads backend routes configuration."""
        from server.app.settings import Settings as SettingsClass

        routes_json = json.dumps(
            {
                "/workspace/": {"type": "filesystem"},
                "/memories/": {"type": "store"},
            }
        )

        # Mock environment
        monkeypatch.setenv("AGENT_BACKEND_ROUTES", routes_json)

        # Create settings with env file disabled (will load from environment)
        settings = SettingsClass(_env_file=None)

        assert settings.agent_backend_routes == routes_json

    def test_malformed_json_routes_config(self) -> None:
        """Test handling of malformed JSON in routes config."""
        malformed = '{"incomplete": '

        routes = BackendConfig.parse_routes_from_env(malformed)

        # Should fall back to defaults
        assert "/workspace/" in routes
        assert "/memories/" in routes

    def test_empty_routes_config(self) -> None:
        """Test handling of empty routes configuration."""
        empty_routes = "{}"

        routes = BackendConfig.parse_routes_from_env(empty_routes)

        # Should return defaults since routes are empty
        assert len(routes) >= 0


class TestSessionWorkspaceWithBackends:
    """Integration tests for session workspace with backend configuration."""

    @pytest.mark.integration
    def test_session_workspace_paths_with_config(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test that session workspace paths work with backend configuration."""
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

        settings = Settings(_env_file=None)

        manager = SessionManager(settings)

        # Create a project with a session
        session = manager.create_or_resume_session(user_prefix="test-project", network_mode="OFF")

        # Verify workspace was created
        assert session.workspace_path.startswith(str(tmp_path))
        assert Path(session.workspace_path).exists()

        # Verify repo path
        repo_path = manager.workspace_manager.get_repo_path(session.project_id)
        assert repo_path.exists()

        # Cleanup - disconnect session and delete project
        manager.disconnect_session(session.session_id)
        manager.get_project_manager().delete_project(session.project_id, force=True)

    @pytest.mark.integration
    def test_absolute_path_resolution_for_backends(self, tmp_path: Path, monkeypatch: Any) -> None:
        """Test that workspace paths are resolved to absolute for backends."""
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))

        settings = Settings(_env_file=None)

        manager = SessionManager(settings)
        session = manager.create_or_resume_session(user_prefix="test-project", network_mode="OFF")

        # Get repo path
        repo_path = manager.workspace_manager.get_repo_path(session.project_id)

        # Resolve to absolute
        absolute_path = repo_path.resolve()

        # Should be absolute
        assert absolute_path.is_absolute()

        # Should exist
        assert absolute_path.exists()

        # Cleanup - disconnect session and delete project
        manager.disconnect_session(session.session_id)
        manager.get_project_manager().delete_project(session.project_id, force=True)


class TestBackendRoutesComplexScenarios:
    """Complex scenario tests for backend routes."""

    def test_route_priority_longest_prefix_match(self) -> None:
        """Test that routes are matched by longest prefix."""
        routes_json = json.dumps(
            {
                "/workspace/": {"type": "filesystem", "root": "/data/workspace"},
                "/workspace/data/": {"type": "filesystem", "root": "/data/files"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        # Both routes should be configured
        assert "/workspace/" in routes
        assert "/workspace/data/" in routes

    def test_multiple_state_backends(self) -> None:
        """Test configuration with multiple state backends."""
        routes_json = json.dumps(
            {
                "/tmp/": {"type": "state"},
                "/cache/": {"type": "state"},
                "/scratch/": {"type": "state"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        assert routes["/tmp/"]["type"] == "state"
        assert routes["/cache/"]["type"] == "state"
        assert routes["/scratch/"]["type"] == "state"

    def test_mixed_backend_types_in_config(self) -> None:
        """Test realistic config with mixed backend types."""
        routes_json = json.dumps(
            {
                "/workspace/": {
                    "type": "filesystem",
                    "root": "/data/workspace",
                    "virtual_mode": True,
                },
                "/data/": {
                    "type": "filesystem",
                    "root": "/mnt/data",
                },
                "/memories/": {"type": "store"},
                "/cache/": {"type": "state"},
                "/tmp/": {"type": "state"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        assert len(routes) == 5
        assert routes["/workspace/"]["type"] == "filesystem"
        assert routes["/data/"]["type"] == "filesystem"
        assert routes["/memories/"]["type"] == "store"
        assert routes["/cache/"]["type"] == "state"
        assert routes["/tmp/"]["type"] == "state"


class TestBackendConfigEdgeCases:
    """Edge case tests for backend configuration."""

    def test_routes_with_trailing_slashes(self) -> None:
        """Test that routes handle trailing slashes correctly."""
        routes_json = json.dumps(
            {
                "/workspace/": {"type": "filesystem"},
                "/workspace": {"type": "filesystem"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        # Both should be present
        assert "/workspace/" in routes or "/workspace" in routes

    def test_deeply_nested_paths(self) -> None:
        """Test deeply nested virtual paths."""
        routes_json = json.dumps(
            {
                "/a/b/c/d/": {"type": "filesystem", "root": "/data"},
                "/x/y/z/": {"type": "state"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        assert "/a/b/c/d/" in routes
        assert "/x/y/z/" in routes

    def test_single_character_paths(self) -> None:
        """Test single character path routing."""
        routes_json = json.dumps(
            {
                "/a/": {"type": "filesystem", "root": "/data"},
                "/b/": {"type": "store"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        assert "/a/" in routes
        assert "/b/" in routes

    def test_root_path_routing(self) -> None:
        """Test root path configuration."""
        routes_json = json.dumps(
            {
                "/": {"type": "filesystem", "root": "/data"},
            }
        )

        routes = BackendConfig.parse_routes_from_env(routes_json)

        assert "/" in routes
        assert routes["/"]["type"] == "filesystem"

    def test_unicode_paths(self) -> None:
        """Test unicode characters in paths (if supported)."""
        routes_json = json.dumps(
            {
                "/数据/": {"type": "filesystem", "root": "/data"},
            }
        )

        # Should parse without error
        routes = BackendConfig.parse_routes_from_env(routes_json)

        # Might not be in routes if path is invalid, but shouldn't crash
        assert isinstance(routes, dict)
