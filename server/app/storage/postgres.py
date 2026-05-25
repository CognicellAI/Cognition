"""PostgreSQL storage backend implementation.

Implements the unified StorageBackend protocol using PostgreSQL as the
database engine with asyncpg for async operations. Supports connection
pooling for high-performance concurrent access.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import asyncpg
import psycopg
import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.store.base import BaseStore
from langgraph.store.postgres.aio import AsyncPostgresStore
from psycopg_pool import AsyncConnectionPool

from server.app.models import (
    Message,
    RunStatus,
    Session,
    SessionConfig,
    SessionEvent,
    SessionRun,
    SessionStatus,
    ToolCall,
)
from server.app.storage.backend import StorageBackend
from server.app.storage.common import (
    make_message,
    make_session,
    make_session_event,
    make_session_run,
    merge_session_config,
    now_utc,
)
from server.app.storage.message_projection import project_checkpoint_messages

logger = structlog.get_logger(__name__)


def _normalize_sqlalchemy_async_dsn(connection_string: str) -> str:
    """Normalize Postgres DSNs for SQLAlchemy async usage."""
    if connection_string.startswith("postgresql+asyncpg://"):
        return connection_string
    if connection_string.startswith("postgresql://"):
        return connection_string.replace("postgresql://", "postgresql+asyncpg://", 1)
    return connection_string


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
        self._pool: asyncpg.Pool | None = None

        # Checkpointer state
        self._checkpointer: AsyncPostgresSaver | None = None
        self._checkpointer_context: Any | None = None

        # Store state (LangGraph cross-thread memory)
        self._store: AsyncPostgresStore | None = None
        self._store_context: Any | None = None

        logger.debug(
            "PostgresStorageBackend initialized",
            workspace=str(self.workspace_path),
            min_pool=min_pool_size,
            max_pool=max_pool_size,
        )

    async def initialize(self) -> None:
        """Initialize the database schema from centralized schema definitions."""
        from sqlalchemy.ext.asyncio import create_async_engine

        from server.app.storage.schema import metadata

        sqlalchemy_dsn = _normalize_sqlalchemy_async_dsn(self.connection_string)

        # Create async SQLAlchemy engine
        async_engine = create_async_engine(
            sqlalchemy_dsn,
            pool_size=self.min_pool_size,
            max_overflow=self.max_pool_size - self.min_pool_size,
        )

        # Create all tables from centralized schema
        async with async_engine.begin() as conn:
            await conn.run_sync(metadata.create_all)

        # Create connection pool for operations
        # asyncpg expects 'postgresql://' not 'postgresql+asyncpg://'
        asyncpg_dsn = self.connection_string.replace("postgresql+asyncpg://", "postgresql://")
        self._pool = await asyncpg.create_pool(
            asyncpg_dsn,
            min_size=self.min_pool_size,
            max_size=self.max_pool_size,
            command_timeout=60,
        )

        await async_engine.dispose()

        logger.info(
            "PostgreSQL storage initialized",
            workspace=str(self.workspace_path),
        )

        # Create tables
        async with self._pool.acquire() as conn:
            try:
                await conn.execute(
                    "ALTER TABLE sessions ADD COLUMN metadata JSONB DEFAULT '{}'::jsonb"
                )
            except Exception as exc:
                if "already exists" not in str(exc).lower():
                    raise

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
        await self.close_store()

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
        title: str | None = None,
        scopes: dict[str, str] | None = None,
        agent_name: str = "default",
        metadata: dict[str, str] | None = None,
        workspace_path: str | None = None,
    ) -> Session:
        """Create a new session."""
        now = now_utc()

        session = make_session(
            session_id=session_id,
            workspace_path=workspace_path or str(self.workspace_path),
            title=title,
            thread_id=thread_id,
            config=config,
            scopes=scopes,
            agent_name=agent_name,
            metadata=metadata,
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )

        config_json = {
            "provider": config.provider,
            "model": config.model,
            "temperature": config.temperature,
            "max_tokens": config.max_tokens,
            "recursion_limit": config.recursion_limit,
            "response_format": config.response_format,
            "system_prompt": config.system_prompt,
        }

        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO sessions (
                    id, workspace_path, title, thread_id, status,
                    scopes, metadata, config, message_count, agent_name, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                """,
                session.id,
                session.workspace_path,
                session.title,
                session.thread_id,
                session.status.value,
                json.dumps(session.scopes),
                json.dumps(session.metadata),
                json.dumps(config_json),
                session.message_count,
                session.agent_name,
                now,
                now,
            )

        logger.info(
            "Session created",
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return session

    async def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE id = $1",
                session_id,
            )
            if row:
                return self._row_to_session(row)
        return None

    async def list_sessions(
        self,
        filter_scopes: dict[str, str] | None = None,
        metadata_filters: dict[str, str] | None = None,
    ) -> list[Session]:
        """List all sessions."""
        sessions = []
        async with self._pool.acquire() as conn:
            query = "SELECT * FROM sessions"
            params: list[str] = []
            if metadata_filters:
                predicates = []
                for index, (key, value) in enumerate(metadata_filters.items(), start=1):
                    predicates.append(f"metadata->>$${index * 2 - 1} = $${index * 2}")
                    params.extend([key, value])
                query += " WHERE " + " AND ".join(
                    predicate.replace("$$", "$") for predicate in predicates
                )
            query += " ORDER BY updated_at DESC"

            rows = await conn.fetch(query, *params)
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
        title: str | None = None,
        status: str | None = None,
        config: SessionConfig | None = None,
        agent_name: str | None = None,
        metadata: dict[str, str] | None = None,
    ) -> Session | None:
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

        if status is not None:
            updates.append(f"status = ${param_idx}")
            params.append(status)
            param_idx += 1
            session.status = SessionStatus(status)

        if agent_name is not None:
            updates.append(f"agent_name = ${param_idx}")
            params.append(agent_name)
            param_idx += 1
            session.agent_name = agent_name

        if metadata is not None:
            updates.append(f"metadata = ${param_idx}")
            params.append(json.dumps(metadata))
            param_idx += 1
            session.metadata = dict(metadata)

        if config is not None:
            new_config = merge_session_config(session.config, config)

            config_json = {
                "provider": new_config.provider,
                "model": new_config.model,
                "temperature": new_config.temperature,
                "max_tokens": new_config.max_tokens,
                "recursion_limit": new_config.recursion_limit,
                "response_format": new_config.response_format,
                "system_prompt": new_config.system_prompt,
            }
            updates.append(f"config = ${param_idx}")
            params.append(json.dumps(config_json))
            param_idx += 1
            session.config = new_config

        if not updates:
            return session

        updates.append(f"updated_at = ${param_idx}")
        now = now_utc()
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
        now = now_utc()
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
        content: str | None,
        parent_id: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        tool_call_id: str | None = None,
        token_count: int | None = None,
        model_used: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Create a new message."""
        now = now_utc()

        message = make_message(
            message_id=message_id,
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

    async def rebuild_message_projection(
        self,
        session_id: str,
        thread_id: str,
        checkpoint_messages: list[Any],
    ) -> int:
        """Rebuild API message projection from authoritative checkpoint messages."""
        del thread_id

        projected_messages = project_checkpoint_messages(session_id, checkpoint_messages)

        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM messages WHERE session_id = $1", session_id)
                for message in projected_messages:
                    await conn.execute(
                        """
                        INSERT INTO messages (id, session_id, role, content, parent_id, tool_calls, tool_call_id, token_count, model_used, metadata, created_at)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                        """,
                        message.id,
                        message.session_id,
                        message.role,
                        message.content,
                        message.parent_id,
                        json.dumps(
                            [
                                {"name": tc.name, "args": tc.args, "id": tc.id}
                                for tc in message.tool_calls
                            ]
                        )
                        if message.tool_calls
                        else None,
                        message.tool_call_id,
                        message.token_count,
                        message.model_used,
                        json.dumps(message.metadata) if message.metadata else None,
                        message.created_at,
                    )

                await conn.execute(
                    "UPDATE sessions SET message_count = $1, updated_at = $2 WHERE id = $3",
                    len(projected_messages),
                    now_utc(),
                    session_id,
                )

        return len(projected_messages)

    async def get_message(self, message_id: str) -> Message | None:
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

    # Runtime operations
    async def create_run(
        self,
        run_id: str,
        session_id: str,
        thread_id: str,
        status: RunStatus = RunStatus.QUEUED,
        effective_scope: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        parent_run_id: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRun:
        """Create a durable run for a session."""
        runs = await self.list_runs(session_id)
        now = now_utc()
        run = make_session_run(
            run_id=run_id,
            session_id=session_id,
            thread_id=thread_id,
            status=status,
            effective_scope=effective_scope,
            attempt=len(runs) + 1,
            idempotency_key=idempotency_key,
            parent_run_id=parent_run_id,
            trace_id=trace_id,
            metadata=metadata,
            started_at=now.isoformat()
            if status in {RunStatus.STARTING, RunStatus.ACTIVE}
            else None,
            last_activity_at=now.isoformat(),
            created_at=now.isoformat(),
            updated_at=now.isoformat(),
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO session_runs (
                    id, session_id, thread_id, status, effective_scope,
                    idempotency_key, attempt, parent_run_id, started_at,
                    last_activity_at, completed_at, error_code, status_reason,
                    trace_id, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                          $11, $12, $13, $14, $15, $16, $17)
                """,
                run.id,
                run.session_id,
                run.thread_id,
                run.status.value,
                json.dumps(run.effective_scope),
                run.idempotency_key,
                run.attempt,
                run.parent_run_id,
                datetime.fromisoformat(run.started_at) if run.started_at else None,
                datetime.fromisoformat(run.last_activity_at) if run.last_activity_at else None,
                datetime.fromisoformat(run.completed_at) if run.completed_at else None,
                run.error_code,
                run.status_reason,
                run.trace_id,
                json.dumps(run.metadata),
                datetime.fromisoformat(run.created_at),
                datetime.fromisoformat(run.updated_at),
            )
        return run

    async def get_run(self, run_id: str) -> SessionRun | None:
        """Get a run by ID."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM session_runs WHERE id = $1", run_id)
            if row:
                return self._row_to_run(row)
        return None

    async def get_run_by_idempotency_key(
        self,
        session_id: str,
        idempotency_key: str,
    ) -> SessionRun | None:
        """Get an existing run by session and idempotency key."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM session_runs
                WHERE session_id = $1 AND idempotency_key = $2
                ORDER BY created_at DESC
                LIMIT 1
                """,
                session_id,
                idempotency_key,
            )
            if row:
                return self._row_to_run(row)
        return None

    async def list_runs(self, session_id: str) -> list[SessionRun]:
        """List runs for a session, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM session_runs
                WHERE session_id = $1
                ORDER BY created_at DESC
                """,
                session_id,
            )
            return [self._row_to_run(row) for row in rows]

    async def get_active_run(self, session_id: str) -> SessionRun | None:
        """Get the active foreground run for a session."""
        active_statuses = [
            RunStatus.QUEUED.value,
            RunStatus.STARTING.value,
            RunStatus.ACTIVE.value,
            RunStatus.WAITING_FOR_APPROVAL.value,
            RunStatus.STALLED.value,
            RunStatus.ABORTING.value,
        ]
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM session_runs
                WHERE session_id = $1 AND status = ANY($2::text[])
                ORDER BY created_at DESC
                LIMIT 1
                """,
                session_id,
                active_statuses,
            )
            if row:
                return self._row_to_run(row)
        return None

    async def update_run(
        self,
        run_id: str,
        status: RunStatus | str | None = None,
        last_activity_at: str | None = None,
        completed_at: str | None = None,
        error_code: str | None = None,
        status_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRun | None:
        """Update durable run state."""
        run = await self.get_run(run_id)
        if run is None:
            return None

        updates: list[str] = []
        params: list[Any] = []
        param_idx = 1
        now = now_utc()
        if status is not None:
            run_status = status if isinstance(status, RunStatus) else RunStatus(status)
            updates.append(f"status = ${param_idx}")
            params.append(run_status.value)
            param_idx += 1
            if run.started_at is None and run_status in {RunStatus.STARTING, RunStatus.ACTIVE}:
                updates.append(f"started_at = ${param_idx}")
                params.append(now)
                param_idx += 1
            if RunStatus.is_terminal(run_status) and completed_at is None:
                completed_at = now.isoformat()
        if last_activity_at is not None:
            updates.append(f"last_activity_at = ${param_idx}")
            params.append(datetime.fromisoformat(last_activity_at))
            param_idx += 1
        if completed_at is not None:
            updates.append(f"completed_at = ${param_idx}")
            params.append(datetime.fromisoformat(completed_at))
            param_idx += 1
        if error_code is not None:
            updates.append(f"error_code = ${param_idx}")
            params.append(error_code)
            param_idx += 1
        if status_reason is not None:
            updates.append(f"status_reason = ${param_idx}")
            params.append(status_reason)
            param_idx += 1
        if metadata is not None:
            updates.append(f"metadata = ${param_idx}")
            params.append(json.dumps(metadata))
            param_idx += 1
        if not updates:
            return run
        updates.append(f"updated_at = ${param_idx}")
        params.append(now)
        param_idx += 1
        params.append(run_id)
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE session_runs SET {', '.join(updates)} WHERE id = ${param_idx}",
                *params,
            )
        return await self.get_run(run_id)

    async def append_event(
        self,
        event_id: str,
        session_id: str,
        run_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        visibility: Literal["internal", "builder", "end_user"] = "builder",
        effective_scope: dict[str, str] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> SessionEvent:
        """Append a durable runtime event."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", session_id)
                sequence = await conn.fetchval(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM session_events
                    WHERE session_id = $1
                    """,
                    session_id,
                )
                event = make_session_event(
                    event_id=event_id,
                    session_id=session_id,
                    run_id=run_id,
                    sequence=int(sequence),
                    event_type=event_type,
                    visibility=visibility,
                    payload=payload,
                    effective_scope=effective_scope,
                    trace_id=trace_id,
                    span_id=span_id,
                )
                created_at = datetime.fromisoformat(event.created_at)
                await conn.execute(
                    """
                    INSERT INTO session_events (
                        id, session_id, run_id, sequence, event_type, visibility,
                        payload, effective_scope, trace_id, span_id, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                    """,
                    event.id,
                    event.session_id,
                    event.run_id,
                    event.sequence,
                    event.event_type,
                    event.visibility,
                    json.dumps(event.payload),
                    json.dumps(event.effective_scope),
                    event.trace_id,
                    event.span_id,
                    created_at,
                )
                metadata_patch = {
                    "latest_run_id": run_id,
                    "latest_event_type": event_type,
                    "last_activity_at": event.created_at,
                }
                session = await self.get_session(session_id)
                if session is not None:
                    metadata = {**session.metadata, **metadata_patch}
                    await conn.execute(
                        """
                        UPDATE sessions
                        SET metadata = $1, updated_at = $2
                        WHERE id = $3
                        """,
                        json.dumps(metadata),
                        created_at,
                        session_id,
                    )
                await conn.execute(
                    """
                    UPDATE session_runs
                    SET last_activity_at = $1, updated_at = $1
                    WHERE id = $2
                    """,
                    created_at,
                    run_id,
                )
        return event

    async def list_events(
        self,
        session_id: str,
        run_id: str | None = None,
        after_sequence: int | None = None,
        limit: int = 100,
        visibility: Literal["internal", "builder", "end_user"] | None = None,
        event_type: str | None = None,
    ) -> list[SessionEvent]:
        """List runtime events for a session using cursor-style filters."""
        query = "SELECT * FROM session_events WHERE session_id = $1"
        params: list[Any] = [session_id]
        param_idx = 2
        if run_id is not None:
            query += f" AND run_id = ${param_idx}"
            params.append(run_id)
            param_idx += 1
        if after_sequence is not None:
            query += f" AND sequence > ${param_idx}"
            params.append(after_sequence)
            param_idx += 1
        if visibility is not None:
            query += f" AND visibility = ${param_idx}"
            params.append(visibility)
            param_idx += 1
        if event_type is not None:
            query += f" AND event_type = ${param_idx}"
            params.append(event_type)
            param_idx += 1
        query += f" ORDER BY sequence ASC LIMIT ${param_idx}"
        params.append(limit)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
            return [self._row_to_event(row) for row in rows]

    # Checkpointer operations
    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get the PostgreSQL checkpointer."""
        if self._checkpointer:
            return self._checkpointer

        conn_string = self.connection_string.replace("postgresql+asyncpg://", "postgresql://")

        pool = AsyncConnectionPool(
            conn_string,
            open=False,
            min_size=1,
            max_size=self.max_pool_size,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": psycopg.rows.dict_row,
            },
        )
        await pool.open()
        self._checkpointer_context = pool
        self._checkpointer = AsyncPostgresSaver(pool)
        await self._checkpointer.setup()

        return self._checkpointer

    async def close_checkpointer(self) -> None:
        """Close the checkpointer connection."""
        if self._checkpointer_context:
            try:
                await self._checkpointer_context.close()
            except Exception:
                pass  # Ignore errors during cleanup
            self._checkpointer_context = None
            self._checkpointer = None

    async def get_store(self) -> BaseStore | None:
        """Get the PostgreSQL store for cross-thread agent memory."""
        if self._store:
            return self._store

        conn_string = self.connection_string.replace("postgresql+asyncpg://", "postgresql://")
        pool = AsyncConnectionPool(
            conn_string,
            open=False,
            min_size=1,
            max_size=self.max_pool_size,
            kwargs={
                "autocommit": True,
                "prepare_threshold": 0,
                "row_factory": psycopg.rows.dict_row,
            },
        )
        await pool.open()
        self._store_context = pool
        self._store = AsyncPostgresStore(pool)
        await self._store.setup()
        return self._store

    async def close_store(self) -> None:
        """Close the store connection."""
        if self._store_context:
            try:
                await self._store_context.close()
            except Exception:
                pass
            self._store_context = None
            self._store = None

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
        config_data = _json_dict(row["config"])
        scopes = _json_dict(row.get("scopes"))
        metadata = _json_dict(row.get("metadata"))
        return make_session(
            session_id=row["id"],
            workspace_path=row["workspace_path"],
            title=row["title"],
            thread_id=row["thread_id"],
            config=SessionConfig(
                provider=config_data.get("provider"),
                model=config_data.get("model"),
                temperature=config_data.get("temperature"),
                max_tokens=config_data.get("max_tokens"),
                recursion_limit=config_data.get("recursion_limit"),
                response_format=config_data.get("response_format"),
                system_prompt=config_data.get("system_prompt"),
            ),
            scopes=scopes,
            created_at=row["created_at"].isoformat(),
            updated_at=row["updated_at"].isoformat(),
            message_count=row["message_count"],
            agent_name=row.get("agent_name", "default"),
            metadata=metadata,
            status=SessionStatus(row["status"]),
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
            tc_list: Any
            if isinstance(tool_calls_data, list):
                tc_list = tool_calls_data
            else:
                try:
                    tc_list = json.loads(tool_calls_data)
                except (json.JSONDecodeError, TypeError):
                    tc_list = None
            try:
                tool_calls = [
                    ToolCall(name=tc["name"], args=tc.get("args", {}), id=tc["id"])
                    for tc in tc_list or []
                ]
            except (KeyError, TypeError):
                tool_calls = None

        metadata = _json_dict(row.get("metadata")) or None

        return make_message(
            message_id=row["id"],
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

    def _row_to_run(self, row: asyncpg.Record) -> SessionRun:
        """Convert a database row to a SessionRun."""
        return make_session_run(
            run_id=row["id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            status=RunStatus(row["status"]),
            effective_scope=_json_dict(row.get("effective_scope")),
            attempt=row["attempt"],
            idempotency_key=row.get("idempotency_key"),
            parent_run_id=row.get("parent_run_id"),
            started_at=_dt_iso(row.get("started_at")),
            last_activity_at=_dt_iso(row.get("last_activity_at")),
            completed_at=_dt_iso(row.get("completed_at")),
            error_code=row.get("error_code"),
            status_reason=row.get("status_reason"),
            trace_id=row.get("trace_id"),
            metadata=_json_dict(row.get("metadata")),
            created_at=_dt_iso(row["created_at"]) or now_utc().isoformat(),
            updated_at=_dt_iso(row["updated_at"]) or now_utc().isoformat(),
        )

    def _row_to_event(self, row: asyncpg.Record) -> SessionEvent:
        """Convert a database row to a SessionEvent."""
        return make_session_event(
            event_id=row["id"],
            session_id=row["session_id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            event_type=row["event_type"],
            visibility=row["visibility"],
            payload=_json_dict(row.get("payload")),
            effective_scope=_json_dict(row.get("effective_scope")),
            trace_id=row.get("trace_id"),
            span_id=row.get("span_id"),
            created_at=_dt_iso(row["created_at"]) or now_utc().isoformat(),
        )


def _dt_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _json_dict(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


# Register as implementing the protocol
StorageBackend.register(PostgresStorageBackend)
