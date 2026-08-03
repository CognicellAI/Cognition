"""FastAPI dependency injection providers for Cognition.

Replaces the old get_*/set_* global singleton pattern with FastAPI's
Depends() system. Route handlers receive their dependencies via function
parameters instead of calling module-level accessors.

Layer: 6 (API & Streaming)

Usage in route handlers:
    @router.get("/sessions")
    async def list_sessions(
        store: ConfigStore = Depends(get_config_store),
        settings: Settings = Depends(get_settings_dep),
    ): ...
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import Depends, Request

from server.app.agent.resolver import RuntimeResolver
from server.app.api.scoping import SessionScope, create_scope_dependency
from server.app.rate_limiter import RateLimiter, get_rate_limiter
from server.app.settings import Settings, get_settings
from server.app.storage.config_store import ConfigStore

if TYPE_CHECKING:
    from server.app.agent.mcp_oauth_flow import McpOAuthFlowCoordinator
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.llm.model_catalog import ModelCatalog
    from server.app.storage.artifact_store import ArtifactStore
    from server.app.storage.backend import StorageBackend
    from server.app.storage.mcp_oauth import McpOAuthStateRepository

# ---------------------------------------------------------------------------
# Global references — set once during lifespan, read via Depends()
# ---------------------------------------------------------------------------

_config_store: ConfigStore | None = None
_runtime_resolver: RuntimeResolver | None = None
_storage_backend: StorageBackend | None = None
_session_agent_manager: SessionAgentManager | None = None
_model_catalog: ModelCatalog | None = None
_artifact_store: ArtifactStore | None = None
_mcp_oauth_state_repository: McpOAuthStateRepository | None = None
_mcp_oauth_flow_coordinator: McpOAuthFlowCoordinator | None = None


def set_config_store(store: ConfigStore) -> None:
    global _config_store
    _config_store = store


def set_runtime_resolver(resolver: RuntimeResolver) -> None:
    global _runtime_resolver
    _runtime_resolver = resolver


def set_storage_backend_dep(backend: StorageBackend) -> None:
    global _storage_backend
    _storage_backend = backend


def set_session_agent_manager_dep(manager: SessionAgentManager) -> None:
    global _session_agent_manager
    _session_agent_manager = manager


def set_model_catalog_dep(catalog: ModelCatalog) -> None:
    global _model_catalog
    _model_catalog = catalog


def set_artifact_store(store: ArtifactStore) -> None:
    global _artifact_store
    _artifact_store = store


def set_mcp_oauth_state_repository(repository: McpOAuthStateRepository) -> None:
    global _mcp_oauth_state_repository
    _mcp_oauth_state_repository = repository


def set_mcp_oauth_flow_coordinator(coordinator: McpOAuthFlowCoordinator) -> None:
    global _mcp_oauth_flow_coordinator
    _mcp_oauth_flow_coordinator = coordinator


# ---------------------------------------------------------------------------
# FastAPI dependency providers
# ---------------------------------------------------------------------------


def get_settings_dep() -> Settings:
    return get_settings()


def get_config_store() -> ConfigStore:
    if _config_store is None:
        raise RuntimeError("ConfigStore not initialized. Call set_config_store() during startup.")
    return _config_store


def get_runtime_resolver() -> RuntimeResolver:
    if _runtime_resolver is None:
        raise RuntimeError(
            "RuntimeResolver not initialized. Call set_runtime_resolver() during startup."
        )
    return _runtime_resolver


def get_storage_backend_dep() -> StorageBackend:
    if _storage_backend is None:
        raise RuntimeError(
            "StorageBackend not initialized. Call set_storage_backend_dep() during startup."
        )
    return _storage_backend


def get_session_agent_manager_dep() -> SessionAgentManager:
    if _session_agent_manager is None:
        raise RuntimeError(
            "SessionAgentManager not initialized. Call set_session_agent_manager_dep() during startup."
        )
    return _session_agent_manager


def get_model_catalog_dep() -> ModelCatalog:
    if _model_catalog is None:
        raise RuntimeError(
            "ModelCatalog not initialized. Call set_model_catalog_dep() during startup."
        )
    return _model_catalog


def get_artifact_store() -> ArtifactStore:
    if _artifact_store is None:
        raise RuntimeError(
            "ArtifactStore not initialized. Call set_artifact_store() during startup."
        )
    return _artifact_store


def get_mcp_oauth_state_repository() -> McpOAuthStateRepository:
    if _mcp_oauth_state_repository is None:
        raise RuntimeError("MCP OAuth state repository not initialized during startup.")
    return _mcp_oauth_state_repository


def get_mcp_oauth_flow_coordinator() -> McpOAuthFlowCoordinator:
    if _mcp_oauth_flow_coordinator is None:
        raise RuntimeError("MCP OAuth flow coordinator not initialized during startup.")
    return _mcp_oauth_flow_coordinator


def get_rate_limiter_dep() -> RateLimiter:
    return get_rate_limiter()


def get_scope_dep(
    request: Request,
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
) -> SessionScope:
    headers: dict[str, str | None] = {}
    for key in settings.scope_keys:
        header_name = f"x-cognition-scope-{key.replace('_', '-')}"
        headers[key] = request.headers.get(header_name)
    return create_scope_dependency(settings)(**headers)


__all__ = [
    "ConfigStore",
    "RuntimeResolver",
    "get_artifact_store",
    "get_mcp_oauth_state_repository",
    "get_mcp_oauth_flow_coordinator",
    "get_config_store",
    "get_model_catalog_dep",
    "get_rate_limiter_dep",
    "get_runtime_resolver",
    "get_scope_dep",
    "get_session_agent_manager_dep",
    "get_settings_dep",
    "get_storage_backend_dep",
    "set_artifact_store",
    "set_config_store",
    "set_model_catalog_dep",
    "set_runtime_resolver",
    "set_session_agent_manager_dep",
    "set_storage_backend_dep",
    "set_mcp_oauth_state_repository",
    "set_mcp_oauth_flow_coordinator",
]
