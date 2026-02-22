"""PostgreSQL storage backend implementation.

Implements the unified StorageBackend protocol using PostgreSQL as the
database engine with asyncpg for async operations. Supports connection
pooling for high-performance concurrent access.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Optional

import asyncpg
import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from server.app.models import Message, Session, SessionConfig, SessionStatus, ToolCall
from server.app.storage.backend import StorageBackend

logger = structlog.get_logger(__name__)


class PostgresStorageBackend:
    """PostgreSQL-based unified storage backend.

    Implements all StorageBackend operations using PostgreSQL with
    connection pooling for efficient concurrent access.
    """

    def __init__(
        self,
        connection_string: str,
        workspace_path: str = ".",
        min_pool_size: int = 1,
        max_pool_size: int = 10,
    ):
        """Initialize PostgreSQL storage backend.

        Args:
            connection_string: PostgreSQL connection string.
                Format: postgresql://user:password@host:port/database
            workspace_path: Absolute path to the workspace directory.
            min_pool_size: Minimum number of connections in the pool.
            max_pool_size: Maximum number of connections in the pool.
        """
        self.connection_string = connection_string
        self.workspace_path = Path(workspace_path).resolve()
        self.min_pool_size = min_pool_size
        self.max_pool_size = max_pool_size

        # Connection pool
        self._pool: Optional[asyncpg.Pool] = None

        # Checkpointer state
        self._checkpointer: Optional[AsyncPostgresSaver] = None

        logger.debug(
            "PostgresStorageBackend initialized",
            workspace=str(self.workspace_path),
            min_pool=min_pool_size,
            max_pool=max_pool_size,
        )

    async def initialize(self) -> None:
        """Initialize the database schema."""
        # Create connection pool
        self._pool = await asyncpg.create_pool(
            self.connection_string,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
            command_timeout=60,
        )

        # Create tables
        async with self._pool.acquire() as conn:
            # Sessions table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS sessions (
                    id TEXT PRIMARY KEY,
                    workspace_path TEXT NOT NULL,
                    title TEXT,
                    thread_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config JSONB NOT NULL,
                    scopes JSONB DEFAULT '{}',
                    message_count INTEGER DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            # Messages table
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS messages (
                    id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT,
                    parent_id TEXT,
                    tool_calls JSONB,
                    tool_call_id TEXT,
                    token_count INTEGER,
                    model_used TEXT,
                    metadata JSONB,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            # Indexes
            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_messages_session 
                ON messages(session_id, created_at)
                """
            )

            await conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sessions_workspace 
                ON sessions(workspace_path)
                """
            )

        logger.info(
            "PostgreSQL storage initialized",
            workspace=str(self.workspace_path),
        )

    async def close(self) -> None:
        """Close all connections."""
        await self.close_checkpointer()

        if self._pool:
            await self._pool.close()
            self._pool = None

        logger.debug("PostgreSQL storage closed")

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
        now = datetime.now(UTC)

        session = Session(
            id=session_id,
            workspace_path=str(self.workspace_path),
            title=title,
            thread_id=thread_id,
            status=SessionStatus.ACTIVE,
            config=config,
            scopes=scopes or {},
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
            message_count=0,
        )

        config_json = {
            "provider": config.provider,
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "system_prompt": config.system_prompt,
        }

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (
                    id, workspace_path, title, thread_id, status,
                    scopes, config, message_count, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                """,
                session.id,
                session.workspace_path,
                session.title,
                session.thread_id,
                session.status.value,
                json.dumps(session.scopes),
                json.dumps(config_json),
                session.message_count,
                now,
                now,
            )

        logger.info(
            "Session created",
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE id = $1",
                session_id,
            )
            if row:
                return self._row_to_session(row)
        return None

    async def list_sessions(self, filter_scopes: Optional[dict[str, str]] = None) -> list[Session]:
        """List all sessions."""
        sessions = []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM sessions ORDER BY updated_at DESC")
            for row in rows:
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
        config: Optional[SessionConfig] = None,
    ) -> Optional[Session]:
        """Update a session."""
        session = await self.get_session(session_id)
        if not session:
            return None

        updates = []
        params = []
        param_idx = 1

        if title is not None:
            updates.append(f"title = ${param_idx}")
            params.append(title)
            param_idx += 1
            session.title = title

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

            config_json = {
                "provider": new_config.provider,
                "model": new_config.model,
                "temperature": new_config.temperature,
                "max_tokens": new_config.max_tokens,
                "system_prompt": new_config.system_prompt,
            }
            updates.append(f"config = ${param_idx}")
            params.append(json.dumps(config_json))
            param_idx += 1
            session.config = new_config

        if not updates:
            return session

        updates.append(f"updated_at = ${param_idx}")
        now = datetime.now(UTC)
        params.append(now)
        param_idx += 1

        params.append(session_id)

        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} WHERE id = ${param_idx}",
                *params,
            )

        session.updated_at = now.isoformat()
        return session

    async def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session."""
        now = datetime.now(UTC)
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sessions 
                SET message_count = $1, updated_at = $2 
                WHERE id = $3
                """,
                count,
                now,
                session_id,
            )

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM sessions WHERE id = $1",
                session_id,
            )
            # asyncpg returns "DELETE <count>" for DELETE operations
            deleted_count = int(result.split()[-1])
            if deleted_count > 0:
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
        now = datetime.now(UTC)

        message = Message(
            id=message_id,
            session_id=session_id,
            role=role,
            content=content,
            parent_id=parent_id,
            created_at=now,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            token_count=token_count,
            model_used=model_used,
            metadata=metadata,
        )

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO messages (id, session_id, role, content, parent_id, created_at, tool_calls, tool_call_id, token_count, model_used, metadata)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                message.id,
                message.session_id,
                message.role,
                message.content,
                message.parent_id,
                now,
                json.dumps([{"name": tc.name, "args": tc.args, "id": tc.id} for tc in tool_calls])
                if tool_calls
                else None,
                message.tool_call_id,
                message.token_count,
                message.model_used,
                json.dumps(metadata) if metadata else None,
            )

        logger.debug(
            "Message created",
            message_id=message_id,
            session_id=session_id,
        )

        return message

    async def get_message(self, message_id: str) -> Optional[Message]:
        """Get a message by ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM messages WHERE id = $1",
                message_id,
            )
            if row:
                return self._row_to_message(row)
        return None

    async def get_messages_by_session(
        self, session_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[Message], int]:
        """Get messages for a session with pagination."""
        async with self._pool.acquire() as conn:
            # Get total count
            total_row = await conn.fetchrow(
                "SELECT COUNT(*) FROM messages WHERE session_id = $1",
                session_id,
            )
            total = total_row[0]

            # Get paginated messages
            # Handle limit=-1 (no limit) by using a large number
            query_limit = total if limit < 0 else limit
            rows = await conn.fetch(
                """
                SELECT * FROM messages 
                WHERE session_id = $1
                ORDER BY created_at ASC
                LIMIT $2 OFFSET $3
                """,
                session_id,
                query_limit,
                offset,
            )

            messages = [self._row_to_message(row) for row in rows]

        return messages, total

    async def list_messages_for_session(self, session_id: str) -> list[Message]:
        """List all messages for a session."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM messages 
                WHERE session_id = $1
                ORDER BY created_at ASC
                """,
                session_id,
            )
            return [self._row_to_message(row) for row in rows]

    async def delete_messages_for_session(self, session_id: str) -> int:
        """Delete all messages for a session."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM messages WHERE session_id = $1",
                session_id,
            )
            deleted_count = int(result.split()[-1])

            if deleted_count > 0:
                logger.info(
                    "Messages deleted for session",
                    session_id=session_id,
                    count=deleted_count,
                )
            return deleted_count

    # Checkpointer operations
    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get the PostgreSQL checkpointer."""
        if self._checkpointer:
            return self._checkpointer

        # AsyncPostgresSaver now uses from_conn_string class method
        self._checkpointer = await AsyncPostgresSaver.from_conn_string(
            self.connection_string,
        )

        return self._checkpointer

    async def close_checkpointer(self) -> None:
        """Close the checkpointer connection."""
        if self._checkpointer:
            await self._checkpointer.aclose()
            self._checkpointer = None

    # Health check
    async def health_check(self) -> dict[str, Any]:
        """Check backend health status."""
        try:
            if self._pool:
                async with self._pool.acquire() as conn:
                    await conn.fetchval("SELECT 1")
                return {
                    "status": "healthy",
                    "backend": "postgres",
                    "pool_size": self._pool.get_size(),
                    "pool_free": self._pool.get_free_size(),
                }
            else:
                return {
                    "status": "unhealthy",
                    "backend": "postgres",
                    "error": "Pool not initialized",
                }
        except Exception as e:
            return {
                "status": "unhealthy",
                "backend": "postgres",
                "error": str(e),
            }

    # Helper methods
    def _row_to_session(self, row: asyncpg.Record) -> Session:
        """Convert a database row to a Session."""
        config_data = json.loads(row["config"])
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
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
            message_count=row["message_count"],
        )

    def _row_to_message(self, row: asyncpg.Record) -> Message:
        """Convert a database row to a Message."""
        created_at = row["created_at"]
        if isinstance(created_at, datetime):
            created_at = created_at
        else:
            created_at = datetime.fromisoformat(created_at)

        tool_calls_data = row.get("tool_calls")
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

        metadata_data = row.get("metadata")
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
            created_at=created_at,
            tool_calls=tool_calls,
            tool_call_id=row.get("tool_call_id"),
            token_count=row.get("token_count"),
            model_used=row.get("model_used"),
            metadata=metadata,
        )


# Register as implementing the protocol
StorageBackend.register(PostgresStorageBackend)
