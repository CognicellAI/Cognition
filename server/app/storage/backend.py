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

from server.app.models import Message, Session, SessionConfig


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session storage operations."""

    async def create_session(
        self,
        session_id: str,
        thread_id: str,
        config: SessionConfig,
        title: str | None = None,
        metadata: dict[str, str] | None = None,
        workspace_path: str | None = None,
    ) -> Session:
        """Create a new session.

        Args:
            session_id: Unique identifier for the session.
            thread_id: LangGraph thread identifier.
            config: Session configuration options.
            title: Optional human-readable title.

        Returns:
            The created Session object.
        """
        ...

    async def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID.

        Args:
            session_id: The session identifier.

        Returns:
            The Session if found, None otherwise.
        """
        ...

    async def list_sessions(self) -> list[Session]:
        """List all sessions.

        Returns:
            List of all sessions in the store.
        """
        ...

    async def update_session(
        self,
        session_id: str,
        title: str | None = None,
        config: SessionConfig | None = None,
    ) -> Session | None:
        """Update a session.

        Args:
            session_id: The session identifier.
            title: Optional new title.
            config: Optional configuration updates.

        Returns:
            The updated Session if found, None otherwise.
        """
        ...

    async def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session.

        Args:
            session_id: The session identifier.
            count: New message count.
        """
        ...

    async def delete_session(self, session_id: str) -> bool:
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

    async def get_message(self, message_id: str) -> Message | None:
        """Get a message by ID.

        Args:
            message_id: The message identifier.

        Returns:
            The Message if found, None otherwise.
        """
        ...

    async def get_messages_by_session(
        self, session_id: str, limit: int = 50, offset: int = 0
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

    async def list_messages_for_session(self, session_id: str) -> list[Message]:
        """List all messages for a session (no pagination).

        Args:
            session_id: The session identifier.

        Returns:
            List of all messages for the session.
        """
        ...

    async def delete_messages_for_session(self, session_id: str) -> int:
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
        title: str | None = None,
        scopes: dict[str, str] | None = None,
        agent_name: str = "default",
        metadata: dict[str, str] | None = None,
        workspace_path: str | None = None,
    ) -> Session:
        """Create a new session."""
        ...

    async def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        ...

    async def list_sessions(
        self,
        filter_scopes: dict[str, str] | None = None,
        metadata_filters: dict[str, str] | None = None,
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

    async def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session."""
        ...

    async def delete_session(self, session_id: str) -> bool:
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
    ) -> Message:
        """Create a new message."""
        ...

    async def get_message(self, message_id: str) -> Message | None:
        """Get a message by ID."""
        ...

    async def get_messages_by_session(
        self, session_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[Message], int]:
        """Get messages for a session with pagination."""
        ...

    async def list_messages_for_session(self, session_id: str) -> list[Message]:
        """List all messages for a session."""
        ...

    async def delete_messages_for_session(self, session_id: str) -> int:
        """Delete all messages for a session."""
        ...

    async def rebuild_message_projection(
        self,
        session_id: str,
        thread_id: str,
        checkpoint_messages: list[Any],
    ) -> int:
        """Rebuild the message projection for a session from checkpoint state."""
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
