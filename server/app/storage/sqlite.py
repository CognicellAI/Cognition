"""SQLite storage backend implementation.

Implements the unified StorageBackend protocol using SQLite as the
database engine. Supports sessions, messages, and checkpoint persistence.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import aiosqlite
import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.store.base import BaseStore
from langgraph.store.sqlite.aio import AsyncSqliteStore

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
    now_utc_iso,
)
from server.app.storage.message_projection import project_checkpoint_messages

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
        normalized_connection_string = connection_string.removeprefix("sqlite:///")
        db_path = Path(normalized_connection_string)
        if not db_path.is_absolute():
            db_path = self.workspace_path / normalized_connection_string
        self.db_path = db_path

        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Checkpointer state
        self._checkpointer: AsyncSqliteSaver | None = None
        self._checkpointer_context: Any | None = None

        # Store state (LangGraph cross-thread memory)
        self._store: AsyncSqliteStore | None = None
        self._store_context: Any | None = None

        logger.debug(
            "SqliteStorageBackend initialized",
            db_path=str(self.db_path),
            workspace=str(self.workspace_path),
        )

    async def initialize(self) -> None:
        """Initialize the database schema from centralized schema definitions."""
        from sqlalchemy import create_engine

        from server.app.storage.schema import metadata

        # Generate DDL statements from centralized schema for SQLite dialect
        def generate_schema(connection: Any) -> None:
            # Create all tables
            metadata.create_all(connection)

        # Use sync engine to create schema in our database
        sync_engine = create_engine(f"sqlite:///{self.db_path}")
        with sync_engine.begin() as conn:
            metadata.create_all(conn)

        logger.info(
            "SQLite storage initialized",
            db_path=str(self.db_path),
        )

        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("PRAGMA table_info(sessions)") as cursor:
                session_columns = {str(row[1]) async for row in cursor}
            if "metadata" not in session_columns:
                await db.execute("ALTER TABLE sessions ADD COLUMN metadata JSON DEFAULT '{}'")

            async with db.execute("PRAGMA table_info(session_runs)") as cursor:
                run_columns = {str(row[1]) async for row in cursor}
            if "task_id" not in run_columns:
                await db.execute("ALTER TABLE session_runs ADD COLUMN task_id VARCHAR(36)")

            async with db.execute("PRAGMA table_info(session_events)") as cursor:
                event_columns = {str(row[1]) async for row in cursor}
            if "task_id" not in event_columns:
                await db.execute("ALTER TABLE session_events ADD COLUMN task_id VARCHAR(36)")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_runs_task "
                "ON session_runs(task_id, created_at)"
            )
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_events_task_sequence "
                "ON session_events(task_id, sequence)"
            )
            await db.commit()

        logger.info(
            "SQLite storage initialized",
            db_path=str(self.db_path),
        )

    async def close(self) -> None:
        """Close all connections."""
        await self.close_checkpointer()
        await self.close_store()
        logger.debug("SQLite storage closed")

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
        session = make_session(
            session_id=session_id,
            workspace_path=workspace_path or str(self.workspace_path),
            title=title,
            thread_id=thread_id,
            config=config,
            scopes=scopes,
            agent_name=agent_name,
            metadata=metadata,
        )

        config_json = json.dumps(
            {
                "provider": config.provider,
                "model": config.model,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "recursion_limit": config.recursion_limit,
                "response_format": config.response_format,
                "system_prompt": config.system_prompt,
            }
        )

        scopes_json = json.dumps(scopes or {})
        metadata_json = json.dumps(metadata or {})

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO sessions (
                    id, workspace_path, title, thread_id, status,
                    config, scopes, metadata, message_count, agent_name, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session.id,
                    session.workspace_path,
                    session.title,
                    session.thread_id,
                    session.status.value,
                    config_json,
                    scopes_json,
                    metadata_json,
                    session.message_count,
                    session.agent_name,
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

    async def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)) as cursor:
                row = await cursor.fetchone()
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
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            query = "SELECT * FROM sessions"
            params: list[str] = []
            where_clauses: list[str] = []

            if metadata_filters:
                for key, value in metadata_filters.items():
                    where_clauses.append("json_extract(metadata, ?) = ?")
                    params.extend([f"$.{key}", value])

            if where_clauses:
                query += " WHERE " + " AND ".join(where_clauses)
            query += " ORDER BY updated_at DESC"

            async with db.execute(query, params) as cursor:
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

        if title is not None:
            updates.append("title = ?")
            params.append(title)
            session.title = title

        if status is not None:
            updates.append("status = ?")
            params.append(status)
            session.status = SessionStatus(status)

        if agent_name is not None:
            updates.append("agent_name = ?")
            params.append(agent_name)
            session.agent_name = agent_name

        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata))
            session.metadata = dict(metadata)

        if config is not None:
            new_config = merge_session_config(session.config, config)

            config_json = json.dumps(
                {
                    "provider": new_config.provider,
                    "model": new_config.model,
                    "temperature": new_config.temperature,
                    "max_tokens": new_config.max_tokens,
                    "recursion_limit": new_config.recursion_limit,
                    "response_format": new_config.response_format,
                    "system_prompt": new_config.system_prompt,
                }
            )
            updates.append("config = ?")
            params.append(config_json)
            session.config = new_config

        if not updates:
            return session

        updates.append("updated_at = ?")
        now = now_utc_iso()
        params.append(now)
        session.updated_at = now

        params.append(session_id)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE sessions SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()

        return session

    async def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session."""
        now = now_utc_iso()
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
        content: str | None,
        parent_id: str | None = None,
        tool_calls: list[ToolCall] | None = None,
        tool_call_id: str | None = None,
        token_count: int | None = None,
        model_used: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Message:
        """Create a new message."""
        message = make_message(
            message_id=message_id,
            session_id=session_id,
            role=role,
            content=content,
            parent_id=parent_id,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            token_count=token_count,
            model_used=model_used,
            metadata=metadata,
        )
        now = message.created_at.isoformat()

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

    async def get_message(self, message_id: str) -> Message | None:
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

    async def rebuild_message_projection(
        self,
        session_id: str,
        thread_id: str,
        checkpoint_messages: list[Any],
    ) -> int:
        """Rebuild API message projection from authoritative checkpoint messages."""
        del thread_id

        projected_messages = project_checkpoint_messages(session_id, checkpoint_messages)

        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
            for message in projected_messages:
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
                        message.created_at.isoformat(),
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
                    ),
                )

            now = now_utc_iso()
            await db.execute(
                "UPDATE sessions SET message_count = ?, updated_at = ? WHERE id = ?",
                (len(projected_messages), now, session_id),
            )
            await db.commit()

        return len(projected_messages)

    async def delete_messages_for_session(self, session_id: str) -> int:
        """Delete all messages for a session."""
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                "DELETE FROM messages WHERE session_id = ?",
                (session_id,),
            )
            await db.commit()
            deleted = int(cursor.rowcount)

            if deleted > 0:
                logger.info(
                    "Messages deleted for session",
                    session_id=session_id,
                    count=deleted,
                )
            return deleted

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
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO runtime_tasks (
                    id, context_id, session_id, agent_name, status,
                    effective_scope, scope_key, current_run_id, last_run_id,
                    idempotency_key, status_reason, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task.id,
                    task.context_id,
                    task.session_id,
                    task.agent_name,
                    task.status.value,
                    json.dumps(task.effective_scope, sort_keys=True),
                    effective_scope_key(task.effective_scope),
                    task.current_run_id,
                    task.last_run_id,
                    task.idempotency_key,
                    task.status_reason,
                    json.dumps(task.metadata),
                    task.created_at,
                    task.updated_at,
                ),
            )
            await db.commit()
        return task

    async def get_task(
        self,
        task_id: str,
        effective_scope: dict[str, str],
        agent_name: str | None = None,
    ) -> RuntimeTask | None:
        """Get a task only for its exact scope and optional agent."""
        query = "SELECT * FROM runtime_tasks WHERE id = ? AND scope_key = ?"
        params: list[Any] = [task_id, effective_scope_key(effective_scope)]
        if agent_name is not None:
            query += " AND agent_name = ?"
            params.append(agent_name)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                row = await cursor.fetchone()
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
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM runtime_tasks
                WHERE agent_name = ? AND scope_key = ? AND idempotency_key = ?
                LIMIT 1
                """,
                (agent_name, effective_scope_key(effective_scope), idempotency_key),
            ) as cursor:
                row = await cursor.fetchone()
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
        query = "SELECT * FROM runtime_tasks WHERE agent_name = ? AND scope_key = ?"
        params: list[Any] = [agent_name, effective_scope_key(effective_scope)]
        if context_id is not None:
            query += " AND context_id = ?"
            params.append(context_id)
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            query += f" AND status IN ({placeholders})"
            params.extend(status.value for status in sorted(statuses, key=str))
        if cursor is not None:
            query += " AND (created_at, id) < (SELECT created_at, id FROM runtime_tasks WHERE id = ?)"
            params.append(cursor)
        page_size = max(1, min(limit, 1000))
        query += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(page_size + 1)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as db_cursor:
                rows = await db_cursor.fetchall()
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
        if status is not None:
            updates.append("status = ?")
            params.append(status.value)
        if current_run_id is not None:
            updates.append("current_run_id = ?")
            params.append(current_run_id)
        if last_run_id is not None:
            updates.append("last_run_id = ?")
            params.append(last_run_id)
        if status_reason is not None:
            updates.append("status_reason = ?")
            params.append(status_reason)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata))
        if not updates:
            return current
        updates.append("updated_at = ?")
        params.append(now_utc_iso())
        params.extend([task_id, effective_scope_key(effective_scope), current.status.value])
        async with aiosqlite.connect(self.db_path) as db:
            cursor_result = await db.execute(
                f"""
                UPDATE runtime_tasks SET {', '.join(updates)}
                WHERE id = ? AND scope_key = ? AND status = ?
                """,
                params,
            )
            await db.commit()
        if cursor_result.rowcount != 1:
            return None
        return await self.get_task(task_id, effective_scope)

    async def delete_task_data(
        self, task_id: str, effective_scope: dict[str, str]
    ) -> bool:
        """Delete only terminal, exact-scope data owned by one task."""
        current = await self.get_task(task_id, effective_scope)
        if current is None or not TaskStatus.is_terminal(current.status):
            return False
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM session_events WHERE task_id = ?", (task_id,))
            await db.execute(
                "DELETE FROM messages WHERE json_extract(metadata, '$.task_id') = ?",
                (task_id,),
            )
            await db.execute("DELETE FROM session_runs WHERE task_id = ?", (task_id,))
            cursor = await db.execute(
                "DELETE FROM runtime_tasks WHERE id = ? AND scope_key = ?",
                (task_id, effective_scope_key(effective_scope)),
            )
            await db.commit()
        return cursor.rowcount == 1

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
    ) -> SessionRun:
        """Create a durable run for a session."""
        runs = await self.list_runs(session_id)
        now = now_utc_iso()
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
            started_at=now if status in {RunStatus.STARTING, RunStatus.ACTIVE} else None,
            last_activity_at=now,
            task_id=task_id,
        )
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """
                INSERT INTO session_runs (
                    id, session_id, thread_id, task_id, status, effective_scope,
                    idempotency_key, attempt, parent_run_id, started_at,
                    last_activity_at, completed_at, error_code, status_reason,
                    trace_id, metadata, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.id,
                    run.session_id,
                    run.thread_id,
                    run.task_id,
                    run.status.value,
                    json.dumps(run.effective_scope),
                    run.idempotency_key,
                    run.attempt,
                    run.parent_run_id,
                    run.started_at,
                    run.last_activity_at,
                    run.completed_at,
                    run.error_code,
                    run.status_reason,
                    run.trace_id,
                    json.dumps(run.metadata),
                    run.created_at,
                    run.updated_at,
                ),
            )
            await db.commit()
        return run

    async def get_run(self, run_id: str) -> SessionRun | None:
        """Get a run by ID."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM session_runs WHERE id = ?", (run_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_run(row)
        return None

    async def get_run_by_idempotency_key(
        self,
        session_id: str,
        idempotency_key: str,
    ) -> SessionRun | None:
        """Get an existing run by session and idempotency key."""
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM session_runs
                WHERE session_id = ? AND idempotency_key = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id, idempotency_key),
            ) as cursor:
                row = await cursor.fetchone()
                if row:
                    return self._row_to_run(row)
        return None

    async def list_runs(self, session_id: str) -> list[SessionRun]:
        """List runs for a session, newest first."""
        runs = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """
                SELECT * FROM session_runs
                WHERE session_id = ?
                ORDER BY created_at DESC
                """,
                (session_id,),
            ) as cursor:
                async for row in cursor:
                    runs.append(self._row_to_run(row))
        return runs

    async def get_active_run(self, session_id: str) -> SessionRun | None:
        """Get the active foreground run for a session."""
        active_statuses = (
            RunStatus.QUEUED.value,
            RunStatus.STARTING.value,
            RunStatus.ACTIVE.value,
            RunStatus.WAITING_FOR_APPROVAL.value,
            RunStatus.STALLED.value,
            RunStatus.ABORTING.value,
        )
        placeholders = ", ".join("?" for _ in active_statuses)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                f"""
                SELECT * FROM session_runs
                WHERE session_id = ? AND status IN ({placeholders})
                ORDER BY created_at DESC
                LIMIT 1
                """,
                (session_id, *active_statuses),
            ) as cursor:
                row = await cursor.fetchone()
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
        now = now_utc_iso()
        if status is not None:
            run_status = status if isinstance(status, RunStatus) else RunStatus(status)
            updates.append("status = ?")
            params.append(run_status.value)
            if run.started_at is None and run_status in {RunStatus.STARTING, RunStatus.ACTIVE}:
                updates.append("started_at = ?")
                params.append(now)
            if RunStatus.is_terminal(run_status) and completed_at is None:
                completed_at = now
        if last_activity_at is not None:
            updates.append("last_activity_at = ?")
            params.append(last_activity_at)
        if completed_at is not None:
            updates.append("completed_at = ?")
            params.append(completed_at)
        if error_code is not None:
            updates.append("error_code = ?")
            params.append(error_code)
        if status_reason is not None:
            updates.append("status_reason = ?")
            params.append(status_reason)
        if metadata is not None:
            updates.append("metadata = ?")
            params.append(json.dumps(metadata))
        if not updates:
            return run
        updates.append("updated_at = ?")
        params.append(now)
        params.append(run_id)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"UPDATE session_runs SET {', '.join(updates)} WHERE id = ?", params)
            await db.commit()
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
        task_id: str | None = None,
    ) -> SessionEvent:
        """Append a durable runtime event."""
        if task_id is None:
            run = await self.get_run(run_id)
            task_id = run.task_id if run is not None else None
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM session_events WHERE session_id = ?",
                (session_id,),
            ) as cursor:
                row = await cursor.fetchone()
                sequence = int(row[0]) if row else 1
            event = make_session_event(
                event_id=event_id,
                session_id=session_id,
                run_id=run_id,
                sequence=sequence,
                event_type=event_type,
                visibility=visibility,
                payload=payload,
                effective_scope=effective_scope,
                trace_id=trace_id,
                span_id=span_id,
                task_id=task_id,
            )
            await db.execute(
                """
                INSERT INTO session_events (
                    id, session_id, run_id, task_id, sequence, event_type, visibility,
                    payload, effective_scope, trace_id, span_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.id,
                    event.session_id,
                    event.run_id,
                    event.task_id,
                    event.sequence,
                    event.event_type,
                    event.visibility,
                    json.dumps(event.payload),
                    json.dumps(event.effective_scope),
                    event.trace_id,
                    event.span_id,
                    event.created_at,
                ),
            )
            metadata_patch = {
                "latest_run_id": run_id,
                "latest_event_type": event_type,
                "last_activity_at": event.created_at,
            }
            session = await self.get_session(session_id)
            if session is not None:
                metadata = {**session.metadata, **metadata_patch}
                await db.execute(
                    "UPDATE sessions SET metadata = ?, updated_at = ? WHERE id = ?",
                    (json.dumps(metadata), event.created_at, session_id),
                )
            await db.execute(
                "UPDATE session_runs SET last_activity_at = ?, updated_at = ? WHERE id = ?",
                (event.created_at, event.created_at, run_id),
            )
            await db.commit()
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
    ) -> list[SessionEvent]:
        """List runtime events for a session using cursor-style filters."""
        query = "SELECT * FROM session_events WHERE session_id = ?"
        params: list[Any] = [session_id]
        if run_id is not None:
            query += " AND run_id = ?"
            params.append(run_id)
        if after_sequence is not None:
            query += " AND sequence > ?"
            params.append(after_sequence)
        if visibility is not None:
            query += " AND visibility = ?"
            params.append(visibility)
        if event_type is not None:
            query += " AND event_type = ?"
            params.append(event_type)
        if task_id is not None:
            query += " AND task_id = ?"
            params.append(task_id)
        query += " ORDER BY sequence ASC LIMIT ?"
        params.append(limit)

        events = []
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                async for row in cursor:
                    events.append(self._row_to_event(row))
        return events

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

    async def get_store(self) -> BaseStore | None:
        """Get the SQLite store for cross-thread agent memory."""
        if self._store:
            return self._store

        self._store_context = AsyncSqliteStore.from_conn_string(str(self.db_path))
        self._store = await self._store_context.__aenter__()
        return self._store

    async def close_store(self) -> None:
        """Close the store connection."""
        if self._store_context:
            await self._store_context.__aexit__(None, None, None)
            self._store_context = None
            self._store = None

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
                recursion_limit=config_data.get("recursion_limit"),
                response_format=config_data.get("response_format"),
                system_prompt=config_data.get("system_prompt"),
            ),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            message_count=row["message_count"],
            agent_name=row["agent_name"] if "agent_name" in row.keys() else "default",
            scopes=scopes_data,
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
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

        return make_message(
            message_id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            parent_id=row["parent_id"],
            created_at=datetime.fromisoformat(row["created_at"]),
            tool_calls=tool_calls,
            tool_call_id=row["tool_call_id"],
            token_count=row["token_count"],
            model_used=row["model_used"],
            metadata=metadata,
        )

    def _row_to_run(self, row: aiosqlite.Row) -> SessionRun:
        """Convert a database row to a SessionRun."""
        return make_session_run(
            run_id=row["id"],
            session_id=row["session_id"],
            thread_id=row["thread_id"],
            status=RunStatus(row["status"]),
            effective_scope=json.loads(row["effective_scope"]) if row["effective_scope"] else {},
            attempt=row["attempt"],
            idempotency_key=row["idempotency_key"],
            parent_run_id=row["parent_run_id"],
            started_at=row["started_at"],
            last_activity_at=row["last_activity_at"],
            completed_at=row["completed_at"],
            error_code=row["error_code"],
            status_reason=row["status_reason"],
            trace_id=row["trace_id"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            task_id=row["task_id"],
        )

    def _row_to_task(self, row: aiosqlite.Row) -> RuntimeTask:
        """Convert a database row to a RuntimeTask."""
        return make_runtime_task(
            task_id=row["id"],
            context_id=row["context_id"],
            session_id=row["session_id"],
            agent_name=row["agent_name"],
            status=TaskStatus(row["status"]),
            effective_scope=json.loads(row["effective_scope"]),
            current_run_id=row["current_run_id"],
            last_run_id=row["last_run_id"],
            idempotency_key=row["idempotency_key"],
            status_reason=row["status_reason"],
            metadata=json.loads(row["metadata"]) if row["metadata"] else {},
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def _row_to_event(self, row: aiosqlite.Row) -> SessionEvent:
        """Convert a database row to a SessionEvent."""
        return make_session_event(
            event_id=row["id"],
            session_id=row["session_id"],
            run_id=row["run_id"],
            sequence=row["sequence"],
            event_type=row["event_type"],
            visibility=row["visibility"],
            payload=json.loads(row["payload"]) if row["payload"] else {},
            effective_scope=json.loads(row["effective_scope"]) if row["effective_scope"] else {},
            trace_id=row["trace_id"],
            span_id=row["span_id"],
            created_at=row["created_at"],
            task_id=row["task_id"],
        )


# Register as implementing the protocol
StorageBackend.register(SqliteStorageBackend)
