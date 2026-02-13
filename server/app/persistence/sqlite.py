"""SQLite persistence provider.

Uses AsyncSqliteSaver for local, file-based persistence.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from server.app.persistence.base import PersistenceBackend


class SqliteBackend(PersistenceBackend):
    """SQLite-based persistence backend."""

    def __init__(self, connection_string: str = ".cognition/state.db"):
        """Initialize SQLite backend.

        Args:
            connection_string: Path to the SQLite database file.
        """
        self.connection_string = connection_string
        self._context_manager: Any = None
        self._checkpointer: AsyncSqliteSaver | None = None

    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get the SQLite checkpointer.

        Ensures directory exists before connecting.
        """
        if self._checkpointer:
            return self._checkpointer

        # Ensure directory exists
        db_path = Path(self.connection_string)
        if not db_path.is_absolute():
            # If relative, make sure parent dir exists relative to CWD
            # This handles the default .cognition/state.db case
            Path(self.connection_string).parent.mkdir(parents=True, exist_ok=True)
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)

        self._context_manager = AsyncSqliteSaver.from_conn_string(self.connection_string)
        self._checkpointer = await self._context_manager.__aenter__()

        return self._checkpointer

    async def close(self) -> None:
        """Close the database connection."""
        if self._context_manager:
            await self._context_manager.__aexit__(None, None, None)
            self._context_manager = None
            self._checkpointer = None
