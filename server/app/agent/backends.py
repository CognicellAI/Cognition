"""Backend configuration and routing for Deep Agents virtual filesystem."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, ClassVar

from deepagents.backends import CompositeBackend, FilesystemBackend, StateBackend, StoreBackend

if TYPE_CHECKING:
    from langgraph.store.base import BaseStore

logger = logging.getLogger(__name__)


class BackendConfig:
    """Configuration for CompositeBackend routes.

    Environment variables:
    - AGENT_BACKEND_ROUTES: JSON mapping of virtual paths to backend configs
      Example: '{""/workspace/"": {"type": "filesystem", "root": "/data/workspace"},
                 "/cache/"": {"type": "filesystem", "root": "/data/cache"}}'

    - AGENT_WORKSPACE_PATH: Default workspace path (fallback)
      Example: '/workspaces/{session_id}/repo'

    Route format:
    {
        "/virtual/path/": {
            "type": "filesystem|state|store",
            "root": "/host/path",           # For filesystem backend
            "virtual_mode": true            # For filesystem backend (optional)
        }
    }
    """

    # Default routes if none specified
    DEFAULT_ROUTES: ClassVar[dict[str, dict[str, Any]]] = {
        "/workspace/": {
            "type": "filesystem",
            "virtual_mode": True,
            # root_dir will be set per-session
        },
        "/memories/": {
            "type": "store",
        },
    }

    @staticmethod
    def parse_routes_from_env(env_routes: str | None) -> dict[str, dict[str, Any]]:
        """Parse backend routes from environment variable JSON.

        Args:
            env_routes: JSON string defining routes

        Returns:
            Dictionary of route configurations
        """
        if not env_routes:
            return BackendConfig.DEFAULT_ROUTES

        try:
            routes = json.loads(env_routes)
            logger.info(f"Loaded custom backend routes: {list(routes.keys())}")
            return routes
        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse AGENT_BACKEND_ROUTES, using defaults: {e}")
            return BackendConfig.DEFAULT_ROUTES

    @staticmethod
    def create_composite_backend(
        session_workspace_path: str,
        store: BaseStore,
        env_routes: str | None = None,
    ) -> Any:
        """Create a CompositeBackend with configured routes.

        Args:
            session_workspace_path: Absolute path to session workspace
            store: LangGraph Store for persistent memories
            env_routes: Optional JSON env var with route configuration

        Returns:
            Backend factory function
        """
        routes_config = BackendConfig.parse_routes_from_env(env_routes)
        routes: dict[str, Any] = {}

        for path, config in routes_config.items():
            backend_type = config.get("type", "filesystem")

            if backend_type == "filesystem":
                # Use session workspace path if not specified
                root = config.get("root", session_workspace_path)
                virtual_mode = config.get("virtual_mode", True)

                routes[path] = FilesystemBackend(
                    root_dir=root,
                    virtual_mode=virtual_mode,
                )
                logger.info(
                    f"Configured filesystem backend: {path} -> {root} (virtual_mode={virtual_mode})"
                )

            elif backend_type == "store":
                routes[path] = StoreBackend(store)
                logger.info(f"Configured store backend: {path}")

            elif backend_type == "state":
                # StateBackend requires runtime, handled by CompositeBackend
                routes[path] = "state"
                logger.info(f"Configured state backend: {path}")

            else:
                logger.warning(f"Unknown backend type, skipping: {backend_type} at {path}")

        # Create composite with defaults
        def create_backend(runtime: Any) -> Any:
            # Replace "state" strings with actual StateBackend instances
            resolved_routes = {}
            for path_key, backend in routes.items():
                if backend == "state":
                    resolved_routes[path_key] = StateBackend(runtime)
                else:
                    resolved_routes[path_key] = backend

            return CompositeBackend(
                default=StateBackend(runtime),
                routes=resolved_routes,
            )

        return create_backend


class BackendFactory:
    """Factory for creating configured backends."""

    @staticmethod
    def create_backend_factory(
        session_workspace_path: str,
        store: BaseStore,
        custom_routes: str | None = None,
    ) -> Any:
        """Create a backend factory function for create_deep_agent.

        Args:
            session_workspace_path: Session workspace directory
            store: LangGraph Store instance
            custom_routes: Optional JSON with route configuration

        Returns:
            Backend factory function suitable for create_deep_agent(backend=...)
        """

        def backend_factory(runtime: Any) -> Any:
            # Parse routes from config
            routes_config = BackendConfig.parse_routes_from_env(custom_routes)
            resolved_routes: dict[str, Any] = {}

            for path, config in routes_config.items():
                backend_type = config.get("type", "filesystem")

                if backend_type == "filesystem":
                    root = config.get("root", session_workspace_path)
                    virtual_mode = config.get("virtual_mode", True)
                    resolved_routes[path] = FilesystemBackend(
                        root_dir=root,
                        virtual_mode=virtual_mode,
                    )

                elif backend_type == "store":
                    resolved_routes[path] = StoreBackend(store)

                elif backend_type == "state":
                    resolved_routes[path] = StateBackend(runtime)

            return CompositeBackend(
                default=StateBackend(runtime),
                routes=resolved_routes,
            )

        return backend_factory
