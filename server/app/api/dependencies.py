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

from fastapi import Depends, Header

from server.app.agent.resolver import RuntimeResolver
from server.app.api.scoping import SessionScope, create_scope_dependency
from server.app.settings import Settings, get_settings
from server.app.storage.config_store import ConfigStore

# ---------------------------------------------------------------------------
# Global references — set once during lifespan, read via Depends()
# ---------------------------------------------------------------------------

_config_store: ConfigStore | None = None
_runtime_resolver: RuntimeResolver | None = None


def set_config_store(store: ConfigStore) -> None:
    global _config_store
    _config_store = store


def set_runtime_resolver(resolver: RuntimeResolver) -> None:
    global _runtime_resolver
    _runtime_resolver = resolver


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


async def get_scope_dep(
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
    user: str | None = Header(None, alias="x-cognition-scope-user"),
    project: str | None = Header(None, alias="x-cognition-scope-project"),
) -> SessionScope:
    return await create_scope_dependency(settings)(user=user, project=project)


__all__ = [
    "ConfigStore",
    "RuntimeResolver",
    "get_config_store",
    "get_runtime_resolver",
    "get_scope_dep",
    "get_settings_dep",
    "set_config_store",
    "set_runtime_resolver",
]
