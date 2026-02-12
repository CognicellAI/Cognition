"""Project lifecycle management."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from server.app.exceptions import SessionError
from server.app.projects.project import (
    Project,
    ProjectConfig,
    ProjectStatistics,
    SessionRecord,
    generate_project_id,
    validate_project_prefix,
)

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = structlog.get_logger()


class ProjectNotFoundError(Exception):
    """Project not found."""

    pass


class ProjectLimitError(Exception):
    """Project limit exceeded."""

    pass


class ProjectManager:
    """Manages project lifecycle and persistence."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.workspace_root = settings.workspace_root
        self.workspace_root.mkdir(parents=True, exist_ok=True)

    def _get_project_path(self, project_id: str) -> Path:
        """Get the workspace path for a project."""
        return self.workspace_root / project_id

    def _get_metadata_path(self, project_id: str) -> Path:
        """Get the metadata file path for a project."""
        return self._get_project_path(project_id) / ".project_metadata.json"

    def _get_repo_path(self, project_id: str) -> Path:
        """Get the repo path for a project."""
        return self._get_project_path(project_id) / "repo"

    def _get_memories_path(self, project_id: str) -> Path:
        """Get the memories directory path."""
        return self._get_project_path(project_id) / ".memories"

    def _get_hot_memories_path(self, project_id: str) -> Path:
        """Get the hot memories path (for snapshots)."""
        return self._get_memories_path(project_id) / "hot"

    def _get_persistent_memories_path(self, project_id: str) -> Path:
        """Get the persistent memories path."""
        return self._get_memories_path(project_id) / "persistent"

    def _get_logs_path(self, project_id: str) -> Path:
        """Get the logs directory path."""
        return self._get_project_path(project_id) / ".logs"

    def project_exists(self, project_id: str) -> bool:
        """Check if a project exists."""
        return self._get_metadata_path(project_id).exists()

    def load_project(self, project_id: str) -> Project:
        """Load project from disk.

        Args:
            project_id: Project ID

        Returns:
            Project instance

        Raises:
            ProjectNotFoundError: If project doesn't exist
        """
        metadata_path = self._get_metadata_path(project_id)
        if not metadata_path.exists():
            raise ProjectNotFoundError(f"Project {project_id} not found")

        try:
            data = json.loads(metadata_path.read_text())
            project = Project.from_dict(data)
            logger.info("Loaded project", project_id=project_id)
            return project
        except Exception as e:
            raise SessionError(f"Failed to load project metadata: {e}") from e

    def save_project(self, project: Project) -> None:
        """Save project metadata to disk.

        Args:
            project: Project instance to save
        """
        metadata_path = self._get_metadata_path(project.project_id)

        try:
            metadata_path.write_text(json.dumps(project.to_dict(), indent=2))
            logger.debug("Saved project metadata", project_id=project.project_id)
        except Exception as e:
            raise SessionError(f"Failed to save project metadata: {e}") from e

    def create_project(
        self,
        user_prefix: str,
        config: ProjectConfig | None = None,
        tags: list[str] | None = None,
        description: str = "",
    ) -> Project:
        """Create a new project.

        Args:
            user_prefix: User-provided project prefix
            config: Optional project configuration
            tags: Optional tags for organization
            description: Optional project description

        Returns:
            Created Project instance

        Raises:
            ValueError: If prefix is invalid
            ProjectLimitError: If project limit exceeded
        """
        # Validate prefix
        validate_project_prefix(user_prefix)

        # Check project limit
        existing_projects = self.list_projects()
        if len(existing_projects) >= getattr(self.settings, "max_projects", 1000):
            raise ProjectLimitError("Maximum number of projects reached")

        # Generate project ID
        project_id = generate_project_id(user_prefix)

        # Create project instance
        now = datetime.utcnow()
        project = Project(
            project_id=project_id,
            user_prefix=user_prefix,
            created_at=now,
            last_accessed=now,
            config=config or ProjectConfig(),
            tags=tags or [],
            description=description,
            cleanup_after_days=getattr(self.settings, "project_cleanup_after_days", 30),
        )

        # Create workspace directories
        try:
            project_path = self._get_project_path(project_id)
            project_path.mkdir(parents=True, exist_ok=True)
            self._get_repo_path(project_id).mkdir(exist_ok=True)
            self._get_memories_path(project_id).mkdir(exist_ok=True)
            self._get_hot_memories_path(project_id).mkdir(exist_ok=True)
            self._get_persistent_memories_path(project_id).mkdir(exist_ok=True)
            self._get_logs_path(project_id).mkdir(exist_ok=True)

            # Save metadata
            self.save_project(project)

            logger.info(
                "Created project",
                project_id=project_id,
                user_prefix=user_prefix,
                workspace=str(project_path),
            )

            return project

        except Exception as e:
            # Cleanup on failure
            self.delete_project(project_id, force=True)
            raise SessionError(f"Failed to create project: {e}") from e

    def update_last_accessed(self, project_id: str) -> None:
        """Update project's last accessed timestamp.

        Args:
            project_id: Project ID
        """
        project = self.load_project(project_id)
        project.last_accessed = datetime.utcnow()
        self.save_project(project)

    def add_session_record(self, project_id: str, session_record: SessionRecord) -> None:
        """Add a session record to project.

        Args:
            project_id: Project ID
            session_record: Session record to add
        """
        project = self.load_project(project_id)
        project.sessions.append(session_record)
        project.statistics.total_sessions += 1
        self.save_project(project)

    def end_session_record(self, project_id: str, session_id: str, messages: int = 0) -> None:
        """Mark a session as ended.

        Args:
            project_id: Project ID
            session_id: Session ID to end
            messages: Number of messages in session
        """
        project = self.load_project(project_id)

        for session in project.sessions:
            if session.session_id == session_id:
                session.ended_at = datetime.utcnow()
                session.messages = messages
                project.statistics.total_messages += messages
                project.statistics.total_duration_seconds += session.duration_seconds
                break

        self.save_project(project)

    def pin_project(self, project_id: str) -> None:
        """Pin a project (disable auto-cleanup).

        Args:
            project_id: Project ID
        """
        project = self.load_project(project_id)
        project.pinned = True
        self.save_project(project)
        logger.info("Pinned project", project_id=project_id)

    def unpin_project(self, project_id: str) -> None:
        """Unpin a project (enable auto-cleanup).

        Args:
            project_id: Project ID
        """
        project = self.load_project(project_id)
        project.pinned = False
        self.save_project(project)
        logger.info("Unpinned project", project_id=project_id)

    def extend_project_lifetime(self, project_id: str, days: int) -> None:
        """Extend project lifetime by N days.

        Args:
            project_id: Project ID
            days: Number of days to extend
        """
        project = self.load_project(project_id)
        project.last_accessed = datetime.utcnow()
        self.save_project(project)
        logger.info("Extended project lifetime", project_id=project_id, days=days)

    def delete_project(self, project_id: str, force: bool = False) -> None:
        """Delete a project and all its data.

        Args:
            project_id: Project ID
            force: Force delete even if project has active sessions

        Raises:
            SessionError: If project has active sessions and force=False
        """
        if not force and self.project_exists(project_id):
            project = self.load_project(project_id)
            if project.active_session:
                raise SessionError("Cannot delete project with active session")

        project_path = self._get_project_path(project_id)
        if project_path.exists():
            import shutil

            shutil.rmtree(project_path)
            logger.info("Deleted project", project_id=project_id)

    def list_projects(
        self,
        prefix_filter: str | None = None,
        tags_filter: list[str] | None = None,
    ) -> list[Project]:
        """List all projects.

        Args:
            prefix_filter: Optional prefix filter
            tags_filter: Optional tags filter

        Returns:
            List of Project instances
        """
        projects = []

        for project_dir in self.workspace_root.iterdir():
            if not project_dir.is_dir():
                continue

            metadata_path = project_dir / ".project_metadata.json"
            if not metadata_path.exists():
                continue

            try:
                project = self.load_project(project_dir.name)

                # Apply filters
                if prefix_filter and not project.user_prefix.startswith(prefix_filter):
                    continue

                if tags_filter and not any(tag in project.tags for tag in tags_filter):
                    continue

                projects.append(project)
            except Exception as e:
                logger.warning(
                    "Failed to load project",
                    project_id=project_dir.name,
                    error=str(e),
                )

        return sorted(projects, key=lambda p: p.last_accessed, reverse=True)

    def get_projects_pending_deletion(self, warning_days: int = 3) -> list[Project]:
        """Get projects that are pending deletion or need warnings.

        Args:
            warning_days: Number of days before deletion to start warning

        Returns:
            List of projects needing attention
        """
        projects = []

        for project in self.list_projects():
            if project.pinned:
                continue

            days_until = project.days_until_cleanup
            if days_until is not None and days_until <= warning_days:
                projects.append(project)

        return projects
