"""Shared storage helpers for backend implementations."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from itertools import combinations
from typing import Any, Literal

from server.app.models import (
    Message,
    RunStatus,
    RuntimeTask,
    Session,
    SessionConfig,
    SessionEvent,
    SessionRun,
    SessionStatus,
    TaskStatus,
    ToolCall,
)


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_utc_iso() -> str:
    return now_utc().isoformat()


def effective_scope_key(scope: dict[str, str] | None) -> str:
    """Return a stable non-secret hash for exact-scope database indexes."""
    canonical = json.dumps(scope or {}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def inherited_scope_candidates(scope: dict[str, str] | None) -> list[dict[str, str]]:
    """Return all scope subsets that may be inherited by ``scope``.

    Exact runtime resources use only the complete effective scope. Generic
    configuration such as providers, tools, skills, MCP servers, sandbox
    profiles, and global defaults may inherit from broader scopes. SQL backends
    use these candidates to constrain reads by indexed ``scope_key`` values
    before verifying the stored scope JSON.
    """
    target = scope or {}
    keys = sorted(target)
    candidates: list[dict[str, str]] = [{}]
    for size in range(1, len(keys) + 1):
        for names in combinations(keys, size):
            candidates.append({name: target[name] for name in names})
    return candidates


def inherited_scope_keys(scope: dict[str, str] | None) -> list[str]:
    """Return indexed lookup keys for all inherited scope candidates."""
    return [effective_scope_key(candidate) for candidate in inherited_scope_candidates(scope)]


def canonical_json_digest(value: Any) -> str:
    """Return a stable SHA-256 digest for a JSON-compatible value."""
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def merge_session_config(existing: SessionConfig, incoming: SessionConfig) -> SessionConfig:
    return SessionConfig(
        provider=incoming.provider or existing.provider,
        model=incoming.model or existing.model,
        temperature=(
            incoming.temperature if incoming.temperature is not None else existing.temperature
        ),
        max_tokens=(
            incoming.max_tokens if incoming.max_tokens is not None else existing.max_tokens
        ),
        recursion_limit=(
            incoming.recursion_limit
            if incoming.recursion_limit is not None
            else existing.recursion_limit
        ),
        response_format=(
            incoming.response_format
            if incoming.response_format is not None
            else existing.response_format
        ),
        system_prompt=(
            incoming.system_prompt if incoming.system_prompt is not None else existing.system_prompt
        ),
    )


def make_session(
    *,
    session_id: str,
    workspace_path: str,
    thread_id: str,
    config: SessionConfig,
    agent_name: str,
    title: str | None = None,
    scopes: dict[str, str] | None = None,
    metadata: dict[str, str] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    message_count: int = 0,
    status: SessionStatus = SessionStatus.IDLE,
) -> Session:
    created = created_at or now_utc_iso()
    updated = updated_at or created
    return Session(
        id=session_id,
        workspace_path=workspace_path,
        title=title,
        thread_id=thread_id,
        status=status,
        config=config,
        scopes=scopes or {},
        created_at=created,
        updated_at=updated,
        message_count=message_count,
        agent_name=agent_name,
        metadata=metadata or {},
    )


def make_message(
    *,
    message_id: str,
    session_id: str,
    role: Literal["user", "assistant", "system", "tool"],
    content: str | None,
    parent_id: str | None = None,
    created_at: datetime | None = None,
    tool_calls: list[ToolCall] | None = None,
    tool_call_id: str | None = None,
    token_count: int | None = None,
    model_used: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Message:
    return Message(
        id=message_id,
        session_id=session_id,
        role=role,
        content=content,
        parent_id=parent_id,
        created_at=created_at or now_utc(),
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
        token_count=token_count,
        model_used=model_used,
        metadata=metadata,
    )


def make_session_run(
    *,
    run_id: str,
    session_id: str,
    thread_id: str,
    status: RunStatus = RunStatus.QUEUED,
    effective_scope: dict[str, str] | None = None,
    agent_revision: int = 1,
    runtime_manifest: dict[str, Any] | None = None,
    manifest_digest: str | None = None,
    attempt: int = 1,
    idempotency_key: str | None = None,
    parent_run_id: str | None = None,
    started_at: str | None = None,
    last_activity_at: str | None = None,
    completed_at: str | None = None,
    error_code: str | None = None,
    status_reason: str | None = None,
    trace_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    task_id: str | None = None,
) -> SessionRun:
    """Create a SessionRun with consistent timestamps."""
    created = created_at or now_utc_iso()
    updated = updated_at or created
    return SessionRun(
        id=run_id,
        session_id=session_id,
        thread_id=thread_id,
        status=status,
        effective_scope=effective_scope or {},
        agent_revision=agent_revision,
        runtime_manifest=dict(runtime_manifest or {}),
        manifest_digest=manifest_digest or canonical_json_digest(runtime_manifest or {}),
        attempt=attempt,
        idempotency_key=idempotency_key,
        parent_run_id=parent_run_id,
        started_at=started_at,
        last_activity_at=last_activity_at,
        completed_at=completed_at,
        error_code=error_code,
        status_reason=status_reason,
        trace_id=trace_id,
        metadata=metadata or {},
        created_at=created,
        updated_at=updated,
        task_id=task_id,
    )


def make_runtime_task(
    *,
    task_id: str,
    context_id: str,
    session_id: str,
    agent_name: str,
    status: TaskStatus = TaskStatus.SUBMITTED,
    effective_scope: dict[str, str] | None = None,
    current_run_id: str | None = None,
    last_run_id: str | None = None,
    idempotency_key: str | None = None,
    status_reason: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
) -> RuntimeTask:
    """Create a RuntimeTask with consistent timestamps and copied scope."""
    created = created_at or now_utc_iso()
    return RuntimeTask(
        id=task_id,
        context_id=context_id,
        session_id=session_id,
        agent_name=agent_name,
        status=status,
        effective_scope=dict(effective_scope or {}),
        current_run_id=current_run_id,
        last_run_id=last_run_id,
        idempotency_key=idempotency_key,
        status_reason=status_reason,
        metadata=dict(metadata or {}),
        created_at=created,
        updated_at=updated_at or created,
    )


def make_session_event(
    *,
    event_id: str,
    session_id: str,
    run_id: str,
    sequence: int,
    event_type: str,
    payload: dict[str, Any] | None = None,
    effective_scope: dict[str, str] | None = None,
    visibility: Literal["internal", "builder", "end_user"] = "builder",
    trace_id: str | None = None,
    span_id: str | None = None,
    created_at: str | None = None,
    task_id: str | None = None,
) -> SessionEvent:
    """Create a SessionEvent with consistent timestamps."""
    return SessionEvent(
        id=event_id,
        session_id=session_id,
        run_id=run_id,
        sequence=sequence,
        event_type=event_type,
        visibility=visibility,
        payload=payload or {},
        effective_scope=effective_scope or {},
        trace_id=trace_id,
        span_id=span_id,
        created_at=created_at or now_utc_iso(),
        task_id=task_id,
    )


def filter_sessions(
    sessions: list[Session],
    filter_scopes: dict[str, str] | None = None,
    metadata_filters: dict[str, str] | None = None,
) -> list[Session]:
    filtered = sessions
    if filter_scopes:
        filtered = [
            session
            for session in filtered
            if all(session.scopes.get(key) == value for key, value in filter_scopes.items())
        ]
    if metadata_filters:
        filtered = [
            session
            for session in filtered
            if all(session.metadata.get(key) == value for key, value in metadata_filters.items())
        ]
    return filtered


__all__ = [
    "filter_sessions",
    "make_message",
    "make_session",
    "make_session_event",
    "make_session_run",
    "merge_session_config",
    "now_utc",
    "now_utc_iso",
]
