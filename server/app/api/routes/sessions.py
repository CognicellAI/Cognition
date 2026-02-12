"""Session API routes.

REST endpoints for session management.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Depends, status

from server.app.api.models import (
    SessionCreate,
    SessionResponse,
    SessionList,
    SessionUpdate,
    SessionConfig,
    ErrorResponse,
)
from server.app.settings import Settings, get_settings
# Agent import removed - using placeholder implementation

router = APIRouter(prefix="/sessions", tags=["sessions"])


# In-memory session store (replace with database in production)
_sessions: dict[str, SessionResponse] = {}


def get_settings_dependency() -> Settings:
    """Get settings dependency."""
    return get_settings()


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
        404: {"model": ErrorResponse, "description": "Project not found"},
    },
)
async def create_session(
    request: SessionCreate,
    settings: Settings = Depends(get_settings_dependency),
) -> SessionResponse:
    """Create a new session.

    Creates a new agent session for a project.
    """
    session_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())  # For LangGraph checkpointing

    # Build config from request or use defaults
    config = SessionConfig(
        provider=request.config.provider if request.config else settings.llm_provider,
        model=request.config.model if request.config else settings.llm_model,
        temperature=request.config.temperature if request.config else None,
        max_tokens=request.config.max_tokens if request.config else None,
        system_prompt=request.config.system_prompt if request.config else None,
    )

    now = datetime.utcnow()

    session = SessionResponse(
        id=session_id,
        project_id=request.project_id,
        title=request.title,
        thread_id=thread_id,
        status="active",
        config=config,
        created_at=now,
        updated_at=now,
        message_count=0,
    )

    # Store session
    _sessions[session_id] = session

    # In a real implementation:
    # 1. Create the agent with appropriate configuration
    # 2. Store session in database
    # 3. Set up checkpointing

    return session


@router.get(
    "",
    response_model=SessionList,
)
async def list_sessions(
    project_id: str | None = None,
) -> SessionList:
    """List all sessions.

    Returns a list of all sessions, optionally filtered by project.
    """
    sessions = list(_sessions.values())

    if project_id:
        sessions = [s for s in sessions if s.project_id == project_id]

    return SessionList(sessions=sessions, total=len(sessions))


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def get_session(session_id: str) -> SessionResponse:
    """Get session details.

    Returns detailed information about a specific session.
    """
    if session_id not in _sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    return _sessions[session_id]


@router.patch(
    "/{session_id}",
    response_model=SessionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def update_session(
    session_id: str,
    request: SessionUpdate,
) -> SessionResponse:
    """Update a session.

    Updates session configuration or metadata.
    """
    if session_id not in _sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    session = _sessions[session_id]

    # Update fields
    if request.title is not None:
        session.title = request.title

    if request.config is not None:
        # Merge configs
        if request.config.provider is not None:
            session.config.provider = request.config.provider
        if request.config.model is not None:
            session.config.model = request.config.model
        if request.config.temperature is not None:
            session.config.temperature = request.config.temperature
        if request.config.max_tokens is not None:
            session.config.max_tokens = request.config.max_tokens
        if request.config.system_prompt is not None:
            session.config.system_prompt = request.config.system_prompt

    session.updated_at = datetime.utcnow()

    return session


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def delete_session(session_id: str) -> None:
    """Delete a session.

    Deletes a session and all associated messages.
    """
    if session_id not in _sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    del _sessions[session_id]


@router.post(
    "/{session_id}/abort",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def abort_session(session_id: str) -> dict:
    """Abort the current operation in a session.

    Cancels any in-progress agent operation.
    """
    if session_id not in _sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # In a real implementation:
    # 1. Signal the agent to cancel
    # 2. Clean up any in-progress operations

    return {"success": True, "message": "Operation aborted"}
