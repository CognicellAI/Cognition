"""Persistence factory for creating storage backends.

Handles creating the appropriate backend (sqlite, memory) based on configuration.
"""

from __future__ import annotations

from server.app.persistence.base import PersistenceBackend
from server.app.persistence.memory import MemoryBackend
from server.app.persistence.sqlite import SqliteBackend
from server.app.settings import Settings


def create_persistence_backend(settings: Settings) -> PersistenceBackend:
    """Create persistence backend based on settings.

    Args:
        settings: Application settings.

    Returns:
        Configured PersistenceBackend instance.
    """
    backend_type = getattr(settings, "persistence_backend", "sqlite")

    if backend_type == "sqlite":
        # Get connection string or use default
        uri = getattr(settings, "persistence_uri", ".cognition/state.db")
        return SqliteBackend(connection_string=uri)
    elif backend_type == "memory":
        return MemoryBackend()
    else:
        # Fallback to sqlite if unknown
        return SqliteBackend()
