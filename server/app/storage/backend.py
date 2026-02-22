"""StorageBackend protocol definition.

Defines the unified storage interface that all backends must implement.
This protocol combines session storage, message storage, and checkpoint
persistence into a single cohesive interface.
"""

from __future__ import annotations

from typing import Any, Literal, Optional, Protocol, runtime_checkable

from langgraph.checkpoint.base import BaseCheckpointSaver

from server.app.models import Message, Session, SessionConfig


@runtime_checkable
class SessionStore(Protocol):
    """Protocol for session storage operations."""

    async def create_session(
        self,
        session_id: str,
        thread_id: str,
        config: SessionConfig,
        title: Optional[str] = None,
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

    async def get_session(self, session_id: str) -> Optional[Session]:
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
        title: Optional[str] = None,
        config: Optional[SessionConfig] = None,
    ) -> Optional[Session]:
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
    """Protocol for message storage operations."""

    async def create_message(
        self,
        message_id: str,
        session_id: str,
        role: Literal["user", "assistant", "system", "tool"],
        content: Optional[str],
        parent_id: Optional[str] = None,
        tool_calls: Optional[list] = None,
        tool_call_id: Optional[str] = None,
        token_count: Optional[int] = None,
        model_used: Optional[str] = None,
        metadata: Optional[dict[str, Any]] = None,
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

    async def get_message(self, message_id: str) -> Optional[Message]:
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


@runtime_checkable
class CheckpointerStore(Protocol):
    """Protocol for LangGraph checkpoint storage operations."""

    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get or create a checkpointer instance.

        Returns:
            Configured checkpoint saver ready for use.
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
        title: Optional[str] = None,
        scopes: Optional[dict[str, str]] = None,
    ) -> Session:
        """Create a new session."""
        ...

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        ...

    async def list_sessions(self) -> list[Session]:
        """List all sessions."""
        ...

    async def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        status: Optional[str] = None,
        config: Optional[SessionConfig] = None,
    ) -> Optional[Session]:
        """Update a session."""
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
        role: Literal["user", "assistant", "system"],
        content: Optional[str],
        parent_id: Optional[str] = None,
    ) -> Message:
        """Create a new message."""
        ...

    async def get_message(self, message_id: str) -> Optional[Message]:
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

    # Checkpointer operations
    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get or create a checkpointer instance."""
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
