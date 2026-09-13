"""Bounded runtime-owned retention, separate from public protocol traffic."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from server.app.models import Session, TaskStatus
from server.app.storage.artifact_store import ArtifactStore, S3ArtifactStore
from server.app.storage.backend import StorageBackend
from server.app.storage.retention import RETAINABLE_RUN_STATUSES, validate_retention_scan
from server.app.telemetry import operation


@dataclass
class RetentionResult:
    """Content-free outcome for one maintenance page."""

    scanned: int = 0
    eligible: int = 0
    purged: int = 0
    protected: int = 0
    failed: int = 0
    next_cursor: str | None = None


class RuntimeRetention:
    """Expire inactive contexts before deleting their dependent runtime data.

    Artifact/checkpoint failures leave the expired session and its run identity
    available for retry. This does not delete Agent configuration, cross-thread
    memory, builder workspace mounts, S3 bodies, object versions or backups.
    Storage lifecycle independently expires published bytes; this service removes
    artifact records without requiring those bytes to remain readable.
    """

    def __init__(self, store: StorageBackend, artifacts: ArtifactStore) -> None:
        self.store = store
        self.artifacts = artifacts

    async def sweep(
        self, before: str, *, after_id: str = "", limit: int = 100,
        apply: bool = False, scope: dict[str, str] | None = None,
    ) -> RetentionResult:
        """Inspect or clean one bounded page; callers own private administration access."""
        validate_retention_scan(before, limit)
        sessions = await self.store.scan_retention_sessions(before, after_id=after_id, limit=limit)
        result = RetentionResult(scanned=len(sessions))
        if len(sessions) == limit:
            result.next_cursor = sessions[-1].id
        for candidate in sessions:
            if scope is not None and candidate.scopes != scope:
                result.protected += 1
                continue
            try:
                with operation("cognition.retention.session"):
                    if await self._protected(candidate):
                        result.protected += 1
                        continue
                    result.eligible += 1
                    if not apply:
                        continue
                    session = await self.store.claim_retention_session(
                        candidate.id, candidate.scopes, before
                    )
                    if session is None:
                        result.protected += 1
                        result.eligible -= 1
                        continue
                    await self._purge_content(session)
                    if await self.store.purge_retention_session(session.id, session.scopes):
                        result.purged += 1
                    else:
                        result.failed += 1
            except Exception:
                # Do not export filenames, content, scope values, or provider errors.
                result.failed += 1
        return result

    async def _protected(self, session: Session) -> bool:
        runs = await self.store.list_runs(session.id, session.scopes)
        if any(run.status not in RETAINABLE_RUN_STATUSES for run in runs):
            return True
        tasks, _ = await self.store.list_tasks(
            session.agent_name, session.scopes, context_id=session.id,
            statuses={status for status in TaskStatus if not TaskStatus.is_terminal(status)},
            limit=1,
        )
        return bool(tasks)

    async def _purge_content(self, session: Session) -> None:
        runs = await self.store.list_runs(session.id, session.scopes)
        for run in runs:
            artifacts = (
                await self.artifacts.list_retention_artifacts(session.scopes, run.id)
                if isinstance(self.artifacts, S3ArtifactStore)
                else await self.artifacts.list_artifacts(session.scopes, run_id=run.id)
            )
            for artifact in artifacts:
                if artifact.run_id != run.id or artifact.scope != session.scopes:
                    raise ValueError("Retention artifact ownership mismatch")
                # Do not delete the whole artifact identity: another context may
                # create a version after this scan. Replacements of this exact
                # version must also make the conditional deletion fail.
                if not await self.artifacts.delete_artifact_version(artifact):
                    raise RuntimeError("Artifact changed during retention cleanup")
        checkpointer = await self.store.get_checkpointer()
        # Native SQLite saver initializes its schema on read, but not on delete.
        await checkpointer.aget_tuple({"configurable": {"thread_id": session.thread_id}})
        await checkpointer.adelete_thread(session.thread_id)
        if await checkpointer.aget_tuple({"configurable": {"thread_id": session.thread_id}}) is not None:
            raise RuntimeError("Checkpoint cleanup is incomplete")


async def watch_retention(
    service: RuntimeRetention, *, days: int, limit: int, interval_seconds: float,
    scope: dict[str, str] | None = None, cursor: str = "",
) -> AsyncIterator[RetentionResult]:
    """Run one bounded page per interval, cycling back for retries and new data.

    The private caller must enforce deployment enablement. Cancellation stops the
    worker; transient failures preserve the cursor and retry after the interval.
    No public invocation triggers this worker.
    """
    if days < 1 or interval_seconds <= 0:
        raise ValueError("Retention age and interval must be positive")
    validate_retention_scan(datetime.now(UTC).isoformat(), limit)
    while True:
        before = (datetime.now(UTC) - timedelta(days=days)).isoformat()
        try:
            result = await service.sweep(before, after_id=cursor, limit=limit,
                                         apply=True, scope=scope)
        except Exception:
            result = RetentionResult(failed=1, next_cursor=cursor or None)
        else:
            cursor = result.next_cursor or ""
        yield result
        await asyncio.sleep(interval_seconds)
