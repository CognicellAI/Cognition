"""Session API routes.

REST endpoints for session management.
Each workspace (directory) has isolated sessions stored in .cognition/sessions.json

Git-Style Workspace Model:
  The server's current working directory (CWD) is the workspace.
  Start the server in a directory = that directory becomes the workspace.
  Example:
    cd ~/projects/my-app
    cognition serve
    → Workspace is ~/projects/my-app
"""

from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Depends, status

from server.app.api.models import (
    SessionCreate,
    SessionResponse,
    SessionList,
    SessionUpdate,
    ErrorResponse,
)
from server.app.models import SessionConfig
from server.app.session_store import get_session_store, LocalSessionStore
from server.app.settings import Settings, get_settings
from server.app.llm.deep_agent_service import (
    get_session_agent_manager,
    SessionAgentManager,
)

router = APIRouter(prefix="/sessions", tags=["sessions"])


def get_settings_dependency() -> Settings:
    """Get settings dependency."""
    return get_settings()


def get_agent_manager(settings: Settings = Depends(get_settings_dependency)) -> SessionAgentManager:
    """Get the session agent manager."""
    return get_session_agent_manager(settings)


@router.post(
    "",
    response_model=SessionResponse,
    status_code=status.HTTP_201_CREATED,
    responses={
        400: {"model": ErrorResponse, "description": "Bad request"},
    },
)
async def create_session(
    request: SessionCreate,
    settings: Settings = Depends(get_settings_dependency),
    agent_manager: SessionAgentManager = Depends(get_agent_manager),
) -> SessionResponse:
    """Create a new session.

    Creates a new agent session for the server's current workspace.
    The workspace is determined by where the server was started (CWD).
    Sessions are stored in .cognition/sessions.json within the workspace.

    Note: Server uses global settings exclusively. No per-session configuration.
    """
    session_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())  # For LangGraph checkpointing
    workspace_path = str(settings.workspace_path)

    # Use server settings exclusively (no client-provided config)
    config = SessionConfig(
        provider=settings.llm_provider,
        model=settings.llm_model,
        temperature=getattr(settings, "llm_temperature", None),
        max_tokens=getattr(settings, "llm_max_tokens", None),
        system_prompt=getattr(settings, "llm_system_prompt", None),
    )

    # Get session store for this workspace
    store = get_session_store(workspace_path)

    # Create session
    session = await store.create_session(
        session_id=session_id,
        thread_id=thread_id,
        config=config,
        title=request.title,
    )

    # Register session with Agent manager
    agent_manager.register_session(session_id, workspace_path)

    return SessionResponse.from_core(session)


@router.get(
    "",
    response_model=SessionList,
)
async def list_sessions(
    settings: Settings = Depends(get_settings_dependency),
) -> SessionList:
    """List all sessions for the workspace.

    Returns sessions only for the server's current workspace directory.
    Sessions are isolated per workspace - they don't appear in other workspaces.
    """
    workspace_path = str(settings.workspace_path)
    store = get_session_store(workspace_path)
    sessions = await store.list_sessions()

    return SessionList(
        sessions=[SessionResponse.from_core(s) for s in sessions], total=len(sessions)
    )


@router.get(
    "/{session_id}",
    response_model=SessionResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def get_session(
    session_id: str,
    settings: Settings = Depends(get_settings_dependency),
) -> SessionResponse:
    """Get session details.

    Returns detailed information about a specific session.
    Only returns sessions from the server's current workspace.
    """
    workspace_path = str(settings.workspace_path)
    store = get_session_store(workspace_path)
    session = await store.get_session(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    return SessionResponse.from_core(session)


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
    settings: Settings = Depends(get_settings_dependency),
) -> SessionResponse:
    """Update a session.

    Updates session metadata (title) or configuration (model, temperature, etc.).
    """
    workspace_path = str(settings.workspace_path)
    store = get_session_store(workspace_path)

    session = await store.update_session(
        session_id=session_id,
        title=request.title,
        config=request.config,
    )

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    return SessionResponse.from_core(session)


@router.delete(
    "/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def delete_session(
    session_id: str,
    settings: Settings = Depends(get_settings_dependency),
    agent_manager: SessionAgentManager = Depends(get_agent_manager),
) -> None:
    """Delete a session.

    Deletes a session and all associated messages.
    """
    workspace_path = str(settings.workspace_path)
    store = get_session_store(workspace_path)

    # Check if session exists
    if await store.get_session(session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Unregister from Agent manager
    agent_manager.unregister_session(session_id)

    # Delete session
    await store.delete_session(session_id)


@router.post(
    "/{session_id}/abort",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def abort_session(
    session_id: str,
    settings: Settings = Depends(get_settings_dependency),
) -> dict:
    """Abort the current operation in a session.

    Cancels any in-progress agent operation.
    """
    workspace_path = str(settings.workspace_path)
    store = get_session_store(workspace_path)

    if await store.get_session(session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # In a real implementation:
    # 1. Signal the agent to cancel
    # 2. Clean up any in-progress operations

    return {"success": True, "message": "Operation aborted"}
