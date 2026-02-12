"""Session management for the Cognition server."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.agent import Any


@dataclass
class Session:
    """Represents an active agent session."""

    session_id: str
    thread_id: str
    project_id: str
    project_path: str
    agent: Any = field(repr=False)
    created_at: datetime = field(default_factory=datetime.utcnow)
    last_activity: datetime = field(default_factory=datetime.utcnow)

    def touch(self) -> None:
        """Update last activity timestamp."""
        self.last_activity = datetime.utcnow()

    @property
    def is_expired(self, timeout_seconds: float = 3600) -> bool:
        """Check if session has expired due to inactivity."""
        elapsed = (datetime.utcnow() - self.last_activity).total_seconds()
        return elapsed > timeout_seconds


class SessionManager:
    """Manages agent sessions with automatic cleanup."""

    def __init__(self, timeout_seconds: float = 3600, max_sessions: int = 100):
        """Initialize the session manager.

        Args:
            timeout_seconds: Time before inactive sessions are cleaned up.
            max_sessions: Maximum number of concurrent sessions allowed.
        """
        self._sessions: dict[str, Session] = {}
        self._timeout_seconds = timeout_seconds
        self._max_sessions = max_sessions
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the background cleanup task."""
        if self._cleanup_task is None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Stop the background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    def create_session(
        self,
        project_id: str,
        project_path: str,
        agent: Any,
    ) -> Session:
        """Create a new session.

        Args:
            project_id: Unique project identifier.
            project_path: Path to the project workspace.
            agent: The Deep Agent instance for this session.

        Returns:
            The created Session.

        Raises:
            RuntimeError: If max sessions exceeded.
        """
        if len(self._sessions) >= self._max_sessions:
            raise RuntimeError(f"Maximum sessions ({self._max_sessions}) exceeded")

        session_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())

        session = Session(
            session_id=session_id,
            thread_id=thread_id,
            project_id=project_id,
            project_path=project_path,
            agent=agent,
        )

        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Session | None:
        """Get a session by ID.

        Args:
            session_id: The session identifier.

        Returns:
            The Session if found, None otherwise.
        """
        session = self._sessions.get(session_id)
        if session:
            session.touch()
        return session

    def delete_session(self, session_id: str) -> bool:
        """Delete a session.

        Args:
            session_id: The session identifier.

        Returns:
            True if deleted, False if not found.
        """
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def list_sessions(self) -> list[Session]:
        """List all active sessions."""
        return list(self._sessions.values())

    async def cleanup_expired(self) -> int:
        """Clean up expired sessions.

        Returns:
            Number of sessions cleaned up.
        """
        expired_ids = [
            sid
            for sid, session in self._sessions.items()
            if session.is_expired(self._timeout_seconds)
        ]

        for sid in expired_ids:
            del self._sessions[sid]

        return len(expired_ids)

    async def _cleanup_loop(self) -> None:
        """Background task that periodically cleans up expired sessions."""
        while True:
            try:
                await asyncio.sleep(60)  # Check every minute
                count = await self.cleanup_expired()
                if count > 0:
                    print(f"Cleaned up {count} expired sessions")
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"Session cleanup error: {e}")


# Global session manager instance
_session_manager: SessionManager | None = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager()
    return _session_manager
