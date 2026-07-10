"""Runtime projection service for durable runs and events."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from server.app.models import RunStatus, Session, SessionEvent, SessionRun, SessionStatus
from server.app.observability import (
    RUN_TRANSITION_COUNT,
    RUNTIME_EVENT_COUNT,
    current_trace_context,
)
from server.app.observability import (
    span as trace_span,
)
from server.app.storage.backend import StorageBackend


class ActiveRunConflictError(RuntimeError):
    """Raised when a session already has an active foreground run."""

    def __init__(self, run: SessionRun) -> None:
        self.run = run
        super().__init__(f"Session already has active run: {run.id}")


class RuntimeProjectionService:
    """Owns Cognition's durable projection of Deep Agents runtime activity."""

    def __init__(self, store: StorageBackend) -> None:
        self.store = store

    async def begin_run(
        self,
        *,
        session: Session,
        effective_scope: dict[str, str] | None = None,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SessionRun:
        """Create or reuse a durable active run for a session."""
        scope = effective_scope or session.scopes
        with trace_span(
            "cognition.run.begin",
            {
                "cognition.session_id": session.id,
                "cognition.thread_id": session.thread_id,
                "cognition.scope_keys": ",".join(sorted(scope)),
            },
        ):
            span_trace_id, _span_id = current_trace_context()
            if idempotency_key:
                existing = await self.store.get_run_by_idempotency_key(session.id, idempotency_key)
                if existing is not None:
                    return existing

            active = await self.store.get_active_run(session.id)
            if active is not None:
                raise ActiveRunConflictError(active)

            run = await self.store.create_run(
                run_id=str(uuid.uuid4()),
                session_id=session.id,
                thread_id=session.thread_id,
                status=RunStatus.ACTIVE,
                effective_scope=scope,
                idempotency_key=idempotency_key,
                trace_id=trace_id or span_trace_id,
                metadata=metadata,
            )
            await self.store.update_session(
                session_id=session.id,
                status=SessionStatus.ACTIVE.value,
                metadata={
                    **session.metadata,
                    "active_run_id": run.id,
                    "latest_run_id": run.id,
                    "last_activity_at": run.last_activity_at or run.created_at,
                },
            )
            await self.append_event(
                run,
                "run.started",
                payload={"status": RunStatus.ACTIVE.value},
            )
            return run

    async def append_event(
        self,
        run: SessionRun,
        event_type: str,
        *,
        payload: dict[str, Any] | None = None,
        visibility: Literal["internal", "builder", "end_user"] = "builder",
        trace_id: str | None = None,
        span_id: str | None = None,
    ) -> SessionEvent:
        """Append a durable event and advance session/run activity."""
        attributes = _runtime_attributes(
            run,
            {
                "cognition.event_type": event_type,
                "cognition.visibility": visibility,
            },
        )
        with trace_span("cognition.runtime_event", attributes):
            current_trace_id, current_span_id = current_trace_context()
            event = await self.store.append_event(
                event_id=str(uuid.uuid4()),
                session_id=run.session_id,
                run_id=run.id,
                event_type=event_type,
                payload=payload,
                visibility=visibility,
                effective_scope=run.effective_scope,
                trace_id=trace_id or current_trace_id or run.trace_id,
                span_id=span_id or current_span_id,
            )
            RUNTIME_EVENT_COUNT.labels(event_type=event_type, visibility=visibility).inc()
            return event

    async def transition_run(
        self,
        run: SessionRun,
        status: RunStatus,
        *,
        reason: str | None = None,
        error_code: str | None = None,
        session_status: SessionStatus | None = None,
    ) -> SessionRun:
        """Transition a run and synchronize the session summary."""
        updated, _event = await self.transition_run_with_event(
            run,
            status,
            reason=reason,
            error_code=error_code,
            session_status=session_status,
        )
        return updated

    async def transition_run_with_event(
        self,
        run: SessionRun,
        status: RunStatus,
        *,
        reason: str | None = None,
        error_code: str | None = None,
        session_status: SessionStatus | None = None,
    ) -> tuple[SessionRun, SessionEvent]:
        """Transition a run and return the durable transition event."""
        with trace_span(
            "cognition.run.transition",
            _runtime_attributes(
                run,
                {
                    "cognition.run_status": status.value,
                    "cognition.error_code": error_code or "",
                },
            ),
        ):
            event_type = f"run.{status.value}"
            event = await self.append_event(
                run,
                event_type,
                payload={"status": status.value, "reason": reason, "error_code": error_code},
            )
            updated = await self.store.update_run(
                run.id,
                status=status,
                last_activity_at=event.created_at,
                error_code=error_code,
                status_reason=reason,
            )
            if updated is None:
                updated = run

            session = await self.store.get_session(run.session_id)
            metadata = dict(session.metadata) if session is not None else {}
            metadata.update(
                {
                    "latest_run_id": run.id,
                    "latest_event_type": event.event_type,
                    "last_activity_at": event.created_at,
                }
            )
            if RunStatus.is_terminal(status):
                if metadata.get("active_run_id") == run.id:
                    metadata.pop("active_run_id", None)
            else:
                metadata["active_run_id"] = run.id

            await self.store.update_session(
                session_id=run.session_id,
                status=(session_status.value if session_status else _session_status_for_run(status)),
                metadata=metadata,
            )
            RUN_TRANSITION_COUNT.labels(status=status.value).inc()
            return updated, event

    async def refresh_message_count(self, session_id: str) -> int:
        """Synchronize session.message_count from the message projection."""
        messages = await self.store.list_messages_for_session(session_id)
        count = len(messages)
        await self.store.update_message_count(session_id, count)
        return count

    async def rebuild_messages_from_checkpoint(
        self,
        service: Any,
        run: SessionRun,
    ) -> int | None:
        """Rebuild message projection if the runtime service supports it."""
        rebuild = getattr(service, "rebuild_message_projection", None)
        if rebuild is None:
            return None
        with trace_span(
            "cognition.message_projection.rebuild",
            _runtime_attributes(run, {"cognition.projection_source": "checkpoint"}),
        ):
            rebuilt_count = int(
                await rebuild(session_id=run.session_id, thread_id=run.thread_id)
            )
            await self.append_event(
                run,
                "message.projection.rebuilt",
                payload={"message_count": rebuilt_count, "source": "checkpoint"},
            )
            await self.store.update_message_count(run.session_id, rebuilt_count)
            return rebuilt_count


def enrich_sse_event(event: dict[str, Any], durable_event: SessionEvent) -> dict[str, Any]:
    """Attach durable run/event correlation fields to an SSE event."""
    data = dict(event.get("data") or {})
    data["session_id"] = durable_event.session_id
    data["run_id"] = durable_event.run_id
    data["sequence"] = durable_event.sequence
    data["event_id"] = durable_event.id
    data["event_type"] = durable_event.event_type
    data["created_at"] = durable_event.created_at
    if durable_event.trace_id:
        data["trace_id"] = durable_event.trace_id
    return {**event, "data": data}


def _session_status_for_run(status: RunStatus) -> str:
    if status == RunStatus.WAITING_FOR_APPROVAL:
        return SessionStatus.WAITING_FOR_APPROVAL.value
    if status == RunStatus.ABORTED:
        return SessionStatus.ABORTED.value
    if status == RunStatus.FAILED:
        return SessionStatus.FAILED.value
    if status == RunStatus.DONE:
        return SessionStatus.IDLE.value
    if status == RunStatus.STALLED:
        return SessionStatus.STALLED.value
    if status == RunStatus.ABORTING:
        return SessionStatus.ABORTING.value
    return SessionStatus.ACTIVE.value


def _runtime_attributes(
    run: SessionRun,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    attributes: dict[str, Any] = {
        "cognition.session_id": run.session_id,
        "cognition.run_id": run.id,
        "cognition.thread_id": run.thread_id,
        "cognition.scope_keys": ",".join(sorted(run.effective_scope)),
    }
    if run.trace_id:
        attributes["cognition.trace_id"] = run.trace_id
    if extra:
        attributes.update(extra)
    return attributes
