"""Session workspace management."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from server.app.exceptions import SessionError

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = structlog.get_logger()


class WorkspaceManager:
    """Manages per-session workspace directories."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.workspace_root = settings.workspace_root
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def get_workspace_path(self, session_id: str) -> Path:
        """Get the workspace path for a session."""
        return self.workspace_root / session_id

    def get_repo_path(self, session_id: str) -> Path:
        """Get the repo path for a session (mounted in container)."""
        return self.get_workspace_path(session_id) / "repo"

    def get_state_path(self, session_id: str) -> Path:
        """Get the agent state path for a session."""
        return self.get_workspace_path(session_id) / ".agent_state"

    def get_tmp_path(self, session_id: str) -> Path:
        """Get the temp path for a session."""
        return self.get_workspace_path(session_id) / "tmp"

    def create_workspace(self, session_id: str) -> Path:
        """Create workspace directories for a session."""
        workspace = self.get_workspace_path(session_id)

        try:
            workspace.mkdir(parents=True, exist_ok=True)
            self.get_repo_path(session_id).mkdir(exist_ok=True)
            self.get_state_path(session_id).mkdir(exist_ok=True)
            self.get_tmp_path(session_id).mkdir(exist_ok=True)

            logger.info(
                "Created workspace",
                session_id=session_id,
                workspace=str(workspace),
            )
            return workspace
        except OSError as e:
            raise SessionError(f"Failed to create workspace: {e}") from e

    def clone_repo(self, session_id: str, repo_url: str) -> None:
        """Clone a repository into the session workspace."""
        repo_path = self.get_repo_path(session_id)

        try:
            subprocess.run(
                ["git", "clone", repo_url, str(repo_path)],
                check=True,
                capture_output=True,
                text=True,
            )
            logger.info(
                "Cloned repository",
                session_id=session_id,
                repo_url=repo_url,
            )
        except subprocess.CalledProcessError as e:
            raise SessionError(f"Failed to clone repository: {e.stderr}") from e

    def cleanup_workspace(self, session_id: str, force: bool = False) -> None:
        """Remove workspace directory for a session.

        Args:
            session_id: The session ID (or project_id)
            force: Force cleanup even if it looks like a project

        Note: This method should be used cautiously with projects.
        Normally, project workspaces persist across sessions.
        """
        workspace = self.get_workspace_path(session_id)

        if workspace.exists():
            # Check if this looks like a project (has .project_metadata.json)
            if not force and (workspace / ".project_metadata.json").exists():
                logger.warning(
                    "Attempted to cleanup project workspace without force flag",
                    session_id=session_id,
                    workspace=str(workspace),
                )
                return

            try:
                shutil.rmtree(workspace)
                logger.info(
                    "Cleaned up workspace",
                    session_id=session_id,
                    workspace=str(workspace),
                )
            except OSError as e:
                logger.error(
                    "Failed to cleanup workspace",
                    session_id=session_id,
                    error=str(e),
                )

    def get_memories_path(self, project_id: str) -> Path:
        """Get the memories path for a project."""
        return self.get_workspace_path(project_id) / ".memories"

    def get_hot_memories_path(self, project_id: str) -> Path:
        """Get the hot memories path for a project."""
        return self.get_memories_path(project_id) / "hot"

    def get_persistent_memories_path(self, project_id: str) -> Path:
        """Get the persistent memories path for a project."""
        return self.get_memories_path(project_id) / "persistent"

    def validate_path_in_workspace(self, session_id: str, path: str) -> Path:
        """Validate that a path is within the session workspace.

        Args:
            session_id: The session ID
            path: The path to validate (can be absolute or relative)

        Returns:
            The resolved path if valid

        Raises:
            PathValidationError: If path is outside workspace
        """
        from server.app.exceptions import PathValidationError

        workspace = self.get_workspace_path(session_id).resolve()
        repo_path = self.get_repo_path(session_id).resolve()

        # Handle both absolute and relative paths
        target = Path(path).resolve() if Path(path).is_absolute() else (repo_path / path).resolve()

        # Check if path is within workspace or repo
        if not (str(target).startswith(str(workspace)) or str(target).startswith(str(repo_path))):
            raise PathValidationError(
                f"Path {path} is outside workspace",
                details={"path": path, "session_id": session_id},
            )

        return target
