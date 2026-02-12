"""Simplified session lifecycle management (in-process, no containers)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

import structlog

from server.app.agent.manager import AgentManager
from server.app.exceptions import SessionError, SessionLimitError, SessionNotFoundError
from server.app.projects.manager import ProjectManager
from server.app.projects.persistence import MemoryPersistence
from server.app.projects.project import ProjectConfig, SessionRecord
from server.app.sessions.workspace import WorkspaceManager
from server.app.settings import Settings, get_settings

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = structlog.get_logger()


@dataclass
class Session:
    """Represents an active coding session (in-process, no container)."""

    session_id: str
    project_id: str
    network_mode: str
    workspace_path: str
    websocket: WebSocket | None = None
    history: list[dict[str, Any]] = field(default_factory=list)


class SessionManager:
    """Manages session lifecycle with in-process agents (no containers)."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.workspace_manager = WorkspaceManager(self.settings)
        self.agent_manager = AgentManager(self.settings)
        self.project_manager = ProjectManager(self.settings)
        self.memory_persistence = MemoryPersistence(self.project_manager)
        self._sessions: dict[str, Session] = {}

    @property
    def session_count(self) -> int:
        """Get the current number of active sessions."""
        return len(self._sessions)

    def create_or_resume_session(
        self,
        project_id: str | None = None,
        user_prefix: str | None = None,
        network_mode: str | None = None,
        repo_url: str | None = None,
        config: ProjectConfig | None = None,
        tags: list[str] | None = None,
        description: str = "",
    ) -> Session:
        """Create a new session or resume existing project.

        Args:
            project_id: Optional project ID to resume
            user_prefix: Optional user prefix for new project
            network_mode: "OFF" or "ON" (currently unused, for future network access)
            repo_url: Optional git repository URL to clone
            config: Optional project configuration
            tags: Optional project tags
            description: Optional project description

        Returns:
            Created Session instance

        Raises:
            SessionLimitError: If max sessions reached
            SessionError: If session creation fails
        """
        if self.session_count >= self.settings.max_sessions:
            raise SessionLimitError(f"Maximum sessions ({self.settings.max_sessions}) reached")

        # Determine project
        if project_id:
            # Resume existing project
            project = self.project_manager.load_project(project_id)
            project_config = project.config
            actual_network_mode = network_mode or project_config.network_mode

            logger.info("Resuming project", project_id=project_id)
        elif user_prefix:
            # Create new project
            project = self.project_manager.create_project(
                user_prefix=user_prefix,
                config=config or ProjectConfig(network_mode=network_mode or "OFF"),
                tags=tags or [],
                description=description,
            )
            project_id = project.project_id
            project_config = project.config
            actual_network_mode = network_mode or project_config.network_mode

            # Clone repo if provided
            if repo_url:
                self.workspace_manager.clone_repo(project_id, repo_url)

            logger.info("Created new project", project_id=project_id, user_prefix=user_prefix)
        else:
            raise SessionError("Either project_id or user_prefix must be provided")

        # Update last accessed
        self.project_manager.update_last_accessed(project_id)

        # Generate session ID
        session_id = str(uuid.uuid4())

        try:
            # Get workspace path
            project_workspace = self.workspace_manager.get_workspace_path(project_id)
            repo_path = self.workspace_manager.get_repo_path(project_id)

            # Ensure paths exist
            project_workspace.mkdir(parents=True, exist_ok=True)
            repo_path.mkdir(parents=True, exist_ok=True)

            # Create in-process agent
            self.agent_manager.create_agent(session_id)

            # Create session record
            session_record = SessionRecord(
                session_id=session_id,
                started_at=datetime.utcnow(),
            )
            self.project_manager.add_session_record(project_id, session_record)

            # Create session
            session = Session(
                session_id=session_id,
                project_id=project_id,
                network_mode=actual_network_mode,
                workspace_path=str(repo_path),
            )

            self._sessions[session_id] = session

            logger.info(
                "Created session",
                session_id=session_id,
                project_id=project_id,
                workspace=str(repo_path),
            )

            return session

        except Exception as e:
            logger.error("Failed to create session", error=str(e))
            # Cleanup on failure
            self.agent_manager.delete_agent(session_id)
            raise SessionError(f"Failed to create session: {e}") from e

    def get_session(self, session_id: str) -> Session:
        """Get a session by ID.

        Args:
            session_id: The session ID

        Returns:
            Session instance

        Raises:
            SessionNotFoundError: If session not found
        """
        if session_id not in self._sessions:
            raise SessionNotFoundError(f"Session {session_id} not found")
        return self._sessions[session_id]

    async def send_to_agent(
        self,
        session_id: str,
        content: str,
        turn_number: int = 0,
    ) -> None:
        """Send message to agent for processing.

        Args:
            session_id: The session ID
            content: Message content
            turn_number: Turn number in conversation
        """
        session = self.get_session(session_id)
        agent = self.agent_manager.get_agent(session_id)
        if not agent:
            raise SessionError(f"No agent found for session {session_id}")

        try:
            response = await agent.process_message(content)
            # Store in history
            self.add_to_history(session_id, "user", content)
            self.add_to_history(session_id, "assistant", response)
        except Exception as e:
            logger.error("Failed to send message to agent", session_id=session_id, error=str(e))
            raise

    def add_to_history(self, session_id: str, role: str, content: str) -> None:
        """Add message to session history.

        Args:
            session_id: Session ID
            role: "user" or "assistant"
            content: Message content
        """
        session = self.get_session(session_id)
        session.history.append(
            {
                "role": role,
                "content": content,
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

    def attach_websocket(self, session_id: str, websocket: Any) -> None:
        """Attach WebSocket to session.

        Args:
            session_id: Session ID
            websocket: WebSocket connection
        """
        session = self.get_session(session_id)
        session.websocket = websocket

    def detach_websocket(self, session_id: str) -> None:
        """Detach WebSocket from session.

        Args:
            session_id: Session ID
        """
        session = self.get_session(session_id)
        session.websocket = None

    async def disconnect_session(self, session_id: str) -> None:
        """Disconnect session (preserve workspace and memories).

        Args:
            session_id: Session ID
        """
        try:
            session = self.get_session(session_id)
            session.websocket = None
            # Session stays in _sessions in case it needs to be resumed
            logger.info("Disconnected session", session_id=session_id)
        except SessionNotFoundError:
            pass

    async def cleanup_all(self) -> None:
        """Cleanup all sessions on shutdown."""
        for session_id in list(self._sessions.keys()):
            try:
                await self.disconnect_session(session_id)
                self.agent_manager.delete_agent(session_id)
            except Exception as e:
                logger.error("Failed to cleanup session", session_id=session_id, error=str(e))
        self._sessions.clear()

    def get_project_manager(self) -> ProjectManager:
        """Get project manager."""
        return self.project_manager

    def get_memory_persistence(self) -> MemoryPersistence:
        """Get memory persistence."""
        return self.memory_persistence


_session_manager: SessionManager | None = None


def get_session_manager(settings: Settings | None = None) -> SessionManager:
    """Get or create global session manager singleton."""
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager(settings)
    return _session_manager
