"""Unified storage backend module.

Provides a protocol-based storage layer that supports multiple backends
(SQLite, PostgreSQL) with consistent interfaces for sessions, messages,
and checkpoint persistence.
"""

from __future__ import annotations

from server.app.storage.backend import (
    CheckpointerStore,
    MessageStore,
    SessionStore,
    StorageBackend,
)
from server.app.storage.factory import create_storage_backend

__all__ = [
    "StorageBackend",
    "SessionStore",
    "MessageStore",
    "CheckpointerStore",
    "create_storage_backend",
]
