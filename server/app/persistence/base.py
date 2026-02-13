"""Base interface for persistence providers.

Defines the protocol for creating LangGraph checkpointers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver


class PersistenceBackend(ABC):
    """Abstract base class for persistence backends."""

    @abstractmethod
    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get or create a checkpointer instance.

        Returns:
            Configured checkpoint saver ready for use.
        """
        pass

    @abstractmethod
    async def close(self) -> None:
        """Close any open connections."""
        pass
