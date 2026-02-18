"""SQLite message storage.

Stores message metadata in the same SQLite database as sessions and LangGraph checkpoints.
Replaces the old in-memory dict-based storage.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Optional

import aiosqlite
import structlog

from server.app.models import Message

logger = structlog.get_logger(__name__)


class SqliteMessageStore:
    """SQLite-based message storage per workspace."""

    def __init__(self, workspace_path: str, db_path: str = ".cognition/state.db"):
        """Initialize store for a workspace.

        Args:
            workspace_path: Absolute path to the workspace directory.
            db_path: Path to the database file relative to workspace.
        """
        self.workspace_path = Path(workspace_path).resolve()
        self.db_path = self.workspace_path / db_path
        self._ensure_storage()

    def _ensure_storage(self) -> None:
        """Ensure the storage directory exists."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    async def _init_db(self) -> None:
        """Initialize the database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT,
                    parent_id TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (session_id) REFERENCES sessions(id) ON DELETE CASCADE
                )
                """
            )
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session 
                ON messages(session_id, created_at)
                """
            )
            await db.commit()

    async def create_message(
        self,
        message_id: str,
        session_id: str,
        role: str,
        content: Optional[str],
        parent_id: Optional[str] = None,
    ) -> Message:
        """Create a new message."""
        await self._init_db()
        now = datetime.utcnow().isoformat()

        message = Message(
            id=message_id,
            session_id=session_id,
            role=role,  # type: ignore
            content=content,
            parent_id=parent_id,
            created_at=datetime.fromisoformat(now),
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO messages (id, session_id, role, content, parent_id, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.parent_id,
                    now,
                ),
            )
            await db.commit()

        logger.debug(
            "Message created (SQLite)",
            message_id=message_id,
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return message

    async def get_message(self, message_id: str) -> Optional[Message]:
        """Get a message by ID."""
        await self._init_db()
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return Message(
                        id=row["id"],
                        session_id=row["session_id"],
                        role=row["role"],  # type: ignore
                        content=row["content"],
                        parent_id=row["parent_id"],
                        created_at=datetime.fromisoformat(row["created_at"]),
                    )
        return None

    async def get_messages_by_session(
        self, session_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[Message], int]:
        """Get messages for a session with pagination.

        Returns:
            Tuple of (paginated messages, total count)
        """
        await self._init_db()
        messages = []

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row

            # Get total count
            async with db.execute(
                "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                total = row[0] if row else 0

            # Get paginated messages
            async with db.execute(
                """
                SELECT * FROM messages 
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ? OFFSET ?
                """,
                (session_id, limit, offset),
            ) as cursor:
                async for row in cursor:
                    messages.append(
                        Message(
                            id=row["id"],
                            session_id=row["session_id"],
                            role=row["role"],  # type: ignore
                            content=row["content"],
                            parent_id=row["parent_id"],
                            created_at=datetime.fromisoformat(row["created_at"]),
                        )
                    )

        return messages, total

    async def list_messages_for_session(self, session_id: str) -> list[Message]:
        """List all messages for a session (no pagination)."""
        messages, _ = await self.get_messages_by_session(session_id, limit=-1, offset=0)
        return messages

    async def delete_messages_for_session(self, session_id: str) -> int:
        """Delete all messages for a session. Returns count deleted."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM messages WHERE session_id = ?",
                (session_id,),
            )
            await db.commit()
            deleted = cursor.rowcount

            if deleted > 0:
                logger.info(
                    "Messages deleted for session (SQLite)",
                    session_id=session_id,
                    count=deleted,
                    workspace=str(self.workspace_path),
                )
            return deleted


# Global cache of stores per workspace path
_message_store_cache: dict[str, SqliteMessageStore] = {}


def get_message_store(workspace_path: str) -> SqliteMessageStore:
    """Get or create a message store for a workspace.

    Args:
        workspace_path: Absolute path to the workspace.

    Returns:
        SqliteMessageStore for the workspace.
    """
    resolved = str(Path(workspace_path).resolve())

    if resolved not in _message_store_cache:
        _message_store_cache[resolved] = SqliteMessageStore(resolved)

    return _message_store_cache[resolved]
