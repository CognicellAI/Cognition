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

from server.app.models import Message, Session, SessionConfig
from server.app.storage.common import (
    filter_sessions,
    make_message,
    make_session,
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
        self._checkpointer = None
        logger.debug("Memory storage closed")

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

        self._sessions[session_id] = session

        logger.info(
            "Session created (memory)",
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return session

    async def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    async def list_sessions(
        self,
        filter_scopes: dict[str, str] | None = None,
        metadata_filters: dict[str, str] | None = None,
    ) -> list[Session]:
        """List all sessions."""
        sessions = sorted(self._sessions.values(), key=lambda s: s.updated_at, reverse=True)
        return filter_sessions(
            sessions, filter_scopes=filter_scopes, metadata_filters=metadata_filters
        )

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
        session = self._sessions.get(session_id)
        if not session:
            return None

        if title is not None:
            session.title = title

        if config is not None:
            session.config = merge_session_config(session.config, config)

        if metadata is not None:
            session.metadata = dict(metadata)

        session.updated_at = now_utc_iso()
        return session

    async def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session."""
        session = self._sessions.get(session_id)
        if session:
            session.message_count = count
            session.updated_at = now_utc_iso()

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            # Also delete associated messages
            self._messages = {k: v for k, v in self._messages.items() if v.session_id != session_id}
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

        self._messages[message_id] = message

        logger.debug(
            "Message created (memory)",
            message_id=message_id,
            session_id=session_id,
        )

        return message

    async def get_message(self, message_id: str) -> Message | None:
        """Get a message by ID."""
        return self._messages.get(message_id)

    async def get_messages_by_session(
        self, session_id: str, limit: int = 50, offset: int = 0
    ) -> tuple[list[Message], int]:
        """Get messages for a session with pagination."""
        session_messages = [m for m in self._messages.values() if m.session_id == session_id]
        session_messages.sort(key=lambda m: m.created_at)

        total = len(session_messages)
        paginated = session_messages[offset : offset + limit]

        return paginated, total

    async def list_messages_for_session(self, session_id: str) -> list[Message]:
        """List all messages for a session."""
        messages = [m for m in self._messages.values() if m.session_id == session_id]
        messages.sort(key=lambda m: m.created_at)
        return messages

    async def delete_messages_for_session(self, session_id: str) -> int:
        """Delete all messages for a session."""
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
    ) -> int:
        """Rebuild API message projection from authoritative checkpoint messages."""
        del thread_id

        await self.delete_messages_for_session(session_id)

        projected_messages = project_checkpoint_messages(session_id, checkpoint_messages)
        for message in projected_messages:
            self._messages[message.id] = message

        session = self._sessions.get(session_id)
        if session is not None:
            session.message_count = len(projected_messages)
            session.updated_at = now_utc_iso()

        return len(projected_messages)

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
