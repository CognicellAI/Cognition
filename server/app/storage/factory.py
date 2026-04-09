"""Storage backend factory.

Creates appropriate storage backend instances based on configuration.
Supports SQLite, PostgreSQL, and Memory backends.
Also creates the matching ConfigRegistry and ConfigChangeDispatcher.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from server.app.exceptions import CognitionError, ErrorCode

if TYPE_CHECKING:
    from server.app.settings import Settings
    from server.app.storage.backend import StorageBackend
    from server.app.storage.config_dispatcher import ConfigChangeDispatcher
    from server.app.storage.config_registry import ConfigRegistry


class StorageBackendError(CognitionError):
    """Error related to storage backend initialization."""

    def __init__(self, message: str, backend_type: str):
        super().__init__(
            message=message,
            code=ErrorCode.INTERNAL_ERROR,
            details={"backend_type": backend_type},
        )


def create_storage_backend(settings: Settings) -> StorageBackend:
    """Create storage backend based on settings.

    Args:
        settings: Application settings containing persistence configuration.

    Returns:
        Configured StorageBackend instance.

    Raises:
        StorageBackendError: If backend type is unknown or initialization fails.

    Example:
        >>> settings = get_settings()
        >>> backend = create_storage_backend(settings)
        >>> await backend.initialize()
    """
    backend_type = getattr(settings, "persistence_backend", "sqlite")
    uri = getattr(settings, "persistence_uri", ".cognition/state.db")
    workspace_path = str(settings.workspace_path)

    if backend_type == "sqlite":
        from server.app.storage.sqlite import SqliteStorageBackend

        return SqliteStorageBackend(
            connection_string=uri,
            workspace_path=workspace_path,
        )

    elif backend_type == "postgres":
        from server.app.storage.postgres import PostgresStorageBackend

        return PostgresStorageBackend(
            connection_string=uri,
            workspace_path=workspace_path,
        )

    elif backend_type == "memory":
        from server.app.storage.memory import MemoryStorageBackend

        return MemoryStorageBackend(workspace_path=workspace_path)

    else:
        # Raise error for unknown backend types - NO silent fallback
        raise StorageBackendError(
            f"Unknown storage backend type: '{backend_type}'. "
            f"Supported types: sqlite, postgres, memory",
            backend_type=backend_type,
        )


def create_config_registry(settings: Settings) -> ConfigRegistry:
    """Create the ConfigRegistry matching the persistence backend.

    Args:
        settings: Application settings.

    Returns:
        Configured ConfigRegistry instance.

    Raises:
        StorageBackendError: If backend type is unknown.
    """
    backend_type = getattr(settings, "persistence_backend", "sqlite")
    uri = getattr(settings, "persistence_uri", ".cognition/state.db")
    workspace_path = str(settings.workspace_path)

    if backend_type == "sqlite":
        from server.app.storage.config_registry import SqliteConfigRegistry

        # Resolve DB path the same way SqliteStorageBackend does
        normalized_uri = uri.removeprefix("sqlite:///")
        db_path = Path(normalized_uri)
        if not db_path.is_absolute():
            db_path = Path(workspace_path) / normalized_uri
        db_path.parent.mkdir(parents=True, exist_ok=True)
        registry = SqliteConfigRegistry(db_path=str(db_path))
        return registry

    elif backend_type == "postgres":
        from server.app.storage.config_registry import PostgresConfigRegistry

        # asyncpg expects "postgresql://" not "postgresql+asyncpg://"
        asyncpg_dsn = uri.replace("postgresql+asyncpg://", "postgresql://", 1)
        return PostgresConfigRegistry(dsn=asyncpg_dsn)

    elif backend_type == "memory":
        from server.app.storage.config_registry import MemoryConfigRegistry

        return MemoryConfigRegistry()

    else:
        raise StorageBackendError(
            f"Unknown storage backend type: '{backend_type}'. "
            f"Supported types: sqlite, postgres, memory",
            backend_type=backend_type,
        )


def create_config_dispatcher(settings: Settings) -> ConfigChangeDispatcher:
    """Create the ConfigChangeDispatcher matching the persistence backend.

    SQLite → InProcessDispatcher (zero-latency, same process)
    Postgres → PostgresListenDispatcher (LISTEN/NOTIFY, cross-instance)
    Memory → InProcessDispatcher

    Args:
        settings: Application settings.

    Returns:
        Configured ConfigChangeDispatcher instance.
    """
    backend_type = getattr(settings, "persistence_backend", "sqlite")
    uri = getattr(settings, "persistence_uri", ".cognition/state.db")

    if backend_type == "postgres":
        from server.app.storage.config_dispatcher import PostgresListenDispatcher

        # asyncpg.connect() expects a plain "postgresql://" DSN; strip the
        # SQLAlchemy driver qualifier (e.g. "postgresql+asyncpg://") if present.
        asyncpg_dsn = uri.replace("postgresql+asyncpg://", "postgresql://", 1)
        return PostgresListenDispatcher(dsn=asyncpg_dsn)

    else:
        # sqlite and memory both use in-process dispatch
        from server.app.storage.config_dispatcher import InProcessDispatcher

        return InProcessDispatcher()
