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

from server.app.exceptions import SessionAlreadyExistsError
from server.app.models import (
    Message,
    RunStatus,
    RuntimeTask,
    Session,
    SessionConfig,
    SessionEvent,
    SessionRun,
    SessionStatus,
    TaskStatus,
    ToolCall,
)
from server.app.storage.backend import StorageBackend
from server.app.storage.common import (
    effective_scope_key,
    make_message,
    make_runtime_task,
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

            await conn.execute(
                "ALTER TABLE session_runs ADD COLUMN IF NOT EXISTS task_id VARCHAR(36)"
            )
            await conn.execute(
                "ALTER TABLE session_events ADD COLUMN IF NOT EXISTS task_id VARCHAR(36)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_runs_task "
                "ON session_runs(task_id, created_at)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_events_task_sequence "
                "ON session_events(task_id, sequence)"
            )

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
        agent_name: str,
        title: str | None = None,
        scopes: dict[str, str] | None = None,
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

        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    """
                    INSERT INTO sessions (
                        id, workspace_path, title, thread_id, status,
                        scopes, scope_key, metadata, config, message_count, agent_name,
                        created_at, updated_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    """,
                    session.id,
                    session.workspace_path,
                    session.title,
                    session.thread_id,
                    session.status.value,
                    json.dumps(session.scopes),
                    effective_scope_key(session.scopes),
                    json.dumps(session.metadata),
                    json.dumps(config_json),
                    session.message_count,
                    session.agent_name,
                    now,
                    now,
                )
        except asyncpg.UniqueViolationError as exc:
            raise SessionAlreadyExistsError(session_id) from exc

        logger.info(
            "Session created",
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return session

    async def get_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> Session | None:
        """Get a session only at the exact effective scope."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM sessions WHERE id = $1 AND scope_key = $2",
                session_id,
                effective_scope_key(effective_scope),
            )
            if row and _json_dict(row["scopes"]) == (effective_scope or {}):
                return self._row_to_session(row)
        return None

    async def list_sessions(
        self,
        filter_scopes: dict[str, str] | None = None,
        metadata_filters: dict[str, str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Session]:
        """List all sessions."""
        sessions = []
        async with self._pool.acquire() as conn:
            exact_scope = filter_scopes or {}
            query = "SELECT * FROM sessions WHERE scope_key = $1"
            params: list[Any] = [effective_scope_key(exact_scope)]
            parameter_index = 2
            if metadata_filters:
                predicates = []
                for key, value in metadata_filters.items():
                    predicates.append(
                        f"metadata->>${parameter_index} = ${parameter_index + 1}"
                    )
                    params.extend([key, value])
                    parameter_index += 2
                query += " AND " + " AND ".join(predicates)
            query += " ORDER BY updated_at DESC, id DESC"
            if limit is not None:
                query += f" LIMIT ${parameter_index} OFFSET ${parameter_index + 1}"
                params.extend([limit, offset])

            rows = await conn.fetch(query, *params)
            for row in rows:
                session = self._row_to_session(row)
                if session.scopes == exact_scope:
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
        effective_scope: dict[str, str] | None = None,
    ) -> Session | None:
        """Update a session."""
        session = await self.get_session(session_id, effective_scope)
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
        param_idx += 1
        params.append(effective_scope_key(effective_scope))

        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE sessions SET {', '.join(updates)} "
                f"WHERE id = ${param_idx - 1} AND scope_key = ${param_idx}",
                *params,
            )

        session.updated_at = now.isoformat()
        return session

    async def update_message_count(
        self,
        session_id: str,
        count: int,
        effective_scope: dict[str, str] | None = None,
    ) -> None:
        """Update the message count for a session."""
        now = now_utc()
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE sessions 
                SET message_count = $1, updated_at = $2 
                WHERE id = $3 AND scope_key = $4
                """,
                count,
                now,
                session_id,
                effective_scope_key(effective_scope),
            )

    async def delete_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> bool:
        """Delete a session."""
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                "DELETE FROM sessions WHERE id = $1 AND scope_key = $2",
                session_id,
                effective_scope_key(effective_scope),
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
        effective_scope: dict[str, str] | None = None,
    ) -> Message:
        """Create a new message."""
        if await self.get_session(session_id, effective_scope) is None:
            raise ValueError("Session not found at exact message scope")
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
        effective_scope: dict[str, str] | None = None,
    ) -> int:
        """Rebuild API message projection from authoritative checkpoint messages."""
        del thread_id
        if await self.get_session(session_id, effective_scope) is None:
            return 0

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
                    """
                    UPDATE sessions SET message_count = $1, updated_at = $2
                    WHERE id = $3 AND scope_key = $4
                    """,
                    len(projected_messages),
                    now_utc(),
                    session_id,
                    effective_scope_key(effective_scope),
                )

        return len(projected_messages)

    async def get_message(
        self,
        message_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> Message | None:
        """Get a message after an exact-scoped session join."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT messages.* FROM messages
                JOIN sessions ON sessions.id = messages.session_id
                WHERE messages.id = $1 AND sessions.scope_key = $2
                """,
                message_id,
                effective_scope_key(effective_scope),
            )
            if row:
                return self._row_to_message(row)
        return None

    async def get_messages_by_session(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        effective_scope: dict[str, str] | None = None,
    ) -> tuple[list[Message], int]:
        """Get messages for a session with pagination."""
        if await self.get_session(session_id, effective_scope) is None:
            return [], 0
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

    async def list_messages_for_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> list[Message]:
        """List all messages for a session."""
        if await self.get_session(session_id, effective_scope) is None:
            return []
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

    async def delete_messages_for_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> int:
        """Delete all messages for a session."""
        if await self.get_session(session_id, effective_scope) is None:
            return 0
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
    async def create_task(
        self,
        task_id: str,
        context_id: str,
        session_id: str,
        agent_name: str,
        effective_scope: dict[str, str],
        status: TaskStatus = TaskStatus.SUBMITTED,
        idempotency_key: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeTask:
        """Create a durable task with exact scope ownership."""
        task = make_runtime_task(
            task_id=task_id,
            context_id=context_id,
            session_id=session_id,
            agent_name=agent_name,
            status=status,
            effective_scope=effective_scope,
            idempotency_key=idempotency_key,
            metadata=metadata,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO runtime_tasks (
                    id, context_id, session_id, agent_name, status,
                    effective_scope, scope_key, current_run_id, last_run_id,
                    idempotency_key, status_reason, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14)
                """,
                task.id,
                task.context_id,
                task.session_id,
                task.agent_name,
                task.status.value,
                json.dumps(task.effective_scope),
                effective_scope_key(task.effective_scope),
                task.current_run_id,
                task.last_run_id,
                task.idempotency_key,
                task.status_reason,
                json.dumps(task.metadata),
                datetime.fromisoformat(task.created_at),
                datetime.fromisoformat(task.updated_at),
            )
        return task

    async def get_task(
        self,
        task_id: str,
        effective_scope: dict[str, str],
        agent_name: str | None = None,
    ) -> RuntimeTask | None:
        """Get a task only for its exact scope and optional agent."""
        query = "SELECT * FROM runtime_tasks WHERE id = $1 AND scope_key = $2"
        params: list[Any] = [task_id, effective_scope_key(effective_scope)]
        if agent_name is not None:
            query += " AND agent_name = $3"
            params.append(agent_name)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)
        if row is None:
            return None
        task = self._row_to_task(row)
        return task if task.effective_scope == effective_scope else None

    async def get_task_by_idempotency_key(
        self,
        agent_name: str,
        effective_scope: dict[str, str],
        idempotency_key: str,
    ) -> RuntimeTask | None:
        """Get a task by its exact agent/scope idempotency namespace."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM runtime_tasks
                WHERE agent_name = $1 AND scope_key = $2 AND idempotency_key = $3
                LIMIT 1
                """,
                agent_name,
                effective_scope_key(effective_scope),
                idempotency_key,
            )
        if row is None:
            return None
        task = self._row_to_task(row)
        return task if task.effective_scope == effective_scope else None

    async def list_tasks(
        self,
        agent_name: str,
        effective_scope: dict[str, str],
        context_id: str | None = None,
        statuses: set[TaskStatus] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[RuntimeTask], str | None]:
        """List tasks for an exact agent/scope with stable cursor pagination."""
        query = "SELECT * FROM runtime_tasks WHERE agent_name = $1 AND scope_key = $2"
        params: list[Any] = [agent_name, effective_scope_key(effective_scope)]
        param_index = 3
        if context_id is not None:
            query += f" AND context_id = ${param_index}"
            params.append(context_id)
            param_index += 1
        if statuses:
            query += f" AND status = ANY(${param_index}::text[])"
            params.append([status.value for status in statuses])
            param_index += 1
        if cursor is not None:
            query += (
                f" AND (created_at, id) < "
                f"(SELECT created_at, id FROM runtime_tasks WHERE id = ${param_index})"
            )
            params.append(cursor)
            param_index += 1
        page_size = max(1, min(limit, 1000))
        query += f" ORDER BY created_at DESC, id DESC LIMIT ${param_index}"
        params.append(page_size + 1)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        tasks = [self._row_to_task(row) for row in rows]
        tasks = [task for task in tasks if task.effective_scope == effective_scope]
        has_more = len(tasks) > page_size
        page = tasks[:page_size]
        return page, page[-1].id if page and has_more else None

    async def update_task(
        self,
        task_id: str,
        effective_scope: dict[str, str],
        expected_statuses: set[TaskStatus] | None = None,
        status: TaskStatus | None = None,
        current_run_id: str | None = None,
        last_run_id: str | None = None,
        status_reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> RuntimeTask | None:
        """Conditionally update a task while preserving terminal immutability."""
        current = await self.get_task(task_id, effective_scope)
        if current is None or (
            expected_statuses is not None and current.status not in expected_statuses
        ):
            return None
        if status is not None and not TaskStatus.can_transition(current.status, status):
            return None
        updates: list[str] = []
        params: list[Any] = []
        param_index = 1
        values: list[tuple[str, Any]] = []
        if status is not None:
            values.append(("status", status.value))
        if current_run_id is not None:
            values.append(("current_run_id", current_run_id))
        if last_run_id is not None:
            values.append(("last_run_id", last_run_id))
        if status_reason is not None:
            values.append(("status_reason", status_reason))
        if metadata is not None:
            values.append(("metadata", json.dumps(metadata)))
        if not values:
            return current
        for column, value in values:
            updates.append(f"{column} = ${param_index}")
            params.append(value)
            param_index += 1
        updates.append(f"updated_at = ${param_index}")
        params.append(now_utc())
        param_index += 1
        where_task = param_index
        params.append(task_id)
        param_index += 1
        where_scope = param_index
        params.append(effective_scope_key(effective_scope))
        param_index += 1
        where_status = param_index
        params.append(current.status.value)
        async with self._pool.acquire() as conn:
            result = await conn.execute(
                f"""
                UPDATE runtime_tasks SET {', '.join(updates)}
                WHERE id = ${where_task} AND scope_key = ${where_scope}
                  AND status = ${where_status}
                """,
                *params,
            )
        if result != "UPDATE 1":
            return None
        return await self.get_task(task_id, effective_scope)

    async def delete_task_data(
        self, task_id: str, effective_scope: dict[str, str]
    ) -> bool:
        """Delete only terminal, exact-scope data owned by one task."""
        current = await self.get_task(task_id, effective_scope)
        if current is None or not TaskStatus.is_terminal(current.status):
            return False
        scope_key = effective_scope_key(effective_scope)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM session_events WHERE task_id = $1 AND scope_key = $2",
                task_id,
                scope_key,
            )
            await conn.execute(
                "DELETE FROM messages WHERE metadata->>'task_id' = $1",
                task_id,
            )
            await conn.execute(
                "DELETE FROM session_runs WHERE task_id = $1 AND scope_key = $2",
                task_id,
                scope_key,
            )
            result = await conn.execute(
                "DELETE FROM runtime_tasks WHERE id = $1 AND scope_key = $2",
                task_id,
                scope_key,
            )
        return result == "DELETE 1"

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
        task_id: str | None = None,
        agent_revision: int = 1,
        runtime_manifest: dict[str, Any] | None = None,
        manifest_digest: str | None = None,
    ) -> SessionRun:
        """Create a durable run for a session."""
        exact_scope = effective_scope or {}
        if await self.get_session(session_id, exact_scope) is None:
            raise ValueError("Session not found at exact run scope")
        runs = await self.list_runs(session_id, exact_scope)
        now = now_utc()
        run = make_session_run(
            run_id=run_id,
            session_id=session_id,
            thread_id=thread_id,
            status=status,
            effective_scope=effective_scope,
            agent_revision=agent_revision,
            runtime_manifest=runtime_manifest,
            manifest_digest=manifest_digest,
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
            task_id=task_id,
        )
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO session_runs (
                    id, session_id, thread_id, task_id, status, effective_scope,
                    scope_key, agent_revision, runtime_manifest, manifest_digest,
                    idempotency_key, attempt, parent_run_id, started_at,
                    last_activity_at, completed_at, error_code, status_reason,
                    trace_id, metadata, created_at, updated_at
                ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                          $11, $12, $13, $14, $15, $16, $17, $18,
                          $19, $20, $21, $22)
                """,
                run.id,
                run.session_id,
                run.thread_id,
                run.task_id,
                run.status.value,
                json.dumps(run.effective_scope),
                effective_scope_key(run.effective_scope),
                run.agent_revision,
                json.dumps(run.runtime_manifest),
                run.manifest_digest,
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

    async def get_run(
        self,
        run_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get a run by ID only at the exact scope."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM session_runs WHERE id = $1 AND scope_key = $2",
                run_id,
                effective_scope_key(effective_scope),
            )
            if row and _json_dict(row["effective_scope"]) == (effective_scope or {}):
                return self._row_to_run(row)
        return None

    async def get_run_by_idempotency_key(
        self,
        session_id: str,
        idempotency_key: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get an existing run by session and idempotency key."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT * FROM session_runs
                WHERE session_id = $1 AND idempotency_key = $2 AND scope_key = $3
                ORDER BY created_at DESC
                LIMIT 1
                """,
                session_id,
                idempotency_key,
                effective_scope_key(effective_scope),
            )
            if row:
                return self._row_to_run(row)
        return None

    async def list_runs(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> list[SessionRun]:
        """List runs for a session, newest first."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT * FROM session_runs
                WHERE session_id = $1 AND scope_key = $2
                ORDER BY created_at DESC
                """,
                session_id,
                effective_scope_key(effective_scope),
            )
            return [self._row_to_run(row) for row in rows]

    async def get_active_run(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
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
                WHERE session_id = $1 AND scope_key = $2 AND status = ANY($3::text[])
                ORDER BY created_at DESC
                LIMIT 1
                """,
                session_id,
                effective_scope_key(effective_scope),
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
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Update durable run state."""
        run = await self.get_run(run_id, effective_scope)
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
        param_idx += 1
        params.append(effective_scope_key(effective_scope))
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"UPDATE session_runs SET {', '.join(updates)} "
                f"WHERE id = ${param_idx - 1} AND scope_key = ${param_idx}",
                *params,
            )
        return await self.get_run(run_id, effective_scope)

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
        task_id: str | None = None,
    ) -> SessionEvent:
        """Append a durable runtime event."""
        if task_id is None:
            run = await self.get_run(run_id, effective_scope)
            task_id = run.task_id if run is not None else None
        if await self.get_run(run_id, effective_scope) is None:
            raise ValueError("Run not found at exact event scope")
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                scope_lock = f"{effective_scope_key(effective_scope)}:{session_id}"
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", scope_lock)
                sequence = await conn.fetchval(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM session_events
                    WHERE session_id = $1 AND scope_key = $2
                    """,
                    session_id,
                    effective_scope_key(effective_scope),
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
                    task_id=task_id,
                )
                created_at = datetime.fromisoformat(event.created_at)
                await conn.execute(
                    """
                    INSERT INTO session_events (
                        id, session_id, run_id, task_id, sequence, event_type, visibility,
                        payload, effective_scope, scope_key, trace_id, span_id, created_at
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13)
                    """,
                    event.id,
                    event.session_id,
                    event.run_id,
                    event.task_id,
                    event.sequence,
                    event.event_type,
                    event.visibility,
                    json.dumps(event.payload),
                    json.dumps(event.effective_scope),
                    effective_scope_key(event.effective_scope),
                    event.trace_id,
                    event.span_id,
                    created_at,
                )
                metadata_patch = {
                    "latest_run_id": run_id,
                    "latest_event_type": event_type,
                    "last_activity_at": event.created_at,
                }
                session = await self.get_session(session_id, effective_scope)
                if session is not None:
                    metadata = {**session.metadata, **metadata_patch}
                    await conn.execute(
                        """
                        UPDATE sessions
                        SET metadata = $1, updated_at = $2
                        WHERE id = $3 AND scope_key = $4
                        """,
                        json.dumps(metadata),
                        created_at,
                        session_id,
                        effective_scope_key(effective_scope),
                    )
                await conn.execute(
                    """
                    UPDATE session_runs
                    SET last_activity_at = $1, updated_at = $1
                    WHERE id = $2 AND scope_key = $3
                    """,
                    created_at,
                    run_id,
                    effective_scope_key(effective_scope),
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
        task_id: str | None = None,
        effective_scope: dict[str, str] | None = None,
    ) -> list[SessionEvent]:
        """List runtime events for a session using cursor-style filters."""
        query = "SELECT * FROM session_events WHERE session_id = $1 AND scope_key = $2"
        params: list[Any] = [session_id, effective_scope_key(effective_scope)]
        param_idx = 3
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
        if task_id is not None:
            query += f" AND task_id = ${param_idx}"
            params.append(task_id)
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
            agent_name=row["agent_name"],
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
            agent_revision=int(row["agent_revision"]),
            runtime_manifest=_json_dict(row.get("runtime_manifest")),
            manifest_digest=str(row["manifest_digest"]),
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
            task_id=row.get("task_id"),
        )

    def _row_to_task(self, row: asyncpg.Record) -> RuntimeTask:
        """Convert a database row to a RuntimeTask."""
        return make_runtime_task(
            task_id=row["id"],
            context_id=row["context_id"],
            session_id=row["session_id"],
            agent_name=row["agent_name"],
            status=TaskStatus(row["status"]),
            effective_scope=_json_dict(row.get("effective_scope")),
            current_run_id=row.get("current_run_id"),
            last_run_id=row.get("last_run_id"),
            idempotency_key=row.get("idempotency_key"),
            status_reason=row.get("status_reason"),
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
            task_id=row.get("task_id"),
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
