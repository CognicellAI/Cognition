"""StorageBackend protocol definition.

Defines the unified storage interface that all backends must implement.
This protocol combines session storage, message storage, and checkpoint
persistence into a single cohesive interface.

Message persistence follows an explicit split of responsibilities:

- LangGraph checkpoint state is the authoritative record for agent/runtime state.
- The custom ``messages`` table is a read-optimized projection used by Cognition's
  REST API for pagination, timestamps, threading metadata, and per-message
  attributes like token usage.

Backends therefore support both normal message writes and projection
reconciliation from checkpoint state when the projection drifts or must be
rebuilt after an interrupted write path.
"""

from __future__ import annotations

from typing import Any, Literal, Protocol, runtime_checkable

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.store.base import BaseStore

from server.app.models import (
    Message,
    RunStatus,
    RuntimeTask,
    Session,
    SessionConfig,
    SessionEvent,
    SessionRun,
    TaskStatus,
)


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session storage operations."""

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
        """Create a new session.

        Args:
            session_id: Unique identifier for the session.
            thread_id: LangGraph thread identifier.
            config: Session configuration options.
            agent_name: Builder-provisioned Agent bound to the session.
            title: Optional human-readable title.

        Returns:
            The created Session object.
        """
        ...

    async def get_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> Session | None:
        """Get a session by ID.

        Args:
            session_id: The session identifier.

        Returns:
            The Session if found, None otherwise.
        """
        ...

    async def list_sessions(
        self,
        filter_scopes: dict[str, str] | None = None,
        metadata_filters: dict[str, str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Session]:
        """List all sessions.

        Returns:
            List of all sessions in the store.
        """
        ...

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
        """Update a session.

        Args:
            session_id: The session identifier.
            title: Optional new title.
            status: Optional lifecycle status update.
            config: Optional configuration updates.
            agent_name: Optional bound agent definition name.
            metadata: Optional metadata replacement.

        Returns:
            The updated Session if found, None otherwise.
        """
        ...

    async def update_message_count(
        self,
        session_id: str,
        count: int,
        effective_scope: dict[str, str] | None = None,
    ) -> None:
        """Update the message count for a session.

        Args:
            session_id: The session identifier.
            count: New message count.
        """
        ...

    async def delete_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> bool:
        """Delete a session.

        Args:
            session_id: The session identifier.

        Returns:
            True if deleted, False if not found.
        """
        ...


@runtime_checkable
class MessageStore(Protocol):
    """Protocol for message projection storage operations.

    The message store is not the source of truth for runtime conversation state.
    It is a read-optimized projection used for API queries. Implementations must
    therefore support rebuilding the projection from LangGraph checkpoint state
    for a given session/thread.
    """

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
        """Create a new message.

        Args:
            message_id: Unique identifier for the message.
            session_id: The parent session identifier.
            role: Message role (user/assistant/system/tool).
            content: Message content.
            parent_id: Optional parent message ID for threading.
            tool_calls: Optional list of tool call invocations.
            tool_call_id: Optional ID of tool being responded to.
            token_count: Optional token usage for this message.
            model_used: Optional model that generated response.
            metadata: Optional additional metadata.

        Returns:
            The created Message object.
        """
        ...

    async def get_message(
        self,
        message_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> Message | None:
        """Get a message by ID.

        Args:
            message_id: The message identifier.

        Returns:
            The Message if found, None otherwise.
        """
        ...

    async def get_messages_by_session(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        effective_scope: dict[str, str] | None = None,
    ) -> tuple[list[Message], int]:
        """Get messages for a session with pagination.

        Args:
            session_id: The session identifier.
            limit: Maximum number of messages to return.
            offset: Number of messages to skip.

        Returns:
            Tuple of (paginated messages, total count).
        """
        ...

    async def list_messages_for_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> list[Message]:
        """List all messages for a session (no pagination).

        Args:
            session_id: The session identifier.

        Returns:
            List of all messages for the session.
        """
        ...

    async def delete_messages_for_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> int:
        """Delete all messages for a session.

        Args:
            session_id: The session identifier.

        Returns:
            Number of messages deleted.
        """
        ...

    async def rebuild_message_projection(
        self,
        session_id: str,
        thread_id: str,
        checkpoint_messages: list[Any],
        effective_scope: dict[str, str] | None = None,
    ) -> int:
        """Rebuild the message projection for a session from checkpoint state.

        Args:
            session_id: Session whose projection should be reconciled.
            thread_id: LangGraph thread identifier for documentation/debugging.
            checkpoint_messages: Message list from authoritative checkpoint state.

        Returns:
            Number of projected messages written.
        """
        ...


@runtime_checkable
class CheckpointerStore(Protocol):
    """Protocol for LangGraph checkpoint storage operations."""

    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get or create a checkpointer instance.

        Returns:
            Configured checkpoint saver ready for use.
        """
        ...

    async def get_store(self) -> BaseStore | None:
        """Get or create a LangGraph Store instance for cross-thread memory.

        Returns:
            Configured store ready for use, or None if not supported.
        """
        ...

    async def close_checkpointer(self) -> None:
        """Close the checkpointer connection."""
        ...


@runtime_checkable
class RuntimeStore(Protocol):
    """Protocol for durable run and runtime event storage."""

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
        """Create a durable protocol-neutral task."""
        ...

    async def get_task(
        self,
        task_id: str,
        effective_scope: dict[str, str],
        agent_name: str | None = None,
    ) -> RuntimeTask | None:
        """Get a task only when exact scope and optional agent match."""
        ...

    async def get_task_by_idempotency_key(
        self,
        agent_name: str,
        effective_scope: dict[str, str],
        idempotency_key: str,
    ) -> RuntimeTask | None:
        """Get a task by its agent/scope-namespaced idempotency key."""
        ...

    async def list_tasks(
        self,
        agent_name: str,
        effective_scope: dict[str, str],
        context_id: str | None = None,
        statuses: set[TaskStatus] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[RuntimeTask], str | None]:
        """List exact-scope tasks using an opaque continuation cursor."""
        ...

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
        """Conditionally update a task, returning None on mismatch/not-found."""
        ...

    async def delete_task_data(self, task_id: str, effective_scope: dict[str, str]) -> bool:
        """Delete a terminal task and its task-owned runtime projections."""
        ...

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
        ...

    async def get_run(
        self,
        run_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get a run by ID."""
        ...

    async def get_run_by_idempotency_key(
        self,
        session_id: str,
        idempotency_key: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get an existing run by session and idempotency key."""
        ...

    async def list_runs(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> list[SessionRun]:
        """List runs for a session, newest first."""
        ...

    async def get_active_run(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get the active foreground run for a session, if any."""
        ...

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
        ...

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
        ...

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
        ...


@runtime_checkable
class StorageBackend(Protocol):
    """Unified storage backend protocol.

    Combines SessionStore, MessageStore, and CheckpointerStore
    into a single cohesive interface for all persistence needs.
    """

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
        ...

    async def get_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> Session | None:
        """Get a session by ID."""
        ...

    async def list_sessions(
        self,
        filter_scopes: dict[str, str] | None = None,
        metadata_filters: dict[str, str] | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[Session]:
        """List all sessions."""
        ...

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
        """Update a session.

        Args:
            session_id: The session identifier.
            title: Optional new title.
            status: Optional new status.
            config: Optional configuration updates.
            agent_name: Optional new agent binding.

        Returns:
            The updated Session if found, None otherwise.
        """
        ...

    async def update_message_count(
        self,
        session_id: str,
        count: int,
        effective_scope: dict[str, str] | None = None,
    ) -> None:
        """Update the message count for a session."""
        ...

    async def delete_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> bool:
        """Delete a session."""
        ...

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
        ...

    async def get_message(
        self,
        message_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> Message | None:
        """Get a message by ID."""
        ...

    async def get_messages_by_session(
        self,
        session_id: str,
        limit: int = 50,
        offset: int = 0,
        effective_scope: dict[str, str] | None = None,
    ) -> tuple[list[Message], int]:
        """Get messages for a session with pagination."""
        ...

    async def list_messages_for_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> list[Message]:
        """List all messages for a session."""
        ...

    async def delete_messages_for_session(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> int:
        """Delete all messages for a session."""
        ...

    async def rebuild_message_projection(
        self,
        session_id: str,
        thread_id: str,
        checkpoint_messages: list[Any],
        effective_scope: dict[str, str] | None = None,
    ) -> int:
        """Rebuild the message projection for a session from checkpoint state."""
        ...

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
        """Create a durable protocol-neutral task."""
        ...

    async def get_task(
        self,
        task_id: str,
        effective_scope: dict[str, str],
        agent_name: str | None = None,
    ) -> RuntimeTask | None:
        """Get a task only when exact scope and optional agent match."""
        ...

    async def get_task_by_idempotency_key(
        self,
        agent_name: str,
        effective_scope: dict[str, str],
        idempotency_key: str,
    ) -> RuntimeTask | None:
        """Get a task by agent/scope-namespaced idempotency key."""
        ...

    async def list_tasks(
        self,
        agent_name: str,
        effective_scope: dict[str, str],
        context_id: str | None = None,
        statuses: set[TaskStatus] | None = None,
        limit: int = 100,
        cursor: str | None = None,
    ) -> tuple[list[RuntimeTask], str | None]:
        """List exact-scope tasks using an opaque continuation cursor."""
        ...

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
        """Conditionally update a task, returning None on mismatch/not-found."""
        ...

    async def delete_task_data(self, task_id: str, effective_scope: dict[str, str]) -> bool:
        """Delete a terminal task and its task-owned runtime projections."""
        ...

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
        ...

    async def get_run(
        self,
        run_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get a run by ID."""
        ...

    async def get_run_by_idempotency_key(
        self,
        session_id: str,
        idempotency_key: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get an existing run by session and idempotency key."""
        ...

    async def list_runs(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> list[SessionRun]:
        """List runs for a session, newest first."""
        ...

    async def get_active_run(
        self,
        session_id: str,
        effective_scope: dict[str, str] | None = None,
    ) -> SessionRun | None:
        """Get the active foreground run for a session, if any."""
        ...

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
        ...

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
        ...

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
        ...

    # Checkpointer operations
    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get or create a checkpointer instance."""
        ...

    async def get_store(self) -> BaseStore | None:
        """Get or create a LangGraph Store instance for cross-thread memory."""
        ...

    # Lifecycle operations
    async def initialize(self) -> None:
        """Initialize the backend (create tables, connections, etc.)."""
        ...

    async def close(self) -> None:
        """Close all connections and cleanup resources."""
        ...

    async def health_check(self) -> dict[str, Any]:
        """Check backend health status.

        Returns:
            Dictionary with health status information.
        """
        ...
