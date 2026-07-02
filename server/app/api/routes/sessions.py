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
from typing import Annotated, Any, Literal, cast

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse

from server.app.agent.resolver import RuntimeResolver
from server.app.agent.token_counter import count_text_tokens
from server.app.api.dependencies import (
    get_config_store,
    get_scope_dep,
    get_session_agent_manager_dep,
    get_settings_dep,
    get_storage_backend_dep,
)
from server.app.api.models import (
    ContextDebugResponse,
    ContextMessageDebug,
    ErrorResponse,
    SessionCreate,
    SessionEventList,
    SessionEventResponse,
    SessionList,
    SessionResponse,
    SessionResumeRequest,
    SessionRunList,
    SessionRunResponse,
    SessionUpdate,
)
from server.app.api.scoping import SessionScope
from server.app.api.sse import EventBuilder, SSEStream, get_last_event_id
from server.app.llm.deep_agent_service import (
    ContextEvent,
    DoneEvent,
    HitlDecisionEvent,
    SessionAgentManager,
    TokenEvent,
    UsageEvent,
)
from server.app.llm.deep_agent_service import (
    ErrorEvent as ResumeErrorEvent,
)
from server.app.models import RunStatus, SessionConfig, SessionStatus
from server.app.runtime_projection import RuntimeProjectionService
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


def _policy_dict(policy: Any | None) -> dict[str, Any]:
    if policy is None:
        return {}
    if hasattr(policy, "model_dump"):
        return cast(dict[str, Any], policy.model_dump(exclude_none=True, mode="json"))
    if isinstance(policy, dict):
        return dict(policy)
    return {}


async def _resolve_context_policy(
    session: Any,
    config_store: ConfigStore,
    scope: dict[str, str] | None,
) -> dict[str, Any]:
    agent_def = await config_store.get_agent_definition(session.agent_name, scope)
    policy: dict[str, Any] = {}
    if agent_def is not None:
        policy = _policy_dict(agent_def.config.context_policy)
        if agent_def.config.tool_token_limit_before_evict is not None:
            policy.setdefault(
                "tool_token_limit_before_evict",
                agent_def.config.tool_token_limit_before_evict,
            )

    defaults = await config_store.get_global_agent_defaults(scope)
    if not policy:
        policy = _policy_dict(defaults.context_policy)
    if defaults.tool_token_limit_before_evict is not None:
        policy.setdefault(
            "tool_token_limit_before_evict",
            defaults.tool_token_limit_before_evict,
        )
    return policy


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
        request.config.provider = _as_session_provider(target.provider)


async def _get_scoped_session(
    session_id: str,
    store: StorageBackend,
    scope: SessionScope,
) -> Any:
    session = await store.get_session(session_id)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )
    if not scope.is_empty() and not scope.matches(session.scopes):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                f"Scope mismatch: session '{session_id}' was created with scope "
                f"{session.scopes}, but request has scope {scope.get_all()}. "
                "Session scope is immutable after creation."
            ),
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
    effective_scope = scope.get_all() or None
    if not await config_store.is_valid_primary(request.agent_name, effective_scope):
        raise _unprocessable_entity(f"Invalid or unknown agent: {request.agent_name}")

    # Idempotency: return existing session if idempotency_key matches
    if request.idempotency_key:
        existing = await _find_session_by_idempotency_key(
            store, request.idempotency_key, scope.get_all()
        )
        if existing is not None:
            return SessionResponse.from_core(existing)

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
        metadata={
            **(request.metadata or {}),
            **({"idempotency_key": request.idempotency_key} if request.idempotency_key else {}),
        },
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


@router.get(
    "/{session_id}/context",
    response_model=ContextDebugResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def get_session_context(
    session_id: str,
    scope: SessionScope = Depends(get_scope_dep),
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> ContextDebugResponse:
    """Return scoped, redacted context policy and token-accounting metadata."""
    session = await _get_scoped_session(session_id, store, scope)
    scope_dict = scope.get_all() if not scope.is_empty() else session.scopes
    messages = await store.list_messages_for_session(session_id)
    debug_messages = [
        ContextMessageDebug(
            id=message.id,
            role=message.role,
            token_count=message.token_count,
            estimated_tokens=message.token_count
            if message.token_count is not None
            else count_text_tokens(message.content),
            created_at=message.created_at,
        )
        for message in messages
    ]

    return ContextDebugResponse(
        session_id=session.id,
        thread_id=session.thread_id,
        agent_name=session.agent_name,
        scope_keys=sorted(scope_dict),
        policy=await _resolve_context_policy(session, config_store, scope_dict or None),
        message_count=len(messages),
        estimated_tokens=sum(message.estimated_tokens for message in debug_messages),
        messages=debug_messages,
    )


@router.get(
    "/{session_id}/runs",
    response_model=SessionRunList,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def list_session_runs(
    session_id: str,
    scope: SessionScope = Depends(get_scope_dep),
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> SessionRunList:
    """List durable runs for a session."""
    session = await _get_scoped_session(session_id, store, scope)
    runs = await store.list_runs(session.id)
    return SessionRunList(
        runs=[SessionRunResponse.from_core(run) for run in runs],
        total=len(runs),
    )


@router.get(
    "/{session_id}/runs/{run_id}",
    response_model=SessionRunResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Run not found"},
    },
)
async def get_session_run(
    session_id: str,
    run_id: str,
    scope: SessionScope = Depends(get_scope_dep),
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> SessionRunResponse:
    """Get durable state for one session run."""
    session = await _get_scoped_session(session_id, store, scope)
    run = await store.get_run(run_id)
    if run is None or run.session_id != session.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Run not found: {run_id}",
        )
    if not scope.is_empty() and run.effective_scope != scope.get_all():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Scope mismatch: run scope is immutable after creation.",
        )
    return SessionRunResponse.from_core(run)


@router.get(
    "/{session_id}/events",
    response_model=SessionEventList,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
    },
)
async def list_session_events(
    session_id: str,
    run_id: str | None = None,
    after_sequence: int | None = None,
    limit: int = 100,
    visibility: Literal["internal", "builder", "end_user"] | None = None,
    event_type: str | None = None,
    scope: SessionScope = Depends(get_scope_dep),
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> SessionEventList:
    """List durable runtime events for a session."""
    session = await _get_scoped_session(session_id, store, scope)
    safe_limit = max(1, min(limit, 500))
    events = await store.list_events(
        session.id,
        run_id=run_id,
        after_sequence=after_sequence,
        limit=safe_limit + 1,
        visibility=visibility,
        event_type=event_type,
    )
    visible_events = [
        event
        for event in events
        if scope.is_empty() or event.effective_scope == scope.get_all()
    ]
    has_more = len(visible_events) > safe_limit
    page = visible_events[:safe_limit]
    return SessionEventList(
        events=[SessionEventResponse.from_core(event) for event in page],
        total=len(page),
        has_more=has_more,
    )


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
        effective_scope = scope.get_all() or None
        if not await config_store.is_valid_primary(request.agent_name, effective_scope):
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
    active_run = await store.get_active_run(session_id)
    projection = RuntimeProjectionService(store)
    if active_run is not None:
        await projection.transition_run(
            active_run,
            RunStatus.ABORTING,
            reason="Abort requested",
            session_status=SessionStatus.ABORTING,
        )

    await agent_manager.abort_session(session_id, session.thread_id)
    if active_run is not None:
        await projection.transition_run(
            active_run,
            RunStatus.ABORTED,
            reason="Execution aborted",
            session_status=SessionStatus.ABORTED,
        )
        agent_manager.release_sandbox_backend(session_id)

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
    agent_manager: SessionAgentManager = Depends(get_session_agent_manager_dep),  # noqa: B008
) -> dict[str, str | bool] | StreamingResponse:
    """Resume an interrupted Deep Agents session using native Command(resume=...)."""
    session = await _get_scoped_session(session_id, store, scope)

    if session.status != SessionStatus.WAITING_FOR_APPROVAL.value:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session {session_id} is not waiting for approval",
        )

    service = agent_manager.get_service(session_id)
    if service is None:
        service = agent_manager.register_session(session_id, session.workspace_path)

    active_run = await store.get_active_run(session_id)
    projection = RuntimeProjectionService(store)
    if active_run is not None:
        active_run = await projection.transition_run(
            active_run,
            RunStatus.ACTIVE,
            reason="Human approval resolved",
            session_status=SessionStatus.ACTIVE,
        )

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
                if active_run is not None:
                    await projection.transition_run(
                        active_run,
                        RunStatus.FAILED,
                        reason=event.message,
                        error_code=event.code,
                        session_status=SessionStatus.FAILED,
                    )
                    agent_manager.release_sandbox_backend(session_id)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=event.message,
                )
            if isinstance(event, DoneEvent) and active_run is not None:
                await projection.transition_run(
                    active_run,
                    RunStatus.DONE,
                    session_status=SessionStatus.IDLE,
                )
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
            elif isinstance(event, HitlDecisionEvent):
                if active_run is not None:
                    await projection.append_event(
                        active_run,
                        "hitl.decision",
                        payload={
                            "decision": event.decision,
                            "tool_name": event.tool_name,
                            "edited_arg_keys": event.edited_arg_keys,
                            "has_rejection_message": event.has_rejection_message,
                        },
                    )
                yield EventBuilder.hitl_decision(
                    decision=event.decision,
                    tool_name=event.tool_name,
                    session_id=event.session_id,
                    run_id=event.run_id,
                    scope_keys=event.scope_keys,
                    edited_arg_keys=event.edited_arg_keys,
                    has_rejection_message=event.has_rejection_message,
                )
            elif isinstance(event, UsageEvent):
                yield EventBuilder.usage(
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    estimated_cost=event.estimated_cost,
                    provider=event.provider,
                    model=event.model,
                )
            elif isinstance(event, ContextEvent):
                yield EventBuilder.context(
                    action=event.action,
                    session_id=event.session_id,
                    run_id=event.run_id,
                    scope_keys=event.scope_keys,
                    policy=event.policy,
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    message_count=event.message_count,
                    retained_messages=event.retained_messages,
                    evicted_messages=event.evicted_messages,
                    summarized_messages=event.summarized_messages,
                    offloaded_messages=event.offloaded_messages,
                    summary_id=event.summary_id,
                    artifact_id=event.artifact_id,
                )
            elif isinstance(event, DoneEvent):
                if active_run is not None:
                    await projection.transition_run(
                        active_run,
                        RunStatus.DONE,
                        session_status=SessionStatus.IDLE,
                    )
                yield EventBuilder.done(
                    message_id="resume",
                    assistant_data={"content": "resumed", "tool_calls": None, "token_count": 0},
                )
            elif isinstance(event, ResumeErrorEvent):
                if active_run is not None:
                    await projection.transition_run(
                        active_run,
                        RunStatus.FAILED,
                        reason=event.message,
                        error_code=event.code,
                        session_status=SessionStatus.FAILED,
                    )
                    agent_manager.release_sandbox_backend(session_id)
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


@router.post(
    "/{session_id}/cancel",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        409: {"model": ErrorResponse, "description": "Session cannot be cancelled in current state"},
    },
)
async def cancel_session(
    session_id: str,
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
    agent_manager: SessionAgentManager = Depends(get_session_agent_manager_dep),
    scope: SessionScope = Depends(get_scope_dep),
) -> dict[str, str | bool]:
    """Cancel a session with proper lifecycle state transitions.

    Transitions: current → ABORTING → ABORTED.
    Terminal states (done, expired) cannot be cancelled.
    """
    session = await _get_scoped_session(session_id, store, scope)
    current = SessionStatus(session.status)
    active_run = await store.get_active_run(session_id)
    projection = RuntimeProjectionService(store)

    if SessionStatus.is_terminal(current):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session '{session_id}' is in terminal state '{current.value}'",
        )

    # Transition to CANCELLING
    await _transition_status(store, session_id, current, SessionStatus.ABORTING)
    if active_run is not None:
        await projection.transition_run(
            active_run,
            RunStatus.ABORTING,
            reason="Cancel requested",
            session_status=SessionStatus.ABORTING,
        )

    # Abort the agent runtime
    await agent_manager.abort_session(session_id, session.thread_id)

    # Transition to CANCELLED
    if active_run is not None:
        await projection.transition_run(
            active_run,
            RunStatus.ABORTED,
            reason="Session cancelled",
            session_status=SessionStatus.ABORTED,
        )
    else:
        await _transition_status(store, session_id, SessionStatus.ABORTING, SessionStatus.ABORTED)
    agent_manager.release_sandbox_backend(session_id)

    return {
        "success": True,
        "message": "Session cancelled",
        "status": SessionStatus.ABORTED.value,
    }


@router.post(
    "/{session_id}/pause",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        409: {"model": ErrorResponse, "description": "Session cannot be paused in current state"},
    },
)
async def pause_session(
    session_id: str,
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),
) -> dict[str, str | bool]:
    """Pause an active session.

    Transitions: active → idle.
    Only active sessions can be paused.
    """
    session = await _get_scoped_session(session_id, store, scope)
    current = SessionStatus(session.status)

    if current != SessionStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session '{session_id}' is not active (current: '{current.value}')",
        )

    if not SessionStatus.can_transition(current, SessionStatus.IDLE):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot pause session '{session_id}' from '{current.value}'",
        )

    await store.update_session(session_id=session_id, status=SessionStatus.IDLE.value)
    return {"success": True, "message": "Session paused", "status": SessionStatus.IDLE.value}


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────


async def _find_session_by_idempotency_key(
    store: StorageBackend,
    idempotency_key: str,
    scope: dict[str, str],
) -> Any | None:
    """Find an existing session by idempotency key in the given scope."""
    sessions = await store.list_sessions()
    for s in sessions:
        if s.metadata and s.metadata.get("idempotency_key") == idempotency_key:
            return s
    return None


async def _transition_status(
    store: StorageBackend,
    session_id: str,
    from_status: SessionStatus,
    to_status: SessionStatus,
) -> None:
    """Transition a session to a new status if allowed."""
    if not SessionStatus.can_transition(from_status, to_status):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Cannot transition session '{session_id}' "
            f"from '{from_status.value}' to '{to_status.value}'",
        )
    await store.update_session(session_id=session_id, status=to_status.value)
