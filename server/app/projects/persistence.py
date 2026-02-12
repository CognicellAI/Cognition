"""Memory persistence for hybrid hot/persistent memory strategy."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from server.app.projects.manager import ProjectManager

logger = structlog.get_logger()


class MemoryPersistence:
    """Manages memory snapshots and restoration."""

    def __init__(self, project_manager: ProjectManager) -> None:
        self.project_manager = project_manager
        self._snapshot_task: asyncio.Task | None = None

    def _get_snapshot_path(self, project_id: str) -> Path:
        """Get the snapshot file path for a project."""
        memories_path = self.project_manager._get_persistent_memories_path(project_id)
        return memories_path / "snapshot.json"

    async def snapshot_hot_memories(self, project_id: str) -> None:
        """Snapshot hot memories to disk.

        This saves the current RAM-based memories to persistent storage.

        Args:
            project_id: Project ID
        """
        try:
            hot_memories_path = self.project_manager._get_hot_memories_path(project_id)

            if not hot_memories_path.exists():
                logger.debug("No hot memories to snapshot", project_id=project_id)
                return

            # Collect all memory files from hot directory
            memory_data = {}
            for memory_file in hot_memories_path.glob("*.json"):
                try:
                    memory_data[memory_file.name] = json.loads(memory_file.read_text())
                except Exception as e:
                    logger.warning(
                        "Failed to read memory file",
                        project_id=project_id,
                        file=memory_file.name,
                        error=str(e),
                    )

            if not memory_data:
                logger.debug("No memory data to snapshot", project_id=project_id)
                return

            # Save snapshot
            snapshot_path = self._get_snapshot_path(project_id)
            snapshot = {
                "timestamp": datetime.utcnow().isoformat(),
                "memories": memory_data,
            }

            await asyncio.to_thread(snapshot_path.write_text, json.dumps(snapshot, indent=2))

            logger.info(
                "Snapshotted memories",
                project_id=project_id,
                files=len(memory_data),
            )

        except Exception as e:
            logger.error(
                "Failed to snapshot memories",
                project_id=project_id,
                error=str(e),
            )

    async def restore_memories(self, project_id: str) -> dict[str, any]:
        """Restore memories from persistent storage.

        Args:
            project_id: Project ID

        Returns:
            Dictionary of restored memory data
        """
        try:
            snapshot_path = self._get_snapshot_path(project_id)

            if not snapshot_path.exists():
                logger.debug("No memory snapshot to restore", project_id=project_id)
                return {}

            snapshot_data = await asyncio.to_thread(lambda: json.loads(snapshot_path.read_text()))

            memories = snapshot_data.get("memories", {})
            timestamp = snapshot_data.get("timestamp")

            logger.info(
                "Restored memories",
                project_id=project_id,
                files=len(memories),
                snapshot_age=timestamp,
            )

            return memories

        except Exception as e:
            logger.error(
                "Failed to restore memories",
                project_id=project_id,
                error=str(e),
            )
            return {}

    async def periodic_snapshot_task(self, interval_seconds: int = 300) -> None:
        """Background task to periodically snapshot all active projects.

        Args:
            interval_seconds: Snapshot interval (default 5 minutes)
        """
        logger.info("Starting periodic memory snapshot task", interval=interval_seconds)

        while True:
            try:
                await asyncio.sleep(interval_seconds)

                # Get all projects with active sessions
                projects = self.project_manager.list_projects()
                active_projects = [p for p in projects if p.active_session]

                if not active_projects:
                    logger.debug("No active projects to snapshot")
                    continue

                logger.info("Snapshotting active projects", count=len(active_projects))

                # Snapshot each active project
                for project in active_projects:
                    await self.snapshot_hot_memories(project.project_id)

            except asyncio.CancelledError:
                logger.info("Memory snapshot task cancelled")
                break
            except Exception as e:
                logger.error("Error in snapshot task", error=str(e))

    def start_periodic_snapshots(self, interval_seconds: int = 300) -> None:
        """Start the periodic snapshot background task.

        Args:
            interval_seconds: Snapshot interval in seconds
        """
        if self._snapshot_task is not None:
            logger.warning("Snapshot task already running")
            return

        self._snapshot_task = asyncio.create_task(self.periodic_snapshot_task(interval_seconds))
        logger.info("Started memory snapshot background task")

    def stop_periodic_snapshots(self) -> None:
        """Stop the periodic snapshot background task."""
        if self._snapshot_task is not None:
            self._snapshot_task.cancel()
            self._snapshot_task = None
            logger.info("Stopped memory snapshot background task")
