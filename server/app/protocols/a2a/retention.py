"""Scope-aware opportunistic retention for durable A2A task data."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import structlog

from server.app.agent.task_runtime import AgentTaskRuntime, ListTasks
from server.app.models import RuntimeTask, TaskStatus
from server.app.observability import (
    RUNTIME_TASK_CLEANUP_DURATION,
    RUNTIME_TASK_CLEANUP_TOTAL,
    span,
)

if TYPE_CHECKING:
    from server.app.storage.artifact_store import ArtifactStore
    from server.app.storage.backend import StorageBackend

logger = structlog.get_logger(__name__)


class A2ARetentionManager:
    """Run bounded cleanup for scopes that are actively using A2A."""

    def __init__(
        self,
        runtime: AgentTaskRuntime,
        store: StorageBackend,
        artifact_store: ArtifactStore | None,
        *,
        ttl_seconds: int,
        interval_seconds: float,
        batch_size: int,
        grace_seconds: int,
    ) -> None:
        self._runtime = runtime
        self._store = store
        self._artifact_store = artifact_store
        self._ttl_seconds = ttl_seconds
        self._interval_seconds = interval_seconds
        self._batch_size = batch_size
        self._grace_seconds = grace_seconds
        self._last_run: dict[tuple[str, tuple[tuple[str, str], ...]], float] = {}
        self._lock = asyncio.Lock()

    async def maybe_cleanup(self, agent_name: str, scope: dict[str, str]) -> int:
        """Clean one exact agent/scope namespace when its interval has elapsed."""
        if self._ttl_seconds <= 0:
            return 0
        key = (agent_name, tuple(sorted(scope.items())))
        now = time.monotonic()
        if now - self._last_run.get(key, 0.0) < self._interval_seconds:
            return 0
        async with self._lock:
            now = time.monotonic()
            if now - self._last_run.get(key, 0.0) < self._interval_seconds:
                return 0
            started = time.monotonic()
            try:
                with span(
                    "cognition.a2a.cleanup",
                    {"cognition.scope.keys": ",".join(sorted(scope))},
                ):
                    deleted = await self._cleanup(agent_name, scope)
            except Exception:
                RUNTIME_TASK_CLEANUP_TOTAL.labels(transport="a2a", outcome="error").inc()
                logger.exception("A2A retention cleanup failed", agent_name=agent_name)
                return 0
            finally:
                self._last_run[key] = time.monotonic()
                RUNTIME_TASK_CLEANUP_DURATION.labels(transport="a2a").observe(
                    time.monotonic() - started
                )
            RUNTIME_TASK_CLEANUP_TOTAL.labels(transport="a2a", outcome="deleted").inc(
                deleted
            )
            return deleted

    async def _cleanup(self, agent_name: str, scope: dict[str, str]) -> int:
        cutoff = datetime.now(UTC) - timedelta(
            seconds=self._ttl_seconds + self._grace_seconds
        )
        deleted = 0
        candidates: list[RuntimeTask] = []
        terminal = {
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELED,
            TaskStatus.REJECTED,
        }
        cursor: str | None = None
        while len(candidates) < self._batch_size:
            page = await self._runtime.list(
                ListTasks(
                    agent_name=agent_name,
                    effective_scope=scope,
                    statuses=terminal,
                    limit=min(100, self._batch_size),
                    cursor=cursor,
                )
            )
            if not page.tasks:
                break
            for task in page.tasks:
                updated_at = datetime.fromisoformat(task.updated_at)
                if updated_at.tzinfo is None:
                    updated_at = updated_at.replace(tzinfo=UTC)
                if updated_at > cutoff:
                    continue
                candidates.append(task)
                if len(candidates) >= self._batch_size:
                    break
            cursor = page.next_cursor
            if cursor is None:
                break

        # Collect before deleting so cursor pagination never refers to a row
        # removed earlier in the same cleanup pass.
        for task in candidates:
            runs = [
                run
                for run in await self._store.list_runs(
                    task.session_id,
                    task.effective_scope,
                )
                if run.task_id == task.id
            ]
            if self._artifact_store is not None:
                for run in runs:
                    artifacts = await self._artifact_store.list_artifacts(
                        scope=task.effective_scope,
                        run_id=run.id,
                    )
                    for artifact in artifacts:
                        await self._artifact_store.delete_artifact(
                            artifact.id, task.effective_scope
                        )
            if await self._store.delete_task_data(task.id, task.effective_scope):
                deleted += 1
        return deleted


__all__ = ["A2ARetentionManager"]
