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

import uuid
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from server.app.agent.agent_definition_registry import get_agent_definition_registry
from server.app.api.models import (
    ErrorResponse,
    SessionCreate,
    SessionList,
    SessionResumeRequest,
    SessionResponse,
    SessionUpdate,
)
from server.app.api.sse import EventBuilder, SSEStream, get_last_event_id
from server.app.api.scoping import SessionScope
from server.app.llm.deep_agent_service import (
    DeepAgentStreamingService,
    DoneEvent,
    ErrorEvent as ResumeErrorEvent,
    SessionAgentManager,
    TokenEvent,
    UsageEvent,
    get_session_agent_manager,
)
from server.app.llm.discovery import DiscoveryEngine
from server.app.models import SessionConfig
from server.app.models import SessionStatus
from server.app.settings import Settings, get_settings
from server.app.storage import get_storage_backend

router = APIRouter(prefix="/sessions", tags=["sessions"])


def get_settings_dependency() -> Settings:
    """Get settings dependency."""
    return get_settings()


def get_agent_manager(settings: Settings = Depends(get_settings_dependency)) -> SessionAgentManager:  # noqa: B008
    """Get the session agent manager."""
    return get_session_agent_manager(settings)


async def get_scope_dependency(
    settings: Settings = Depends(get_settings_dependency),  # noqa: B008
    user: str | None = Header(None, alias="x-cognition-scope-user"),
    project: str | None = Header(None, alias="x-cognition-scope-project"),
) -> SessionScope:
    """Get the session scope from headers."""
    scopes = {}
    if user:
        scopes["user"] = user
    if project:
        scopes["project"] = project

    scope = SessionScope(scopes)

    # Fail-closed: if scoping is enabled, require all configured scope keys
    if settings.scoping_enabled:
        missing_keys = [key for key in settings.scope_keys if not scope.get(key)]
        if missing_keys:
            header_names = [
                f"X-Cognition-Scope-{k.replace('_', '-').title()}" for k in missing_keys
            ]
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing required scope headers: {missing_keys}. Expected headers: {header_names}",
            )

    return scope


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
    settings: Settings = Depends(get_settings_dependency),  # noqa: B008
    agent_manager: SessionAgentManager = Depends(get_agent_manager),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dependency),  # noqa: B008
) -> SessionResponse:
    """Create a new session.

    Creates a new agent session for the server's current workspace.
    The workspace is determined by where the server was started (CWD).
    Sessions are stored in .cognition/sessions.json within the workspace.

    Note: Server uses global settings exclusively. No per-session configuration.
    """
    # Validate agent_name is a valid primary agent
    registry = get_agent_definition_registry()
    if registry and not registry.is_valid_primary(request.agent_name):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid or unknown agent: {request.agent_name}",
        )

    session_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())  # For LangGraph checkpointing
    workspace_path = str(settings.workspace_path)

    # Provider/model/system_prompt are resolved from ConfigRegistry at message-send
    # time (scope-aware). SessionConfig is intentionally sparse at creation.
    config = SessionConfig()

    # Get session store for this workspace
    store = get_storage_backend()

    # Create session with scope
    session = await store.create_session(
        session_id=session_id,
        thread_id=thread_id,
        config=config,
        title=request.title,
        scopes=scope.get_all(),
        agent_name=request.agent_name,
    )

    # Register session with Agent manager
    agent_manager.register_session(session_id, workspace_path)

    return SessionResponse.from_core(session)


@router.get(
    "",
    response_model=SessionList,
)
async def list_sessions(
    settings: Settings = Depends(get_settings_dependency),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dependency),  # noqa: B008
) -> SessionList:
    """List all sessions for the workspace.

    Returns sessions only for the server's current workspace directory.
    Sessions are isolated per workspace - they don't appear in other workspaces.
    If scoping is enabled, only returns sessions matching the current scope.
    """
    _ = str(settings.workspace_path)
    store = get_storage_backend()

    # Filter by scope if provided
    filter_scopes = scope.get_all() if not scope.is_empty() else None
    sessions = await store.list_sessions(filter_scopes=filter_scopes)

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
    scope: SessionScope = Depends(get_scope_dependency),
) -> SessionResponse:
    """Get session details.

    Returns detailed information about a specific session.
    Only returns sessions from the server's current workspace.
    """
    _workspace_path = str(settings.workspace_path)
    store = get_storage_backend()
    session = await store.get_session(session_id)

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Enforce scoping - check if session scope matches current scope
    if not scope.is_empty() and not scope.matches(session.scopes):
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
    scope: SessionScope = Depends(get_scope_dependency),
) -> SessionResponse:
    """Update a session.

    Updates session metadata (title) or configuration (model, temperature, etc.).
    """
    _workspace_path = str(settings.workspace_path)
    store = get_storage_backend()

    # Check session exists and scope matches
    existing_session = await store.get_session(session_id)
    if existing_session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Enforce scoping - check if session scope matches current scope
    if not scope.is_empty() and not scope.matches(existing_session.scopes):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # If model is updated but provider is missing, try to infer it
    if request.config and request.config.model and not request.config.provider:
        discovery = DiscoveryEngine(settings)
        provider = await discovery.get_provider_for_model(request.config.model)
        if provider:
            request.config.provider = provider  # type: ignore[assignment]

    # Validate agent_name if provided
    if request.agent_name:
        registry = get_agent_definition_registry()
        if registry is not None:
            if not registry.is_valid_primary(request.agent_name):
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail=f"Invalid or unknown agent: {request.agent_name}",
                )
        # If registry is None (e.g., tests), we skip validation
        # This allows tests to work without a full registry setup

    session = await store.update_session(
        session_id=session_id,
        title=request.title,
        config=request.config,
        agent_name=request.agent_name,
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
    scope: SessionScope = Depends(get_scope_dependency),
) -> None:
    """Delete a session.

    Deletes a session and all associated messages.
    """
    _workspace_path = str(settings.workspace_path)
    store = get_storage_backend()

    # Check if session exists
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Enforce scoping - check if session scope matches current scope
    if not scope.is_empty() and not scope.matches(session.scopes):
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
    scope: SessionScope = Depends(get_scope_dependency),
    agent_manager: SessionAgentManager = Depends(get_agent_manager),
) -> dict:
    """Abort the current operation in a session.

    Cancels any in-progress agent operation.
    """
    _workspace_path = str(settings.workspace_path)
    store = get_storage_backend()

    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Enforce scoping - check if session scope matches current scope
    if not scope.is_empty() and not scope.matches(session.scopes):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Signal the agent to cancel via the SessionAgentManager
    # Returns True if aborted active operation, False if no active operation
    # Both cases are considered successful (idempotent)
    await agent_manager.abort_session(session_id, session.thread_id)

    return {"success": True, "message": "Operation aborted"}


@router.post(
    "/{session_id}/resume",
    status_code=status.HTTP_200_OK,
    response_model=None,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        409: {"model": ErrorResponse, "description": "Session is not waiting for approval"},
    },
)
async def resume_session(
    session_id: str,
    request: SessionResumeRequest,
    http_request: Request,
    settings: Settings = Depends(get_settings_dependency),
    scope: SessionScope = Depends(get_scope_dependency),
) -> dict[str, str | bool] | StreamingResponse:
    """Resume an interrupted Deep Agents session using native Command(resume=...)."""
    _workspace_path = str(settings.workspace_path)
    store = get_storage_backend()

    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    if not scope.is_empty() and not scope.matches(session.scopes):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    if session.status != SessionStatus.WAITING_FOR_APPROVAL.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session {session_id} is not waiting for approval",
        )

    service = DeepAgentStreamingService(settings)

    accept_header = http_request.headers.get("accept", "")
    wants_stream = "text/event-stream" in accept_header.lower()

    if not wants_stream:
        async for event in service.resume_response(
            session_id=session_id,
            thread_id=session.thread_id,
            project_path=str(settings.workspace_path),
            decision=request.decision,
            tool_name=request.tool_name,
            args=request.args,
            scope=session.scopes,
        ):
            if isinstance(event, ResumeErrorEvent):
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=event.message,
                )
        await store.update_session(session_id=session_id, status=SessionStatus.ACTIVE.value)
        return {"success": True, "message": "Session resumed"}

    sse = SSEStream.from_settings(settings)
    last_event_id = get_last_event_id(http_request)

    async def event_generator() -> AsyncGenerator[dict[str, object], None]:
        await store.update_session(session_id=session_id, status=SessionStatus.ACTIVE.value)
        yield EventBuilder.status("resuming")

        async for event in service.resume_response(
            session_id=session_id,
            thread_id=session.thread_id,
            project_path=str(settings.workspace_path),
            decision=request.decision,
            tool_name=request.tool_name,
            args=request.args,
            scope=session.scopes,
        ):
            if isinstance(event, TokenEvent):
                yield EventBuilder.token(event.content)
            elif isinstance(event, UsageEvent):
                yield EventBuilder.usage(
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    estimated_cost=event.estimated_cost,
                    provider=event.provider,
                    model=event.model,
                )
            elif isinstance(event, DoneEvent):
                yield EventBuilder.done(
                    message_id="resume",
                    assistant_data={"content": "resumed", "tool_calls": None, "token_count": 0},
                )
            elif isinstance(event, ResumeErrorEvent):
                yield EventBuilder.error(event.message, code=event.code)
                return

    return StreamingResponse(
        sse.event_generator(event_generator(), request=http_request, last_event_id=last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
