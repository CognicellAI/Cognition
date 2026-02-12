"""Project cleanup background task."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from server.app.projects.manager import ProjectManager

logger = structlog.get_logger()


class ProjectCleanup:
    """Manages automatic project cleanup."""

    def __init__(self, project_manager: ProjectManager) -> None:
        self.project_manager = project_manager
        self._cleanup_task: asyncio.Task | None = None

    async def check_and_cleanup_projects(
        self,
        warning_days: int = 3,
        dry_run: bool = False,
    ) -> dict[str, list[str]]:
        """Check for projects needing cleanup and optionally delete them.

        Args:
            warning_days: Days before deletion to start warnings
            dry_run: If True, don't actually delete, just report

        Returns:
            Dictionary with 'warned' and 'deleted' project IDs
        """
        result = {"warned": [], "deleted": []}

        try:
            pending = self.project_manager.get_projects_pending_deletion(warning_days)

            for project in pending:
                days_until = project.days_until_cleanup

                if days_until is None:
                    # Pinned, skip
                    continue

                if days_until > 0:
                    # Warning period
                    logger.warning(
                        "Project pending cleanup",
                        project_id=project.project_id,
                        days_remaining=days_until,
                    )
                    result["warned"].append(project.project_id)

                else:
                    # Past cleanup date
                    if dry_run:
                        logger.info(
                            "Would delete project (dry run)",
                            project_id=project.project_id,
                        )
                    else:
                        logger.warning(
                            "Deleting expired project",
                            project_id=project.project_id,
                            last_accessed=project.last_accessed.isoformat(),
                        )
                        try:
                            self.project_manager.delete_project(project.project_id, force=False)
                            result["deleted"].append(project.project_id)
                        except Exception as e:
                            logger.error(
                                "Failed to delete project",
                                project_id=project.project_id,
                                error=str(e),
                            )

            if result["warned"] or result["deleted"]:
                logger.info(
                    "Cleanup check complete",
                    warned=len(result["warned"]),
                    deleted=len(result["deleted"]),
                )

        except Exception as e:
            logger.error("Error in cleanup check", error=str(e))

        return result

    async def periodic_cleanup_task(
        self,
        interval_seconds: int = 86400,
        warning_days: int = 3,
    ) -> None:
        """Background task to periodically check for expired projects.

        Args:
            interval_seconds: Check interval (default 24 hours)
            warning_days: Days before deletion to start warnings
        """
        logger.info(
            "Starting periodic cleanup task",
            interval=interval_seconds,
            warning_days=warning_days,
        )

        while True:
            try:
                await asyncio.sleep(interval_seconds)

                logger.info("Running scheduled cleanup check")
                await self.check_and_cleanup_projects(warning_days=warning_days)

            except asyncio.CancelledError:
                logger.info("Cleanup task cancelled")
                break
            except Exception as e:
                logger.error("Error in cleanup task", error=str(e))

    def start_periodic_cleanup(
        self,
        interval_seconds: int = 86400,
        warning_days: int = 3,
    ) -> None:
        """Start the periodic cleanup background task.

        Args:
            interval_seconds: Check interval in seconds
            warning_days: Days before deletion to start warnings
        """
        if self._cleanup_task is not None:
            logger.warning("Cleanup task already running")
            return

        self._cleanup_task = asyncio.create_task(
            self.periodic_cleanup_task(interval_seconds, warning_days)
        )
        logger.info("Started cleanup background task")

    def stop_periodic_cleanup(self) -> None:
        """Stop the periodic cleanup background task."""
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            self._cleanup_task = None
            logger.info("Stopped cleanup background task")
