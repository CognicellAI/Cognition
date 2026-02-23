"""Unified storage backend module.

Provides a protocol-based storage layer that supports multiple backends
(SQLite, PostgreSQL) with consistent interfaces for sessions, messages,
and checkpoint persistence.
"""

from __future__ import annotations

from typing import Optional

from server.app.storage.backend import (
    CheckpointerStore,
    MessageStore,
    SessionStore,
    StorageBackend,
)
from server.app.storage.factory import create_storage_backend

# Global storage backend instance (initialized in main.py lifespan)
_storage_backend: StorageBackend | None = None


def get_storage_backend() -> StorageBackend:
    """Get the global storage backend instance.

    Must be initialized by calling initialize_storage_backend() first.

    Returns:
        Configured StorageBackend instance.

    Raises:
        RuntimeError: If storage backend has not been initialized.
    """
    if _storage_backend is None:
        raise RuntimeError(
            "Storage backend not initialized. Call initialize_storage_backend() first."
        )
    return _storage_backend


def set_storage_backend(backend: StorageBackend) -> None:
    """Set the global storage backend instance.

    Args:
        backend: Configured StorageBackend instance.
    """
    global _storage_backend
    _storage_backend = backend


__all__ = [
    "StorageBackend",
    "SessionStore",
    "MessageStore",
    "CheckpointerStore",
    "create_storage_backend",
    "get_storage_backend",
    "set_storage_backend",
]
