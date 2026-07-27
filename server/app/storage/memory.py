"""Memory storage backend implementation.

In-memory implementation of the StorageBackend protocol for testing
and development purposes. Data is not persisted across restarts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.store.base import BaseStore
from langgraph.store.memory import InMemoryStore

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
)
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


class MemoryStorageBackend:
    """In-memory storage backend.

    Stores all data in memory. Suitable for testing and development.
    Data is lost when the process exits.
    """

    def __init__(self, workspace_path: str = "."):
        """Initialize memory storage backend.

        Args:
            workspace_path: Absolute path to the workspace directory.
        """
        self.workspace_path = Path(workspace_path).resolve()
        self._sessions: dict[str, Session] = {}
        self._messages: dict[str, Message] = {}
        self._tasks: dict[str, RuntimeTask] = {}
        self._runs: dict[str, SessionRun] = {}
        self._events: dict[str, SessionEvent] = {}
        self._event_sequences: dict[str, int] = {}
        self._checkpointer: InMemorySaver | None = None
        self._store: InMemoryStore | None = None

        logger.debug(
            "MemoryStorageBackend initialized",
            workspace=str(self.workspace_path),
        )

    async def initialize(self) -> None:
        """Initialize the backend (no-op for memory)."""
        logger.info(
            "Memory storage initialized",
            workspace=str(self.workspace_path),
        )

    async def close(self) -> None:
        """Close all connections (no-op for memory)."""
        self._sessions.clear()
        self._messages.clear()
        self._tasks.clear()
        self._runs.clear()
        self._events.clear()
        self._event_sequences.clear()
        self._checkpointer = None
        logger.debug("Memory storage closed")

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
        if session_id in self._sessions:
            raise SessionAlreadyExistsError(session_id)
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

        self._sessions[session_id] = session

        logger.info(
            "Session created (memory)",
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
        session = self._sessions.get(session_id)
        if session is None or session.scopes != (effective_scope or {}):
            return None
        return session

    async def list_sessions(
        self,
        filter_scopes: dict[str, str] | None = None,
        metadata_filters: dict[str, str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Session]:
        """List all sessions."""
        exact_scope = filter_scopes or {}
        sessions = [
            session
            for session in self._sessions.values()
            if session.scopes == exact_scope
            and (
                not metadata_filters
                or all(
                    session.metadata.get(key) == value for key, value in metadata_filters.items()
                )
            )
        ]
        ordered = sorted(sessions, key=lambda session: session.updated_at, reverse=True)
        return ordered[offset:] if limit is None else ordered[offset : offset + limit]

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

        if title is not None:
            session.title = title

        if status is not None:
            session.status = SessionStatus(status)

        if agent_name is not None:
            session.agent_name = agent_name

        if config is not None:
            session.config = merge_session_config(session.config, config)

        if metadata is not None:
            session.metadata = dict(metadata)

        session.updated_at = now_utc_iso()
        return session

    async def update_message_count(
        self,
        session_id: str,
        count: int,
        effective_scope: dict[str, str] | None = None,
    ) -> None:
        """Update the message count for a session."""
        session = await self.get_session(session_id, effective_scope)
        if session:
            session.message_count = count
            session.updated_at = now_utc_iso()

    async def delete_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> bool:
        """Delete a session."""
        if await self.get_session(session_id, effective_scope) is not None:
            del self._sessions[session_id]
            # Also delete associated messages
            self._messages = {k: v for k, v in self._messages.items() if v.session_id != session_id}
            self._tasks = {k: v for k, v in self._tasks.items() if v.session_id != session_id}
            self._runs = {k: v for k, v in self._runs.items() if v.session_id != session_id}
            self._events = {k: v for k, v in self._events.items() if v.session_id != session_id}
            self._event_sequences.pop(session_id, None)
            logger.info(
                "Session deleted (memory)",
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
        tool_calls: list | None = None,
        tool_call_id: str | None = None,
        token_count: int | None = None,
        model_used: str | None = None,
        metadata: dict[str, Any] | None = None,
        effective_scope: dict[str, str] | None = None,
    ) -> Message:
        """Create a new message."""
        if await self.get_session(session_id, effective_scope) is None:
            raise ValueError("Session not found at exact message scope")
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

        self._messages[message_id] = message

        logger.debug(
            "Message created (memory)",
            message_id=message_id,
            session_id=session_id,
        )

        return message

    async def get_message(
        self,
        message_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> Message | None:
        """Get a message after verifying its exact-scoped session."""
        message = self._messages.get(message_id)
        if message is None:
            return None
        if await self.get_session(message.session_id, effective_scope) is None:
            return None
        return message

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
        session_messages = [m for m in self._messages.values() if m.session_id == session_id]
        session_messages.sort(key=lambda m: m.created_at)

        total = len(session_messages)
        paginated = session_messages[offset : offset + limit]

        return paginated, total

    async def list_messages_for_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> list[Message]:
        """List all messages for a session."""
        if await self.get_session(session_id, effective_scope) is None:
            return []
        messages = [m for m in self._messages.values() if m.session_id == session_id]
        messages.sort(key=lambda m: m.created_at)
        return messages

    async def delete_messages_for_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> int:
        """Delete all messages for a session."""
        if await self.get_session(session_id, effective_scope) is None:
            return 0
        to_delete = [k for k, v in self._messages.items() if v.session_id == session_id]
        for key in to_delete:
            del self._messages[key]

        if to_delete:
            logger.info(
                "Messages deleted for session (memory)",
                session_id=session_id,
                count=len(to_delete),
            )

        return len(to_delete)

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
        await self.delete_messages_for_session(session_id, effective_scope)

        projected_messages = project_checkpoint_messages(session_id, checkpoint_messages)
        for message in projected_messages:
            self._messages[message.id] = message

        session = await self.get_session(session_id, effective_scope)
        if session is not None:
            session.message_count = len(projected_messages)
            session.updated_at = now_utc_iso()

        return len(projected_messages)

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
        if task_id in self._tasks:
            raise ValueError(f"Task already exists: {task_id}")
        if idempotency_key:
            existing = await self.get_task_by_idempotency_key(
                agent_name,
                effective_scope,
                idempotency_key,
            )
            if existing is not None:
                raise ValueError(f"Task idempotency key already exists: {idempotency_key}")
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
        self._tasks[task_id] = task
        return task

    async def get_task(
        self,
        task_id: str,
        effective_scope: dict[str, str],
        agent_name: str | None = None,
    ) -> RuntimeTask | None:
        """Get a task only for its exact scope and optional agent."""
        task = self._tasks.get(task_id)
        if task is None or task.effective_scope != effective_scope:
            return None
        if agent_name is not None and task.agent_name != agent_name:
            return None
        return task

    async def get_task_by_idempotency_key(
        self,
        agent_name: str,
        effective_scope: dict[str, str],
        idempotency_key: str,
    ) -> RuntimeTask | None:
        """Get a task by its exact agent/scope idempotency namespace."""
        for task in self._tasks.values():
            if (
                task.agent_name == agent_name
                and task.effective_scope == effective_scope
                and task.idempotency_key == idempotency_key
            ):
                return task
        return None

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
        tasks = [
            task
            for task in self._tasks.values()
            if task.agent_name == agent_name and task.effective_scope == effective_scope
        ]
        if context_id is not None:
            tasks = [task for task in tasks if task.context_id == context_id]
        if statuses:
            tasks = [task for task in tasks if task.status in statuses]
        tasks.sort(key=lambda task: (task.created_at, task.id), reverse=True)
        start = 0
        if cursor is not None:
            start = next(
                (index + 1 for index, task in enumerate(tasks) if task.id == cursor),
                len(tasks),
            )
        page = tasks[start : start + max(1, limit)]
        has_more = start + len(page) < len(tasks)
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
        task = await self.get_task(task_id, effective_scope)
        if task is None or (expected_statuses is not None and task.status not in expected_statuses):
            return None
        if status is not None and not TaskStatus.can_transition(task.status, status):
            return None
        if status is not None:
            task.status = status
        if current_run_id is not None:
            task.current_run_id = current_run_id
        if last_run_id is not None:
            task.last_run_id = last_run_id
        if status_reason is not None:
            task.status_reason = status_reason
        if metadata is not None:
            task.metadata = dict(metadata)
        task.updated_at = now_utc_iso()
        return task

    async def delete_task_data(self, task_id: str, effective_scope: dict[str, str]) -> bool:
        """Delete only terminal, exact-scope data owned by one task."""
        task = await self.get_task(task_id, effective_scope)
        if task is None or not TaskStatus.is_terminal(task.status):
            return False
        run_ids = {
            run.id
            for run in self._runs.values()
            if run.task_id == task_id and run.effective_scope == effective_scope
        }
        for event_id in [
            key
            for key, event in self._events.items()
            if event.task_id == task_id and event.effective_scope == effective_scope
        ]:
            del self._events[event_id]
        for message_id in [
            key
            for key, message in self._messages.items()
            if (message.metadata or {}).get("task_id") == task_id
        ]:
            del self._messages[message_id]
        for run_id in run_ids:
            self._runs.pop(run_id, None)
        del self._tasks[task_id]
        return True

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
        attempt = (
            len(
                [
                    run
                    for run in self._runs.values()
                    if run.session_id == session_id and run.effective_scope == exact_scope
                ]
            )
            + 1
        )
        now = now_utc_iso()
        started_at = now if status in {RunStatus.STARTING, RunStatus.ACTIVE} else None
        run = make_session_run(
            run_id=run_id,
            session_id=session_id,
            thread_id=thread_id,
            status=status,
            effective_scope=effective_scope,
            agent_revision=agent_revision,
            runtime_manifest=runtime_manifest,
            manifest_digest=manifest_digest,
            attempt=attempt,
            idempotency_key=idempotency_key,
            parent_run_id=parent_run_id,
            trace_id=trace_id,
            metadata=metadata,
            started_at=started_at,
            last_activity_at=now,
            task_id=task_id,
        )
        self._runs[run_id] = run
        return run

    async def get_run(
        self,
        run_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get a run by ID only at the exact scope."""
        run = self._runs.get(run_id)
        if run is None or run.effective_scope != (effective_scope or {}):
            return None
        return run

    async def get_run_by_idempotency_key(
        self,
        session_id: str,
        idempotency_key: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get an existing run by session and idempotency key."""
        for run in self._runs.values():
            if (
                run.session_id == session_id
                and run.idempotency_key == idempotency_key
                and run.effective_scope == (effective_scope or {})
            ):
                return run
        return None

    async def list_runs(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> list[SessionRun]:
        """List runs for a session, newest first."""
        runs = [
            run
            for run in self._runs.values()
            if run.session_id == session_id and run.effective_scope == (effective_scope or {})
        ]
        runs.sort(key=lambda run: run.created_at, reverse=True)
        return runs

    async def get_active_run(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get the active foreground run for a session."""
        active_statuses = {
            RunStatus.QUEUED,
            RunStatus.STARTING,
            RunStatus.ACTIVE,
            RunStatus.WAITING_FOR_APPROVAL,
            RunStatus.STALLED,
            RunStatus.ABORTING,
        }
        for run in await self.list_runs(session_id, effective_scope):
            if run.status in active_statuses:
                return run
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
        trace_id: str | None = None,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Update durable run state."""
        run = await self.get_run(run_id, effective_scope)
        if run is None:
            return None

        now = now_utc_iso()
        if status is not None:
            run.status = status if isinstance(status, RunStatus) else RunStatus(status)
            if run.started_at is None and run.status in {RunStatus.STARTING, RunStatus.ACTIVE}:
                run.started_at = now
            if RunStatus.is_terminal(run.status) and completed_at is None:
                completed_at = now
        if last_activity_at is not None:
            run.last_activity_at = last_activity_at
        if completed_at is not None:
            run.completed_at = completed_at
        if error_code is not None:
            run.error_code = error_code
        if status_reason is not None:
            run.status_reason = status_reason
        if metadata is not None:
            run.metadata = dict(metadata)
        if trace_id is not None:
            run.trace_id = trace_id
        run.updated_at = now
        return run

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
        run = await self.get_run(run_id, effective_scope)
        if run is None or await self.get_session(session_id, effective_scope) is None:
            raise ValueError("Run or session not found at exact event scope")
        sequence_key = f"{effective_scope_key(effective_scope)}:{session_id}"
        sequence = self._event_sequences.get(sequence_key, 0) + 1
        self._event_sequences[sequence_key] = sequence
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
        self._events[event_id] = event
        now = event.created_at
        await self.update_run(run_id, last_activity_at=now, effective_scope=effective_scope)
        session = await self.get_session(session_id, effective_scope)
        if session is not None:
            session.updated_at = now
            session.metadata = {
                **session.metadata,
                "latest_run_id": run_id,
                "latest_event_type": event_type,
                "last_activity_at": now,
            }
            run = await self.get_run(run_id, effective_scope)
            if run is not None and not RunStatus.is_terminal(run.status):
                session.metadata["active_run_id"] = run_id
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
        events = [
            event
            for event in self._events.values()
            if event.session_id == session_id and event.effective_scope == (effective_scope or {})
        ]
        if run_id is not None:
            events = [event for event in events if event.run_id == run_id]
        if after_sequence is not None:
            events = [event for event in events if event.sequence > after_sequence]
        if visibility is not None:
            events = [event for event in events if event.visibility == visibility]
        if event_type is not None:
            events = [event for event in events if event.event_type == event_type]
        if task_id is not None:
            events = [event for event in events if event.task_id == task_id]
        events.sort(key=lambda event: event.sequence)
        return events[:limit]

    # Checkpointer operations
    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get the in-memory checkpointer."""
        if self._checkpointer is None:
            self._checkpointer = InMemorySaver()
        return self._checkpointer

    async def close_checkpointer(self) -> None:
        """Close the checkpointer (no-op for memory)."""
        self._checkpointer = None

    async def get_store(self) -> BaseStore | None:
        """Get the in-memory store for cross-thread agent memory."""
        if self._store is None:
            self._store = InMemoryStore()
        return self._store

    # Health check
    async def health_check(self) -> dict[str, Any]:
        """Check backend health status."""
        return {
            "status": "healthy",
            "backend": "memory",
            "sessions": len(self._sessions),
            "messages": len(self._messages),
        }
