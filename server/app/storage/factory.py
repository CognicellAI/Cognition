"""Storage backend factory.

Creates appropriate storage backend instances based on configuration.
Supports SQLite, PostgreSQL, and Memory backends.
Also creates the matching ConfigRegistry, ConfigChangeDispatcher,
and ArtifactStore.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from server.app.exceptions import CognitionError, ErrorCode

if TYPE_CHECKING:
    from server.app.settings import Settings
    from server.app.storage.artifact_store import ArtifactStore
    from server.app.storage.backend import StorageBackend
    from server.app.storage.config_dispatcher import ConfigChangeDispatcher
    from server.app.storage.config_registry import ConfigRegistry
    from server.app.storage.mcp_oauth import McpOAuthStateRepository


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


def create_mcp_oauth_state_repository(settings: Settings) -> McpOAuthStateRepository:
    """Create the deployment-matched opaque MCP OAuth state repository."""
    backend_type = getattr(settings, "persistence_backend", "sqlite")
    uri = getattr(settings, "persistence_uri", ".cognition/state.db")

    if backend_type == "memory":
        from server.app.storage.mcp_oauth import MemoryMcpOAuthStateRepository

        return MemoryMcpOAuthStateRepository()

    from sqlalchemy.ext.asyncio import create_async_engine

    from server.app.storage.mcp_oauth import SqlMcpOAuthStateRepository

    if backend_type == "sqlite":
        normalized_uri = uri.removeprefix("sqlite:///")
        db_path = Path(normalized_uri)
        if not db_path.is_absolute():
            db_path = Path(str(settings.workspace_path)) / db_path
        db_path.parent.mkdir(parents=True, exist_ok=True)
        engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}")
        return SqlMcpOAuthStateRepository(engine)

    if backend_type == "postgres":
        sqlalchemy_dsn = uri.replace("postgresql://", "postgresql+asyncpg://", 1)
        engine = create_async_engine(sqlalchemy_dsn)
        return SqlMcpOAuthStateRepository(engine)

    raise StorageBackendError(
        f"Unknown storage backend type: '{backend_type}'. "
        "Supported types: sqlite, postgres, memory",
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


def create_artifact_store(settings: Settings) -> ArtifactStore:
    """Create the ArtifactStore matching the persistence backend.

    Args:
        settings: Application settings.

    Returns:
        Configured ArtifactStore instance.

    Raises:
        StorageBackendError: If backend type is unknown.
    """
    backend_type = getattr(settings, "persistence_backend", "sqlite")
    uri = getattr(settings, "persistence_uri", ".cognition/state.db")
    workspace_path = str(settings.workspace_path)

    if backend_type == "sqlite":
        from server.app.storage.artifact_store import SqliteArtifactStore

        normalized_uri = uri.removeprefix("sqlite:///")
        db_path = Path(normalized_uri)
        if not db_path.is_absolute():
            db_path = Path(workspace_path) / normalized_uri
        db_path.parent.mkdir(parents=True, exist_ok=True)
        store: ArtifactStore = SqliteArtifactStore(db_path=str(db_path))

    elif backend_type == "postgres":
        from server.app.storage.artifact_store import PostgresArtifactStore

        asyncpg_dsn = uri.replace("postgresql+asyncpg://", "postgresql://", 1)
        store = PostgresArtifactStore(dsn=asyncpg_dsn)

    elif backend_type == "memory":
        from server.app.storage.artifact_store import MemoryArtifactStore

        store = MemoryArtifactStore()

    else:
        raise StorageBackendError(
            f"Unknown storage backend type: '{backend_type}'. "
            f"Supported types: sqlite, postgres, memory",
            backend_type=backend_type,
        )

    if not settings.s3_enabled:
        return store

    from server.app.storage.artifact_store import S3ArtifactStore

    if settings.s3_bucket is None or settings.s3_scope_hmac_key is None:
        raise StorageBackendError("S3 artifact storage is missing required configuration", "s3")
    return S3ArtifactStore(
        store,
        bucket=settings.s3_bucket,
        base_prefix=settings.s3_prefix,
        hmac_key=settings.s3_scope_hmac_key.get_secret_value(),
        endpoint_url=settings.s3_endpoint_url,
        region_name=settings.s3_region,
        force_path_style=settings.s3_force_path_style,
    )
