"""In-memory persistence provider (default/testing)."""

from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import MemorySaver

from server.app.persistence.base import PersistenceBackend


class MemoryBackend(PersistenceBackend):
    """In-memory persistence backend."""

    def __init__(self):
        """Initialize memory backend."""
        self._checkpointer = MemorySaver()

    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get the memory checkpointer."""
        return self._checkpointer

    async def close(self) -> None:
        """No-op for memory saver."""
        pass
