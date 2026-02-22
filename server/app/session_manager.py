"""Session manager for GUI applications.

Provides application-level session management with lifecycle events,
cross-workspace queries, and Deep Agents integration.

Layer: 4 (Agent Runtime) / 6 (API)

This is a thin facade over:
- StorageBackend for session persistence
- Deep Agents create_deep_agent() for agent creation
- LangGraph thread_id/checkpointer for conversation state
- LangGraph context_schema for user/org scoping
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Protocol
from datetime import datetime, timezone

import structlog

from server.app.models import Session, SessionConfig, SessionStatus
from server.app.settings import Settings
from server.app.storage import StorageBackend

logger = structlog.get_logger(__name__)


# ============================================================================
# Lifecycle Event Callbacks
# ============================================================================

SessionCreatedCallback = Callable[[Session], None]
SessionDeletedCallback = Callable[[str], None]
SessionUpdatedCallback = Callable[[Session], None]


# ============================================================================
# Session Context for Deep Agents
# ============================================================================


@dataclass
class SessionContext:
    """Context passed to Deep Agents via context_schema.

    This provides user_id, org_id, and other per-invocation context
    without polluting the conversation state.
    """

    user_id: Optional[str] = None
    org_id: Optional[str] = None
    session_id: str = ""
    workspace_path: str = ""
    scopes: dict[str, str] = field(default_factory=dict)


# ============================================================================
# Managed Session
# ============================================================================


@dataclass
class ManagedSession:
    """A session with its associated agent and metadata.

    This wraps a Session domain model with runtime information
    about the compiled agent and when it was last accessed.
    """

    session: Session
    agent: Optional[Any] = None
    last_accessed: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ============================================================================
# Session Manager
# ============================================================================


class SessionManager:
    """Manages sessions across multiple workspaces for GUI applications.

    This is the primary interface for GUI applications to:
    - Create and delete sessions
    - List sessions (optionally cross-workspace)
    - Get sessions with their associated agents
    - Receive lifecycle event callbacks

    The SessionManager is a thin facade over StorageBackend and
    Deep Agents, leveraging LangGraph primitives for thread management.
    """

    def __init__(
        self,
        storage_backend: StorageBackend,
        settings: Settings,
    ):
        """Initialize the session manager.

        Args:
            storage_backend: Backend for session persistence.
            settings: Application settings.
        """
        self._storage = storage_backend
        self._settings = settings
        self._sessions: dict[str, ManagedSession] = {}

        # Lifecycle callbacks
        self._on_created: list[SessionCreatedCallback] = []
        self._on_deleted: list[SessionDeletedCallback] = []
        self._on_updated: list[SessionUpdatedCallback] = []

    # ========================================================================
    # Lifecycle Event Registration
    # ========================================================================

    def on_session_created(self, callback: SessionCreatedCallback) -> None:
        """Register a callback for session creation events.

        Args:
            callback: Function called with the new Session when created.
        """
        self._on_created.append(callback)

    def on_session_deleted(self, callback: SessionDeletedCallback) -> None:
        """Register a callback for session deletion events.

        Args:
            callback: Function called with session_id when deleted.
        """
        self._on_deleted.append(callback)

    def on_session_updated(self, callback: SessionUpdatedCallback) -> None:
        """Register a callback for session update events.

        Args:
            callback: Function called with the updated Session.
        """
        self._on_updated.append(callback)

    def _notify_created(self, session: Session) -> None:
        """Notify all registered creation callbacks."""
        for callback in self._on_created:
            try:
                callback(session)
            except Exception as e:
                logger.warning(
                    "Session created callback failed",
                    callback=callback.__name__,
                    error=str(e),
                )

    def _notify_deleted(self, session_id: str) -> None:
        """Notify all registered deletion callbacks."""
        for callback in self._on_deleted:
            try:
                callback(session_id)
            except Exception as e:
                logger.warning(
                    "Session deleted callback failed",
                    callback=callback.__name__,
                    error=str(e),
                )

    def _notify_updated(self, session: Session) -> None:
        """Notify all registered update callbacks."""
        for callback in self._on_updated:
            try:
                callback(session)
            except Exception as e:
                logger.warning(
                    "Session updated callback failed",
                    callback=callback.__name__,
                    error=str(e),
                )

    # ========================================================================
    # Session CRUD Operations
    # ========================================================================

    async def create_session(
        self,
        workspace_path: str,
        title: Optional[str] = None,
        config: Optional[SessionConfig] = None,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
        scopes: Optional[dict[str, str]] = None,
    ) -> Session:
        """Create a new session.

        Creates a session with a unique ID and thread_id. The thread_id
        is used by Deep Agents/LangGraph for conversation checkpointing.

        Args:
            workspace_path: Path to the project workspace.
            title: Optional session title.
            config: Optional session configuration.
            user_id: Optional user ID for context scoping.
            org_id: Optional organization ID for context scoping.
            scopes: Optional scope key-value pairs.

        Returns:
            The newly created Session.
        """
        session_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()

        session = Session(
            id=session_id,
            workspace_path=workspace_path,
            title=title,
            thread_id=thread_id,
            status=SessionStatus.ACTIVE,
            config=config or SessionConfig(),
            created_at=now,
            updated_at=now,
            message_count=0,
            scopes=scopes or {},
        )

        # Persist via StorageBackend
        await self._storage.create_session(
            session_id=session.id,
            thread_id=session.thread_id,
            config=session.config,
            title=session.title,
            scopes=session.scopes,
        )

        # Cache in memory
        self._sessions[session_id] = ManagedSession(session=session)

        logger.info(
            "Session created",
            session_id=session_id,
            thread_id=thread_id,
            workspace=workspace_path,
            user_id=user_id,
            org_id=org_id,
        )

        # Notify callbacks
        self._notify_created(session)

        return session

    async def get_session(self, session_id: str) -> Optional[Session]:
        """Get a session by ID.

        First checks the in-memory cache, then falls back to StorageBackend.
        Updates last_accessed time when retrieved from cache.

        Args:
            session_id: The session identifier.

        Returns:
            The Session if found, None otherwise.
        """
        # Check cache first
        if session_id in self._sessions:
            managed = self._sessions[session_id]
            managed.last_accessed = datetime.now(timezone.utc)
            return managed.session

        # Fall back to storage
        session = await self._storage.get_session(session_id)
        if session:
            self._sessions[session_id] = ManagedSession(session=session)

        return session

    async def list_sessions(
        self,
        workspace_path: Optional[str] = None,
        filter_scopes: Optional[dict[str, str]] = None,
    ) -> list[Session]:
        """List sessions with optional filtering.

        Args:
            workspace_path: Optional filter by workspace.
            filter_scopes: Optional filter by scope key-value pairs.

        Returns:
            List of matching Session objects.
        """
        sessions = await self._storage.list_sessions()

        # Filter by workspace if specified
        if workspace_path:
            sessions = [s for s in sessions if s.workspace_path == workspace_path]

        # Filter by scopes if specified
        if filter_scopes:
            sessions = [
                s for s in sessions if all(s.scopes.get(k) == v for k, v in filter_scopes.items())
            ]

        return sessions

    async def delete_session(self, session_id: str) -> bool:
        """Delete a session.

        Removes the session from storage and cache, and notifies
        registered deletion callbacks.

        Args:
            session_id: The session identifier.

        Returns:
            True if session was deleted, False if not found.
        """
        # Remove from cache
        self._sessions.pop(session_id, None)

        # Remove from storage
        deleted = await self._storage.delete_session(session_id)

        if deleted:
            logger.info("Session deleted", session_id=session_id)
            self._notify_deleted(session_id)

        return deleted

    async def update_session(
        self,
        session_id: str,
        title: Optional[str] = None,
        status: Optional[SessionStatus] = None,
    ) -> Optional[Session]:
        """Update a session's metadata.

        Args:
            session_id: The session identifier.
            title: Optional new title.
            status: Optional new status.

        Returns:
            The updated Session if found, None otherwise.
        """
        session = await self.get_session(session_id)
        if not session:
            return None

        if title is not None:
            session.title = title
        if status is not None:
            session.status = status

        session.updated_at = datetime.now(timezone.utc).isoformat()

        # Update in storage
        await self._storage.update_session(
            session_id=session_id,
            title=session.title,
            status=session.status.value,
        )

        # Update in cache
        if session_id in self._sessions:
            self._sessions[session_id].session = session

        logger.info("Session updated", session_id=session_id, title=title, status=status)
        self._notify_updated(session)

        return session

    # ========================================================================
    # Agent Management
    # ========================================================================

    async def get_or_create_agent(
        self,
        session_id: str,
        model: Optional[Any] = None,
    ) -> Optional[Any]:
        """Get or create the compiled agent for a session.

        Uses the agent cache in cognition_agent.py. If the agent doesn't
        exist or needs to be recreated (e.g., tools changed), it creates
        a new one via create_cognition_agent().

        Args:
            session_id: The session identifier.
            model: Optional LLM model to use.

        Returns:
            The compiled agent if session exists, None otherwise.
        """
        managed = self._sessions.get(session_id)
        if not managed:
            session = await self.get_session(session_id)
            if not session:
                return None
            managed = self._sessions[session_id]

        # Return cached agent if available
        if managed.agent is not None:
            managed.last_accessed = datetime.now(timezone.utc)
            return managed.agent

        # Create new agent via create_cognition_agent
        from server.app.agent.cognition_agent import create_cognition_agent
        from server.app.storage import get_storage_backend

        storage = get_storage_backend()
        checkpointer = await storage.get_checkpointer()

        agent = create_cognition_agent(
            project_path=managed.session.workspace_path,
            model=model,
            checkpointer=checkpointer,
            settings=self._settings,
        )

        managed.agent = agent
        managed.last_accessed = datetime.now(timezone.utc)

        logger.info(
            "Agent created for session",
            session_id=session_id,
            thread_id=managed.session.thread_id,
        )

        return agent

    def invalidate_agent(self, session_id: str) -> None:
        """Invalidate the cached agent for a session.

        Call this when tools or middleware change and the agent
        needs to be recreated.

        Args:
            session_id: The session identifier.
        """
        if session_id in self._sessions:
            self._sessions[session_id].agent = None
            logger.info("Agent invalidated for session", session_id=session_id)

    # ========================================================================
    # Context Schema for Deep Agents
    # ========================================================================

    def create_context(
        self,
        session_id: str,
        user_id: Optional[str] = None,
        org_id: Optional[str] = None,
    ) -> Optional[SessionContext]:
        """Create a SessionContext for Deep Agents context_schema.

        This context is passed to Deep Agents via the Runtime and can
        be accessed in middleware and tools.

        Args:
            session_id: The session identifier.
            user_id: Optional user ID.
            org_id: Optional organization ID.

        Returns:
            SessionContext if session exists, None otherwise.
        """
        managed = self._sessions.get(session_id)
        if not managed:
            return None

        session = managed.session

        return SessionContext(
            user_id=user_id,
            org_id=org_id,
            session_id=session_id,
            workspace_path=session.workspace_path,
            scopes=session.scopes,
        )

    # ========================================================================
    # Session Statistics
    # ========================================================================

    def get_stats(self) -> dict[str, Any]:
        """Get session manager statistics.

        Returns:
            Dict with session counts, cache size, etc.
        """
        return {
            "cached_sessions": len(self._sessions),
            "created_callbacks": len(self._on_created),
            "deleted_callbacks": len(self._on_deleted),
            "updated_callbacks": len(self._on_updated),
        }


# ============================================================================
# Global Session Manager
# ============================================================================

_session_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get the global session manager instance.

    Must be initialized by calling initialize_session_manager() first.

    Returns:
        SessionManager instance.

    Raises:
        RuntimeError: If session manager has not been initialized.
    """
    if _session_manager is None:
        raise RuntimeError(
            "Session manager not initialized. Call initialize_session_manager() first."
        )
    return _session_manager


def set_session_manager(manager: SessionManager) -> None:
    """Set the global session manager instance.

    Args:
        manager: Configured SessionManager instance.
    """
    global _session_manager
    _session_manager = manager


def initialize_session_manager(
    storage_backend: StorageBackend,
    settings: Settings,
) -> SessionManager:
    """Initialize and return the global session manager.

    Args:
        storage_backend: Backend for session persistence.
        settings: Application settings.

    Returns:
        Configured SessionManager instance.
    """
    manager = SessionManager(storage_backend, settings)
    set_session_manager(manager)
    return manager
