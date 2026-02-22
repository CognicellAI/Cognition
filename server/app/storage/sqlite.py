"""SQLite storage backend implementation.

Implements the unified StorageBackend protocol using SQLite as the
database engine. Supports sessions, messages, and checkpoint persistence.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Optional

import aiosqlite
import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from server.app.models import Message, Session, SessionConfig, SessionStatus, ToolCall
from server.app.storage.backend import StorageBackend

logger = structlog.get_logger(__name__)


class SqliteStorageBackend:
    """SQLite-based unified storage backend.

    Implements all StorageBackend operations using a single SQLite database
    for sessions, messages, and LangGraph checkpoints.
    """

    def __init__(
        self,
        connection_string: str = ".cognition/state.db",
        workspace_path: str = ".",
    ):
        """Initialize SQLite storage backend.

        Args:
            connection_string: Path to the SQLite database file.
            workspace_path: Absolute path to the workspace directory.
        """
        self.connection_string = connection_string
        self.workspace_path = Path(workspace_path).resolve()

        # Resolve database path
        db_path = Path(connection_string)
        if not db_path.is_absolute():
            db_path = self.workspace_path / connection_string
        self.db_path = db_path

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Checkpointer state
        self._checkpointer: Optional[AsyncSqliteSaver] = None
        self._checkpointer_context: Optional[Any] = None

        logger.debug(
            "SqliteStorageBackend initialized",
            db_path=str(self.db_path),
            workspace=str(self.workspace_path),
        )

    async def initialize(self) -> None:
        """Initialize the database schema."""
        async with aiosqlite.connect(self.db_path) as db:
            # Sessions table
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workspace_path TEXT NOT NULL,
                    title TEXT,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config TEXT NOT NULL,
                    scopes TEXT DEFAULT '{}',
                    message_count INTEGER DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )

            # Messages table
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

            # Message index for efficient querying
            await db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session 
                ON messages(session_id, created_at)
                """
            )

            await db.commit()

        logger.info(
            "SQLite storage initialized",
            db_path=str(self.db_path),
        )

    async def close(self) -> None:
        """Close all connections."""
        await self.close_checkpointer()
        logger.debug("SQLite storage closed")

    # Session operations
    async def create_session(
        self,
        session_id: str,
        thread_id: str,
        config: SessionConfig,
        title: Optional[str] = None,
        scopes: Optional[dict[str, str]] = None,
    ) -> Session:
        """Create a new session."""
        now = datetime.now(UTC).isoformat()

        session = Session(
            id=session_id,
            workspace_path=str(self.workspace_path),
            title=title,
            thread_id=thread_id,
            status=SessionStatus.ACTIVE,
            config=config,
            created_at=now,
            updated_at=now,
            message_count=0,
            scopes=scopes or {},
        )

        config_json = json.dumps(
            {
                "provider": config.provider,
                "model": config.model,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "system_prompt": config.system_prompt,
            }
        )

        scopes_json = json.dumps(scopes or {})

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (
                    id, workspace_path, title, thread_id, status, 
                    config, scopes, message_count, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.workspace_path,
                    session.title,
                    session.thread_id,
                    session.status.value,
                    config_json,
                    scopes_json,
                    session.message_count,
                    session.created_at,
                    session.updated_at,
                ),
            )
            await db.commit()

        logger.info(
            "Session created",
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_session(row)
        return None

    async def list_sessions(self, filter_scopes: Optional[dict[str, str]] = None) -> list[Session]:
        """List all sessions."""
        sessions = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM sessions ORDER BY updated_at DESC") as cursor:
                async for row in cursor:
                    session = self._row_to_session(row)
                    # Filter by scopes if specified
                    if filter_scopes:
                        if all(session.scopes.get(k) == v for k, v in filter_scopes.items()):
                            sessions.append(session)
                    else:
                        sessions.append(session)
        return sessions

    async def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        status: Optional[str] = None,
        config: Optional[SessionConfig] = None,
    ) -> Optional[Session]:
        """Update a session."""
        session = await self.get_session(session_id)
        if not session:
            return None

        updates = []
        params = []

        if title is not None:
            updates.append("title = ?")
            params.append(title)
            session.title = title

        if status is not None:
            updates.append("status = ?")
            params.append(status)
            session.status = SessionStatus(status)

        if config is not None:
            existing_config = session.config
            new_config = SessionConfig(
                provider=config.provider or existing_config.provider,
                model=config.model or existing_config.model,
                temperature=config.temperature
                if config.temperature is not None
                else existing_config.temperature,
                max_tokens=config.max_tokens
                if config.max_tokens is not None
                else existing_config.max_tokens,
                system_prompt=config.system_prompt
                if config.system_prompt is not None
                else existing_config.system_prompt,
            )

            config_json = json.dumps(
                {
                    "provider": new_config.provider,
                    "model": new_config.model,
                    "temperature": new_config.temperature,
                    "max_tokens": new_config.max_tokens,
                    "system_prompt": new_config.system_prompt,
                }
            )
            updates.append("config = ?")
            params.append(config_json)
            session.config = new_config

        if not updates:
            return session

        updates.append("updated_at = ?")
        now = datetime.now(UTC).isoformat()
        params.append(now)
        session.updated_at = now

        params.append(session_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()

        return session

    async def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session."""
        now = datetime.now(UTC).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE sessions SET message_count = ?, updated_at = ? WHERE id = ?",
                (count, now, session_id),
            )
            await db.commit()

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            await db.commit()
            if cursor.rowcount > 0:
                logger.info(
                    "Session deleted",
                    session_id=session_id,
                    workspace=str(self.workspace_path),
                )
                return True
        return False

    # Message operations
    async def create_message(
        self,
        message_id: str,
        session_id: str,
        role: Literal["user", "assistant", "system", "tool"],
        content: Optional[str],
        parent_id: Optional[str] = None,
        tool_calls: Optional[list[ToolCall]] = None,
        tool_call_id: Optional[str] = None,
        token_count: Optional[int] = None,
        model_used: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Message:
        """Create a new message."""
        now = datetime.now(UTC).isoformat()

        message = Message(
            id=message_id,
            session_id=session_id,
            role=role,
            content=content,
            parent_id=parent_id,
            created_at=datetime.fromisoformat(now),
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            token_count=token_count,
            model_used=model_used,
            metadata=metadata,
        )

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO messages (id, session_id, role, content, parent_id, created_at, tool_calls, tool_call_id, token_count, model_used, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.id,
                    message.session_id,
                    message.role,
                    message.content,
                    message.parent_id,
                    now,
                    json.dumps(
                        [{"name": tc.name, "args": tc.args, "id": tc.id} for tc in tool_calls]
                    )
                    if tool_calls
                    else None,
                    message.tool_call_id,
                    message.token_count,
                    message.model_used,
                    json.dumps(metadata) if metadata else None,
                ),
            )
            await db.commit()

        logger.debug(
            "Message created",
            message_id=message_id,
            session_id=session_id,
        )

        return message

    async def get_message(self, message_id: str) -> Optional[Message]:
        """Get a message by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM messages WHERE id = ?", (message_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_message(row)
        return None

    async def get_messages_by_session(
        self, session_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[Message], int]:
        """Get messages for a session with pagination."""
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
            # Handle limit=-1 (no limit) by using total count
            query_limit = total if limit < 0 else limit
            async with db.execute(
                """
                SELECT * FROM messages 
                WHERE session_id = ?
                ORDER BY created_at ASC
                LIMIT ? OFFSET ?
                """,
                (session_id, query_limit, offset),
            ) as cursor:
                async for row in cursor:
                    messages.append(self._row_to_message(row))

        return messages, total

    async def list_messages_for_session(self, session_id: str) -> list[Message]:
        """List all messages for a session."""
        messages = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM messages 
                WHERE session_id = ?
                ORDER BY created_at ASC
                """,
                (session_id,),
            ) as cursor:
                async for row in cursor:
                    messages.append(self._row_to_message(row))
        return messages

    async def delete_messages_for_session(self, session_id: str) -> int:
        """Delete all messages for a session."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM messages WHERE session_id = ?",
                (session_id,),
            )
            await db.commit()
            deleted = cursor.rowcount

            if deleted > 0:
                logger.info(
                    "Messages deleted for session",
                    session_id=session_id,
                    count=deleted,
                )
            return deleted

    # Checkpointer operations
    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get the SQLite checkpointer."""
        if self._checkpointer:
            return self._checkpointer

        self._checkpointer_context = AsyncSqliteSaver.from_conn_string(str(self.db_path))
        self._checkpointer = await self._checkpointer_context.__aenter__()

        return self._checkpointer

    async def close_checkpointer(self) -> None:
        """Close the checkpointer connection."""
        if self._checkpointer_context:
            await self._checkpointer_context.__aexit__(None, None, None)
            self._checkpointer_context = None
            self._checkpointer = None

    # Health check
    async def health_check(self) -> dict[str, Any]:
        """Check backend health status."""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute("SELECT 1")
            return {
                "status": "healthy",
                "backend": "sqlite",
                "path": str(self.db_path),
            }
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "sqlite",
                "path": str(self.db_path),
                "error": str(e),
            }

    # Helper methods
    def _row_to_session(self, row: aiosqlite.Row) -> Session:
        """Convert a database row to a Session."""
        config_data = json.loads(row["config"])
        scopes_data = json.loads(row["scopes"]) if row["scopes"] else {}
        return Session(
            id=row["id"],
            workspace_path=row["workspace_path"],
            title=row["title"],
            thread_id=row["thread_id"],
            status=SessionStatus(row["status"]),
            config=SessionConfig(
                provider=config_data.get("provider"),
                model=config_data.get("model"),
                temperature=config_data.get("temperature"),
                max_tokens=config_data.get("max_tokens"),
                system_prompt=config_data.get("system_prompt"),
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=row["message_count"],
            scopes=scopes_data,
        )

    def _row_to_message(self, row: aiosqlite.Row) -> Message:
        """Convert a database row to a Message."""
        tool_calls_data = row["tool_calls"]
        tool_calls = None
        if tool_calls_data:
            try:
                tc_list = json.loads(tool_calls_data)
                tool_calls = [
                    ToolCall(name=tc["name"], args=tc.get("args", {}), id=tc["id"])
                    for tc in tc_list
                ]
            except (json.JSONDecodeError, KeyError, TypeError):
                tool_calls = None

        metadata_data = row["metadata"]
        metadata = None
        if metadata_data:
            try:
                metadata = json.loads(metadata_data)
            except json.JSONDecodeError:
                metadata = None

        return Message(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],  # type: ignore
            content=row["content"],
            parent_id=row["parent_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            tool_calls=tool_calls,
            tool_call_id=row["tool_call_id"],
            token_count=row["token_count"],
            model_used=row["model_used"],
            metadata=metadata,
        )


# Register as implementing the protocol
StorageBackend.register(SqliteStorageBackend)
