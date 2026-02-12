"""Session lifecycle management."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any, Callable

import structlog

from server.app.exceptions import SessionError, SessionLimitError, SessionNotFoundError
from server.app.executor.bridge import AgentBridge
from server.app.executor.container import ContainerExecutor
from server.app.projects.manager import ProjectManager
from server.app.projects.persistence import MemoryPersistence
from server.app.projects.project import ProjectConfig, SessionRecord
from server.app.sessions.workspace import WorkspaceManager
from server.app.settings import Settings, get_settings
from shared.protocol.internal import AgentStartMessage

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = structlog.get_logger()


@dataclass
class Session:
    """Represents an active coding session with agent container."""

    session_id: str
    project_id: str
    network_mode: str
    workspace_path: str
    container_id: str
    websocket: WebSocket | None = None
    history: list[dict[str, Any]] = field(default_factory=list)
    agent_bridge: AgentBridge | None = None  # NEW: Connection to agent container


class SessionManager:
    """Manages session lifecycle including workspace and containers."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.workspace_manager = WorkspaceManager(self.settings)
        self.container_executor = ContainerExecutor(self.settings)
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
        """Create a new session for a project or resume existing project.

        If project_id is provided, resume that project.
        If user_prefix is provided, create a new project with that prefix.
        If neither is provided, create an ephemeral session (backward compatibility).

        Args:
            project_id: Optional project ID to resume
            user_prefix: Optional user prefix for new project
            network_mode: "OFF" or "ON" (uses project default if None)
            repo_url: Optional git repository URL to clone
            config: Optional project configuration
            tags: Optional project tags
            description: Optional project description

        Returns:
            Created Session instance

        Raises:
            SessionLimitError: If max sessions reached
            SessionError: If session creation fails
            ProjectNotFoundError: If project_id doesn't exist
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
            # Fallback: create ephemeral session (backward compatibility)
            return self._create_ephemeral_session(network_mode or "OFF", repo_url)

        # Update last accessed
        self.project_manager.update_last_accessed(project_id)

        # Generate session ID
        session_id = str(uuid.uuid4())

        try:
            # Get workspace path for project (use ProjectManager's path management)
            # For projects, the project workspace IS the session workspace
            project_workspace = self.workspace_manager.get_workspace_path(project_id)
            repo_path = self.workspace_manager.get_repo_path(project_id)

            # Ensure the workspace exists (ProjectManager creates it, but this ensures it)
            project_workspace.mkdir(parents=True, exist_ok=True)
            repo_path.mkdir(parents=True, exist_ok=True)

            # Create agent container
            container_id = self.container_executor.create_container(
                session_id=session_id,
                workspace_path=str(repo_path.resolve()),
                network_mode=actual_network_mode,
                project_id=project_id,
            )

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
                container_id=container_id,
            )

            self._sessions[session_id] = session

            # Restore memories if available (only if in async context)
            if self.settings.memory_snapshot_enabled:
                try:
                    loop = asyncio.get_running_loop()
                    asyncio.create_task(self.memory_persistence.restore_memories(project_id))
                except RuntimeError:
                    # No event loop running, skip async restoration
                    # Memories will be restored when WebSocket connects
                    pass

            logger.info(
                "Created session for project",
                session_id=session_id,
                project_id=project_id,
                network_mode=actual_network_mode,
                workspace=str(repo_path),
                container_id=container_id[:12],
            )

            return session

        except Exception as e:
            # Cleanup on failure - only stop container, don't delete project
            container_id = locals().get("container_id")
            if container_id:
                self.container_executor.stop_container(container_id)
            raise SessionError(f"Failed to create session: {e}") from e

    def _create_ephemeral_session(self, network_mode: str, repo_url: str | None = None) -> Session:
        """Create an ephemeral session (backward compatibility).

        Args:
            network_mode: "OFF" or "ON"
            repo_url: Optional git repository URL to clone

        Returns:
            Created Session instance
        """
        session_id = str(uuid.uuid4())

        try:
            # Create workspace (legacy path)
            workspace_path = self.workspace_manager.create_workspace(session_id)

            # Clone repo if URL provided
            if repo_url:
                self.workspace_manager.clone_repo(session_id, repo_url)

            # Create agent container
            container_id = self.container_executor.create_container(
                session_id=session_id,
                workspace_path=str(self.workspace_manager.get_repo_path(session_id).resolve()),
                network_mode=network_mode,
                project_id=session_id,  # For ephemeral sessions, use session_id as project_id
            )

            # Create session
            session = Session(
                session_id=session_id,
                project_id=session_id,  # Use session_id as project_id for ephemeral
                network_mode=network_mode,
                workspace_path=str(workspace_path),
                container_id=container_id,
            )

            self._sessions[session_id] = session

            logger.info(
                "Created ephemeral session",
                session_id=session_id,
                network_mode=network_mode,
                workspace=str(workspace_path),
                container_id=container_id[:12],
            )

            return session

        except Exception as e:
            # Cleanup on failure
            self.workspace_manager.cleanup_workspace(session_id)
            container_id = locals().get("container_id")
            if container_id:
                self.container_executor.stop_container(container_id)
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

    async def initialize_agent_bridge(
        self,
        session_id: str,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        """Initialize and connect to the agent container.

        This creates a WebSocket bridge to the agent running inside
        the container and sends the initialization message.

        Args:
            session_id: The session ID
            on_event: Callback for agent events (assistant messages, tool events, etc.)

        Raises:
            SessionError: If agent bridge initialization fails
        """
        session = self.get_session(session_id)
        settings = self.settings

        try:
            # Wait for container to be ready (port mapped and accessible)
            await self.container_executor.wait_for_container_ready(
                session.container_id, timeout=30.0
            )

            # Get the port from the container (passes session_id for macOS)
            agent_port = self.container_executor.get_container_port(
                session.container_id, session_id=session_id
            )

            # Create and connect bridge
            bridge = AgentBridge(
                container_id=session.container_id,
                agent_host="localhost",  # Container is accessible via port mapping
                agent_port=agent_port,  # Dynamic mapped port
                on_event=on_event,
            )

            # Connect to agent (with built-in retries)
            await bridge.connect(timeout=30.0)
            session.agent_bridge = bridge

            # Build LLM configuration
            llm_config = self._get_llm_config()

            # Send agent initialization
            init_message = AgentStartMessage(
                session_id=session_id,
                project_id=session.project_id,
                workspace_path="/workspace/repo",
                llm_provider=llm_config["provider"],
                llm_model=llm_config["model"],
                llm_temperature=llm_config.get("temperature", 0.7),
                system_prompt=None,  # Use default
                max_iterations=50,
                history=session.history,
            )

            await bridge.initialize_agent(init_message)

            logger.info(
                "Agent bridge initialized",
                session_id=session_id,
                container_id=session.container_id[:12],
            )

        except Exception as e:
            logger.error(
                "Failed to initialize agent bridge",
                session_id=session_id,
                error=str(e),
            )
            raise SessionError(f"Failed to initialize agent: {e}") from e

    def _get_llm_config(self) -> dict[str, Any]:
        """Get LLM configuration from settings."""
        settings = self.settings

        # Determine provider and model
        if settings.openai_api_key:
            return {
                "provider": "openai",
                "model": "gpt-4-turbo-preview",
                "temperature": 0.7,
            }
        elif settings.anthropic_api_key:
            return {
                "provider": "anthropic",
                "model": "claude-3-sonnet-20240229",
                "temperature": 0.7,
            }
        else:
            # Default fallback
            return {
                "provider": "openai",
                "model": "gpt-4-turbo-preview",
                "temperature": 0.7,
            }

    async def send_to_agent(self, session_id: str, content: str, turn_number: int) -> None:
        """Send a user message to the agent.

        Args:
            session_id: The session ID
            content: User message content
            turn_number: Current turn number

        Raises:
            SessionError: If not connected to agent
        """
        session = self.get_session(session_id)

        if not session.agent_bridge or not session.agent_bridge.is_connected:
            raise SessionError(f"Agent not connected for session {session_id}")

        await session.agent_bridge.send_user_message(
            session_id=session_id,
            content=content,
            turn_number=turn_number,
        )

        logger.debug(
            "Sent message to agent",
            session_id=session_id,
            turn_number=turn_number,
            content_length=len(content),
        )

    async def cancel_agent_turn(self, session_id: str) -> None:
        """Cancel the current agent turn.

        Args:
            session_id: The session ID
        """
        session = self.get_session(session_id)

        if session.agent_bridge and session.agent_bridge.is_connected:
            await session.agent_bridge.cancel(session_id)
            logger.debug("Cancelled agent turn", session_id=session_id)

    def attach_websocket(self, session_id: str, websocket: WebSocket) -> None:
        """Attach a WebSocket to a session.

        Args:
            session_id: The session ID
            websocket: The WebSocket connection
        """
        session = self.get_session(session_id)
        session.websocket = websocket
        logger.debug("Attached WebSocket to session", session_id=session_id)

    def detach_websocket(self, session_id: str) -> None:
        """Detach WebSocket from a session."""
        session = self.get_session(session_id)
        session.websocket = None
        logger.debug("Detached WebSocket from session", session_id=session_id)

    async def disconnect_session(self, session_id: str) -> None:
        """Disconnect a session but preserve project resources.

        This is the normal way to end a session when using projects.
        Stops the agent, snapshots memories, but keeps the workspace intact.

        Args:
            session_id: The session ID
        """
        try:
            session = self.get_session(session_id)

            # Shutdown agent gracefully first
            if session.agent_bridge and session.agent_bridge.is_connected:
                try:
                    await session.agent_bridge.shutdown(session_id)
                    await asyncio.sleep(0.5)  # Give agent time to shutdown
                    await session.agent_bridge.disconnect()
                except Exception as e:
                    logger.warning(
                        "Error shutting down agent",
                        session_id=session_id,
                        error=str(e),
                    )

            # Snapshot memories before disconnecting
            if self.settings.memory_snapshot_enabled:
                try:
                    await self.memory_persistence.snapshot_hot_memories(session.project_id)
                except Exception as e:
                    logger.warning(
                        "Failed to snapshot memories",
                        session_id=session_id,
                        error=str(e),
                    )

            # End session record
            self.project_manager.end_session_record(
                session.project_id,
                session_id,
                messages=len(session.history),
            )

            # Stop container
            self.container_executor.stop_container(session.container_id)

            # Remove from sessions
            del self._sessions[session_id]

            logger.info(
                "Disconnected session",
                session_id=session_id,
                project_id=session.project_id,
                messages=len(session.history),
            )

        except SessionNotFoundError:
            logger.warning("Attempted to disconnect non-existent session", session_id=session_id)

    def destroy_session(self, session_id: str, delete_project: bool = False) -> None:
        """Destroy a session and optionally cleanup resources.

        Args:
            session_id: The session ID
            delete_project: If True, also delete the project (use with caution)
        """
        try:
            session = self.get_session(session_id)

            # Stop container
            self.container_executor.stop_container(session.container_id)

            # Only cleanup workspace for ephemeral sessions or explicit delete
            if delete_project or session.project_id == session_id:
                # This is an ephemeral session or explicit delete requested
                self.workspace_manager.cleanup_workspace(session_id)
                if delete_project and session.project_id != session_id:
                    # Explicit project deletion
                    self.project_manager.delete_project(session.project_id, force=True)

            # Remove from sessions
            del self._sessions[session_id]

            logger.info(
                "Destroyed session",
                session_id=session_id,
                project_id=session.project_id,
                delete_project=delete_project,
            )

        except SessionNotFoundError:
            logger.warning("Attempted to destroy non-existent session", session_id=session_id)

    def add_to_history(self, session_id: str, role: str, content: str) -> None:
        """Add a message to session history.

        Args:
            session_id: The session ID
            role: Message role ("user" or "assistant")
            content: Message content
        """
        session = self.get_session(session_id)
        session.history.append({"role": role, "content": content})

    def get_history(self, session_id: str) -> list[dict[str, Any]]:
        """Get session conversation history.

        Args:
            session_id: The session ID

        Returns:
            List of history messages
        """
        session = self.get_session(session_id)
        return session.history.copy()

    async def cleanup_all(self) -> None:
        """Destroy all sessions and cleanup resources."""
        session_ids = list(self._sessions.keys())
        for session_id in session_ids:
            try:
                await self.disconnect_session(session_id)
            except Exception as e:
                logger.error(
                    "Failed to cleanup session",
                    session_id=session_id,
                    error=str(e),
                )

        # Cleanup any orphaned containers
        self.container_executor.cleanup_all()

    def get_project_manager(self) -> ProjectManager:
        """Get the project manager instance."""
        return self.project_manager

    def get_memory_persistence(self) -> MemoryPersistence:
        """Get the memory persistence instance."""
        return self.memory_persistence


# Global session manager instance
_session_manager: SessionManager | None = None


def get_session_manager(settings: Settings | None = None) -> SessionManager:
    """Get or create the global session manager.

    Args:
        settings: Optional settings instance

    Returns:
        SessionManager instance
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionManager(settings)
    return _session_manager
