"""Memory storage backend implementation.

In-memory implementation of the StorageBackend protocol for testing
and development purposes. Data is not persisted across restarts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Optional

import structlog
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.checkpoint.memory import InMemorySaver

from server.app.models import Message, Session, SessionConfig, SessionStatus
from server.app.storage.backend import StorageBackend

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
        self._checkpointer: Optional[InMemorySaver] = None

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
        title: Optional[str] = None,
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
        )

        self._sessions[session_id] = session

        logger.info(
            "Session created (memory)",
            session_id=session_id,
            workspace=str(self.workspace_path),
        )

        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID."""
        return self._sessions.get(session_id)

    async def list_sessions(self, filter_scopes: Optional[dict[str, str]] = None) -> list[Session]:
        """List all sessions."""
        sessions = sorted(
            self._sessions.values(),
            key=lambda s: s.updated_at,
            reverse=True,
        )

        # Filter by scopes if specified
        if filter_scopes:
            sessions = [
                s for s in sessions if all(s.scopes.get(k) == v for k, v in filter_scopes.items())
            ]

        return sessions

    async def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        config: Optional[SessionConfig] = None,
    ) -> Optional[Session]:
        """Update a session."""
        session = self._sessions.get(session_id)
        if not session:
            return None

        if title is not None:
            session.title = title

        if config is not None:
            existing_config = session.config
            session.config = SessionConfig(
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

        session.updated_at = datetime.now(UTC).isoformat()
        return session

    async def update_message_count(self, session_id: str, count: int) -> None:
        """Update the message count for a session."""
        session = self._sessions.get(session_id)
        if session:
            session.message_count = count
            session.updated_at = datetime.now(UTC).isoformat()

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
        content: Optional[str],
        parent_id: Optional[str] = None,
        tool_calls: Optional[list] = None,
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

        self._messages[message_id] = message

        logger.debug(
            "Message created (memory)",
            message_id=message_id,
            session_id=session_id,
        )

        return message

    async def get_message(self, message_id: str) -> Optional[Message]:
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

    # Checkpointer operations
    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get the in-memory checkpointer."""
        if self._checkpointer is None:
            self._checkpointer = InMemorySaver()
        return self._checkpointer

    async def close_checkpointer(self) -> None:
        """Close the checkpointer (no-op for memory)."""
        self._checkpointer = None

    # Health check
    async def health_check(self) -> dict[str, Any]:
        """Check backend health status."""
        return {
            "status": "healthy",
            "backend": "memory",
            "sessions": len(self._sessions),
            "messages": len(self._messages),
        }


# Register as implementing the protocol
StorageBackend.register(MemoryStorageBackend)
