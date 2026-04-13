"""Shared storage helpers for backend implementations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from server.app.models import Message, Session, SessionConfig, SessionStatus, ToolCall


def now_utc() -> datetime:
    return datetime.now(UTC)


def now_utc_iso() -> str:
    return now_utc().isoformat()


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
    title: str | None = None,
    scopes: dict[str, str] | None = None,
    agent_name: str = "default",
    metadata: dict[str, str] | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    message_count: int = 0,
    status: SessionStatus = SessionStatus.ACTIVE,
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
    role: str,
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
    "merge_session_config",
    "now_utc",
    "now_utc_iso",
]
