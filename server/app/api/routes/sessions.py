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
<<<<<<< HEAD
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from server.app.agent.resolver import RuntimeResolver
from server.app.api.dependencies import (
    get_config_store,
    get_scope_dep,
    get_session_agent_manager_dep,
    get_settings_dep,
    get_storage_backend_dep,
)
from server.app.api.models import (
    ErrorResponse,
    SessionCreate,
    SessionList,
    SessionResponse,
    SessionResumeRequest,
    SessionUpdate,
)
from server.app.api.scoping import SessionScope
from server.app.api.sse import EventBuilder, SSEStream, get_last_event_id
from server.app.llm.deep_agent_service import (
    DeepAgentStreamingService,
    DoneEvent,
    SessionAgentManager,
    TokenEvent,
    UsageEvent,
)
from server.app.llm.deep_agent_service import (
    ErrorEvent as ResumeErrorEvent,
)
from server.app.models import SessionConfig, SessionStatus
from server.app.session_manager import build_session_workspace_path, ensure_session_workspace_path
from server.app.settings import Settings
from server.app.storage.backend import StorageBackend
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/sessions", tags=["sessions"])


SessionProvider = Literal[
    "openai",
    "anthropic",
    "bedrock",
    "mock",
    "openai_compatible",
    "google_genai",
    "google_vertexai",
]


def _as_session_provider(provider: str) -> SessionProvider | None:
    return cast(SessionProvider, provider)


def _unprocessable_entity(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=detail)


async def _normalize_session_config(
    request: SessionUpdate,
    scope: SessionScope,
    config_store: ConfigStore,
    settings: Settings,
) -> None:
    """Validate and normalize session config via the canonical model selector."""
    if request.config is None:
        return

    if request.config.provider and not request.config.model:
        raise _unprocessable_entity(
            "Session config specifies provider but no model. Set config.model alongside config.provider."
        )

    if request.config.model is None:
        return

    if request.config.provider is None and request.config.provider_id is None:
        providers = await config_store.list_providers(scope=scope.get_all() or None)
        matches = [
            config
            for config in providers
            if config.enabled and config.model == request.config.model
        ]
        if not matches:
            raise _unprocessable_entity(
                f"Model '{request.config.model}' is not configured on any enabled provider. "
                "Set config.provider_id or config.provider alongside config.model."
            )

        provider_types = {config.provider for config in matches}
        if len(provider_types) > 1:
            raise _unprocessable_entity(
                f"Model '{request.config.model}' is configured on multiple provider types. "
                "Set config.provider_id or config.provider explicitly."
            )

    resolver = RuntimeResolver(config_store=config_store, settings=settings)
    probe_session = type("ProbeSession", (), {"config": request.config})()
    try:
        target = await resolver.select_model_target_for_session(
            session=probe_session,
            scope=scope.get_all() or None,
            agent_def=None,
        )
    except Exception as exc:
        from server.app.exceptions import LLMProviderConfigError

        if isinstance(exc, LLMProviderConfigError):
            raise _unprocessable_entity(str(exc)) from exc
        raise

    if request.config.provider is None:
<<<<<<< HEAD
        request.config.provider = cast(Any, target.provider)
=======
        request.config.provider = _as_session_provider(target.provider)
>>>>>>> d1e262c (Fix release image build isolation)


async def _get_scoped_session(
    session_id: str,
    store: StorageBackend,
    scope: SessionScope,
) -> Any:
    session = await store.get_session(session_id)
    if session is None or (not scope.is_empty() and not scope.matches(session.scopes)):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )
    return session


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
    agent_manager: SessionAgentManager = Depends(get_session_agent_manager_dep),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> SessionResponse:
    """Create a new session.

    Creates a new agent session for the server's current workspace.
    The workspace is determined by where the server was started (CWD).
    Sessions are stored in .cognition/sessions.json within the workspace.

    Note: Server uses global settings exclusively. No per-session configuration.
    """
    # Validate agent_name is a valid primary agent
    if not await config_store.is_valid_primary(request.agent_name):
        raise _unprocessable_entity(f"Invalid or unknown agent: {request.agent_name}")

    session_id = str(uuid.uuid4())
    thread_id = str(uuid.uuid4())  # For LangGraph checkpointing
    workspace_path = build_session_workspace_path(settings, session_id)
    ensure_session_workspace_path(workspace_path)

    # Provider/model/system_prompt are resolved from ConfigStore at message-send
    # time (scope-aware). SessionConfig is intentionally sparse at creation.
    config = SessionConfig()

    session = await store.create_session(
        session_id=session_id,
        thread_id=thread_id,
        config=config,
        title=request.title,
        scopes=scope.get_all(),
        agent_name=request.agent_name,
        metadata=request.metadata,
        workspace_path=workspace_path,
    )

    # Register session with Agent manager
    agent_manager.register_session(session_id, workspace_path)

    return SessionResponse.from_core(session)


@router.get(
    "",
    response_model=SessionList,
)
async def list_sessions(
    request: Request,
    metadata_filters: Annotated[list[str] | None, Query(alias="metadata")] = None,
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> SessionList:
    """List all sessions for the workspace.

    Returns sessions only for the server's current workspace directory.
    Sessions are isolated per workspace - they don't appear in other workspaces.
    If scoping is enabled, only returns sessions matching the current scope.
    """
    del metadata_filters

    resolved_metadata_filters: dict[str, str] = {
        key.removeprefix("metadata."): value
        for key, value in request.query_params.multi_items()
        if key.startswith("metadata.")
    }

    # Filter by scope if provided
    filter_scopes = scope.get_all() if not scope.is_empty() else None
    sessions = await store.list_sessions(
        filter_scopes=filter_scopes,
        metadata_filters=resolved_metadata_filters or None,
    )

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
    settings: Settings = Depends(get_settings_dep),
    scope: SessionScope = Depends(get_scope_dep),
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> SessionResponse:
    """Get session details.

    Returns detailed information about a specific session.
    Only returns sessions from the server's current workspace.
    """
    session = await _get_scoped_session(session_id, store, scope)

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
    settings: Settings = Depends(get_settings_dep),
    scope: SessionScope = Depends(get_scope_dep),
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> SessionResponse:
    """Update a session.

    Updates session metadata (title) or configuration (model, temperature, etc.).
    """
    await _get_scoped_session(session_id, store, scope)

    await _normalize_session_config(
        request=request,
        scope=scope,
        config_store=config_store,
        settings=settings,
    )

    if request.agent_name:
        if not await config_store.is_valid_primary(request.agent_name):
            raise _unprocessable_entity(f"Invalid or unknown agent: {request.agent_name}")

    session = await store.update_session(
        session_id=session_id,
        title=request.title,
        config=request.config,
        agent_name=request.agent_name,
        metadata=request.metadata,
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
    settings: Settings = Depends(get_settings_dep),
    agent_manager: SessionAgentManager = Depends(get_session_agent_manager_dep),
    scope: SessionScope = Depends(get_scope_dep),
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> None:
    """Delete a session.

    Deletes a session and all associated messages.
    """
    await _get_scoped_session(session_id, store, scope)

    agent_manager.unregister_session(session_id)

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
    settings: Settings = Depends(get_settings_dep),
    scope: SessionScope = Depends(get_scope_dep),
    agent_manager: SessionAgentManager = Depends(get_session_agent_manager_dep),
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> dict:
    """Abort the current operation in a session.

    Cancels any in-progress agent operation.
    """
    session = await _get_scoped_session(session_id, store, scope)

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
    settings: Settings = Depends(get_settings_dep),
    scope: SessionScope = Depends(get_scope_dep),
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> dict[str, str | bool] | StreamingResponse:
    """Resume an interrupted Deep Agents session using native Command(resume=...)."""
    session = await _get_scoped_session(session_id, store, scope)

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
