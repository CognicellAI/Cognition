"""Storage backend factory.

Creates appropriate storage backend instances based on configuration.
Supports SQLite, PostgreSQL, and Memory backends.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from server.app.exceptions import CognitionError, ErrorCode

if TYPE_CHECKING:
    from server.app.settings import Settings
    from server.app.storage.backend import StorageBackend


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
