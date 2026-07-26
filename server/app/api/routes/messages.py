"""Message API routes.

REST endpoints for sending messages and receiving SSE streams.

Git-Style Workspace Model:
  The server's current working directory (CWD) is the workspace.
  All messages are scoped to the server's workspace.

Persistence contract:
  LangGraph checkpoint state is authoritative for runtime conversation state.
  The custom ``messages`` table is a read-optimized projection used by the API.
  This route still performs the normal projection writes, but backends may
  rebuild that projection from checkpoint state if the projection drifts.
"""

from __future__ import annotations

import time
import uuid
from collections.abc import AsyncGenerator, Mapping
from typing import Any, cast
from urllib.parse import urlsplit

import httpx
import structlog
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import StreamingResponse

from server.app.agent.task_runtime import AgentTaskRuntime, SubmitTask
from server.app.api.dependencies import (
    get_artifact_store,
    get_config_store,
    get_rate_limiter_dep,
    get_scope_dep,
    get_session_agent_manager_dep,
    get_settings_dep,
    get_storage_backend_dep,
)
from server.app.api.models import (
    ErrorResponse,
    MessageCreate,
    MessageList,
    MessageResponse,
    ToolCallResponse,
)
from server.app.api.scoping import SessionScope
from server.app.api.sse import EventBuilder, SSEStream, get_last_event_id
from server.app.exceptions import RuntimeTaskConflictError
from server.app.llm.deep_agent_service import (
    CallbackEvent,
    ContextEvent,
    DelegationEvent,
    DoneEvent,
    ErrorEvent,
    HeartbeatEvent,
    InterruptEvent,
    PlanningEvent,
    RunStateEvent,
    SandboxLifecycleEvent,
    SessionAgentManager,
    StatusEvent,
    StepCompleteEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    ToolSafetyEvent,
    UsageEvent,
)
from server.app.models import RunStatus, SessionRun, SessionStatus, ToolCall
from server.app.observability import (
    STORAGE_OPERATION_DURATION,
    STORAGE_OPERATIONS_TOTAL,
    STRICT_EXECUTION_REJECTIONS_TOTAL,
    storage_backend_label,
)
from server.app.rate_limiter import RateLimiter
from server.app.runtime_projection import (
    ActiveRunConflictError,
    RuntimeProjectionService,
    enrich_sse_event,
)
from server.app.settings import Settings
from server.app.storage.artifact_store import ArtifactStore
from server.app.storage.backend import StorageBackend
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/sessions/{session_id}/messages", tags=["messages"])
logger = structlog.get_logger(__name__)

_SENSITIVE_SANDBOX_METADATA_KEY_PARTS = (
    "auth",
    "authorization",
    "credential",
    "password",
    "secret",
    "token",
    "x-aws-proxy-auth",
)


async def _get_scoped_session_for_messages(
    store: StorageBackend,
    session_id: str,
    effective_scope: dict[str, str] | None,
) -> Any | None:
    """Get a scoped session and record bounded storage outcome metrics."""
    started_at = time.monotonic()
    backend = storage_backend_label(store)
    try:
        session = await store.get_session(session_id, effective_scope)
    except Exception:
        STORAGE_OPERATIONS_TOTAL.labels(
            backend=backend,
            operation="get_session",
            result="failure",
        ).inc()
        STORAGE_OPERATION_DURATION.labels(
            backend=backend,
            operation="get_session",
        ).observe(time.monotonic() - started_at)
        raise
    result = "success" if session is not None else "not_found"
    STORAGE_OPERATIONS_TOTAL.labels(
        backend=backend,
        operation="get_session",
        result=result,
    ).inc()
    STORAGE_OPERATION_DURATION.labels(
        backend=backend,
        operation="get_session",
    ).observe(time.monotonic() - started_at)
    return session


async def _refresh_session_message_count(
    store: StorageBackend,
    session_id: str,
    effective_scope: dict[str, str] | None = None,
) -> int:
    """Synchronize the durable session message_count projection."""
    messages_for_session = await store.list_messages_for_session(
        session_id,
        effective_scope,
    )
    count = len(messages_for_session)
    await store.update_message_count(session_id, count, effective_scope)
    return count


def _is_sensitive_sandbox_metadata_key(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in _SENSITIVE_SANDBOX_METADATA_KEY_PARTS)


def _sanitize_sandbox_metadata(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _sanitize_sandbox_metadata(nested)
            for key, nested in value.items()
            if not _is_sensitive_sandbox_metadata_key(str(key))
        }
    if isinstance(value, list):
        return [_sanitize_sandbox_metadata(item) for item in value]
    return value


async def _sandbox_lifecycle_sse(
    event: SandboxLifecycleEvent,
    *,
    projection: RuntimeProjectionService | None = None,
    run: SessionRun | None = None,
) -> dict[str, Any]:
    metadata = cast(dict[str, Any], _sanitize_sandbox_metadata(event.metadata))
    sse: dict[str, Any] = cast(
        dict[str, Any],
        EventBuilder.sandbox_lifecycle(
            sandbox_id=event.sandbox_id,
            phase=event.phase,
            sandbox_backend=event.sandbox_backend,
            duration_ms=event.duration_ms,
            exit_code=event.exit_code,
            is_warm_pool_hit=event.is_warm_pool_hit,
            metadata=metadata,
        ),
    )
    if projection is not None and run is not None:
        durable = await projection.append_event(
            run,
            f"sandbox.{event.phase}",
            payload={
                "sandbox_id": event.sandbox_id,
                "sandbox_backend": event.sandbox_backend,
                "duration_ms": event.duration_ms,
                "exit_code": event.exit_code,
                "is_warm_pool_hit": event.is_warm_pool_hit,
                "metadata": metadata,
            },
        )
        sse = enrich_sse_event(sse, durable)
    return sse


async def _release_sandbox_lifecycle_sse_events(
    agent_manager: SessionAgentManager,
    session_id: str,
    *,
    projection: RuntimeProjectionService | None = None,
    run: SessionRun | None = None,
) -> list[dict[str, Any]]:
    """Release sandbox resources and return lifecycle events emitted by teardown."""
    agent_manager.release_sandbox_backend(session_id)
    events: list[dict[str, Any]] = []
    for event in agent_manager.drain_sandbox_events(session_id):
        events.append(await _sandbox_lifecycle_sse(event, projection=projection, run=run))
    return events


async def _touch_session_activity(
    store: StorageBackend,
    session_id: str,
    *,
    status_value: str | None = None,
    effective_scope: dict[str, str] | None = None,
) -> None:
    """Advance updated_at for durable runtime activity.

    AEP and other orchestrators use session.updated_at as a progress signal, so
    streamed runtime activity must update the durable session row even when it
    does not create a chat message.
    """
    if status_value is not None:
        await store.update_session(
            session_id=session_id,
            status=status_value,
            effective_scope=effective_scope,
        )
        return

    session = await _get_scoped_session_for_messages(store, session_id, effective_scope)
    if session is not None:
        await store.update_session(
            session_id=session_id,
            status=session.status.value,
            effective_scope=effective_scope,
        )


async def agent_event_stream(
    session_id: str,
    thread_id: str,
    content: str,
    workspace_path: str,
    settings: Settings,
    agent_manager: SessionAgentManager,
    store: StorageBackend,
    scope: dict[str, str] | None = None,
    parent_message_id: str | None = None,
    run: SessionRun | None = None,
    projection: RuntimeProjectionService | None = None,
) -> AsyncGenerator[dict, None]:
    """Generate agent events as SSE using DeepAgents.

    Uses DeepAgents for multi-step task completion:
    1. Automatic ReAct loop (LLM → tool → LLM until complete)
    2. State persistence via thread_id checkpointing
    3. Built-in planning with write_todos
    4. Context management and streaming

    Args:
        session_id: The session to stream events for.
        thread_id: Thread ID for state persistence.
        content: User message content.
        workspace_path: Path to the workspace root.
        settings: Application settings.
        agent_manager: Manager for session agent lifecycle.
        scope: Optional builder-authorized runtime isolation scope.
            Propagated to the agent runtime so that scope-aware
            backends (e.g. ConfigRegistrySkillsBackend) can filter
            skills, providers, and other config by application scope.
        parent_message_id: Optional user message ID used as the parent for
            projected assistant tool-call messages.

    Yields:
        SSE events as dictionaries. The final 'done' event contains
        the assistant message data for persistence.
    """
    scope_keys: list[str] | None = list(scope.keys()) if scope else None

    try:
        # Get or create agent service for this session
        service = agent_manager.get_service(session_id)

        if not service:
            service = agent_manager.register_session(session_id, workspace_path)

        session = await _get_scoped_session_for_messages(store, session_id, scope)

        if not session:
            yield EventBuilder.error("Session not found", code="SESSION_NOT_FOUND")
            return

        # Get system prompt from session or use default
        system_prompt = None
        if session and session.config.system_prompt:
            system_prompt = session.config.system_prompt

        # Accumulate assistant message data for persistence.
        assistant_content_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        tool_call_message_ids: dict[str, str] = {}
        metadata: dict[str, Any] = {}

        # Drain sandbox lifecycle events queued during agent setup
        for se in agent_manager.drain_sandbox_events(session_id):
            yield await _sandbox_lifecycle_sse(se, projection=projection, run=run)

        # Stream response using DeepAgents with multi-step support
        # Pass agent_manager to enable abort functionality
        async for event in service.stream_response(
            session_id=session_id,
            thread_id=thread_id,
            project_path=workspace_path,
            content=content,
            system_prompt=system_prompt,
            manager=agent_manager,
            scope=scope,
            run_id=run.id if run is not None else None,
        ):
            if isinstance(event, TokenEvent):
                assistant_content_parts.append(event.content)
                sse = EventBuilder.token(event.content)
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "message.assistant.delta",
                        payload={"length": len(event.content)},
                        visibility="internal",
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, ToolCallEvent):
                tool_call = {
                    "name": event.name,
                    "args": event.args,
                    "id": event.tool_call_id,
                }
                tool_calls.append(tool_call)
                tool_call_message_id = str(uuid.uuid4())
                tool_call_message_ids[event.tool_call_id] = tool_call_message_id
                await store.create_message(
                    message_id=tool_call_message_id,
                    session_id=session_id,
                    role="assistant",
                    content=None,
                    parent_id=parent_message_id,
                    tool_calls=[
                        ToolCall(
                            name=event.name,
                            args=event.args,
                            id=event.tool_call_id,
                        )
                    ],
                    metadata={
                        "projection_source": "runtime_tool_call",
                        **(
                            {"task_id": run.task_id, "run_id": run.id}
                            if run is not None and run.task_id is not None
                            else {}
                        ),
                    },
                    effective_scope=scope,
                )
                await _refresh_session_message_count(store, session_id, scope)
                sse = EventBuilder.tool_call(
                    name=event.name,
                    args=event.args,
                    tool_call_id=event.tool_call_id,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "tool.call.started",
                        payload={
                            "tool_name": event.name,
                            "tool_call_id": event.tool_call_id,
                            "args": event.args,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, ToolResultEvent):
                await store.create_message(
                    message_id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="tool",
                    content=event.output,
                    parent_id=tool_call_message_ids.get(event.tool_call_id),
                    tool_call_id=event.tool_call_id,
                    metadata={
                        "exit_code": event.exit_code,
                        "projection_source": "runtime_tool_result",
                        **(
                            {"task_id": run.task_id, "run_id": run.id}
                            if run is not None and run.task_id is not None
                            else {}
                        ),
                    },
                    effective_scope=scope,
                )
                await _refresh_session_message_count(store, session_id, scope)
                sse = EventBuilder.tool_result(
                    tool_call_id=event.tool_call_id,
                    output=event.output,
                    exit_code=event.exit_code,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "tool.call.completed" if event.exit_code == 0 else "tool.call.failed",
                        payload={
                            "tool_call_id": event.tool_call_id,
                            "exit_code": event.exit_code,
                            "output_length": len(event.output or ""),
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, ToolSafetyEvent):
                await _touch_session_activity(
                    store,
                    session_id,
                    effective_scope=scope,
                )
                sse = EventBuilder.tool_safety(
                    action=event.action,
                    tool_name=event.tool_name,
                    tool_call_id=event.tool_call_id,
                    fields=event.fields,
                    overwritten_fields=event.overwritten_fields,
                    errors=event.errors,
                    message=event.message,
                    session_id=event.session_id,
                    run_id=event.run_id,
                    scope_keys=event.scope_keys or scope_keys,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        f"tool.call.{event.action}",
                        payload={
                            "tool_name": event.tool_name,
                            "tool_call_id": event.tool_call_id,
                            "action": event.action,
                            "fields": event.fields,
                            "overwritten_fields": event.overwritten_fields,
                            "error_count": len(event.errors or []),
                            "message": event.message,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, ContextEvent):
                await _touch_session_activity(
                    store,
                    session_id,
                    effective_scope=scope,
                )
                sse = EventBuilder.context(
                    action=event.action,
                    session_id=event.session_id,
                    run_id=event.run_id,
                    scope_keys=event.scope_keys or scope_keys,
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
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "context.summarized"
                        if event.action == "summarized"
                        else f"context.{event.action}",
                        payload={
                            "action": event.action,
                            "message_count": event.message_count,
                            "retained_messages": event.retained_messages,
                            "evicted_messages": event.evicted_messages,
                            "summarized_messages": event.summarized_messages,
                            "summary_id": event.summary_id,
                            "artifact_id": event.artifact_id,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, PlanningEvent):
                await _touch_session_activity(
                    store,
                    session_id,
                    effective_scope=scope,
                )
                sse = EventBuilder.planning(event.todos)
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "planning.updated",
                        payload={"todo_count": len(event.todos), "todos": event.todos},
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, StepCompleteEvent):
                await _touch_session_activity(
                    store,
                    session_id,
                    effective_scope=scope,
                )
                sse = EventBuilder.step_complete(
                    step_number=event.step_number,
                    total_steps=event.total_steps,
                    description=event.description,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "step.completed",
                        payload={
                            "step_number": event.step_number,
                            "total_steps": event.total_steps,
                            "description": event.description,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, InterruptEvent):
                run_state_event: dict[str, Any] | None = None
                if projection is not None and run is not None:
                    run, durable_state = await projection.transition_run_with_event(
                        run,
                        RunStatus.INTERRUPTED,
                        reason="Human approval required",
                    )
                    run_state_event = enrich_sse_event(
                        EventBuilder.run_state(
                            from_status="active",
                            to_status=SessionStatus.WAITING_FOR_APPROVAL.value,
                            scope_keys=scope_keys,
                        ),
                        durable_state,
                    )
                    await projection.rebuild_messages_from_checkpoint(service, run)
                else:
                    await store.update_session(
                        session_id=session_id,
                        status=SessionStatus.WAITING_FOR_APPROVAL.value,
                        effective_scope=scope,
                    )
                    run_state_event = EventBuilder.run_state(
                        from_status="active",
                        to_status=SessionStatus.WAITING_FOR_APPROVAL.value,
                        scope_keys=scope_keys,
                    )
                yield run_state_event
                sse = EventBuilder.interrupt(
                    tool_call_id=event.tool_call_id,
                    tool_name=event.tool_name,
                    args=event.args,
                    session_id=session_id,
                    action_requests=event.action_requests,
                    scope_keys=scope_keys,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "hitl.interrupt",
                        payload={
                            "tool_call_id": event.tool_call_id,
                            "tool_name": event.tool_name,
                            "action_count": len(event.action_requests or []),
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, DelegationEvent):
                # ISSUE-010: Emit delegation event for UI visibility
                await _touch_session_activity(
                    store,
                    session_id,
                    effective_scope=scope,
                )
                sse = EventBuilder.delegation(
                    from_agent=event.from_agent,
                    to_agent=event.to_agent,
                    task=event.task,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "subagent.started",
                        payload={
                            "from_agent": event.from_agent,
                            "to_agent": event.to_agent,
                            "task": event.task,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, SandboxLifecycleEvent):
                await _touch_session_activity(
                    store,
                    session_id,
                    effective_scope=scope,
                )
                yield await _sandbox_lifecycle_sse(event, projection=projection, run=run)

            elif isinstance(event, StatusEvent):
                await _touch_session_activity(
                    store,
                    session_id,
                    effective_scope=scope,
                )
                sse = EventBuilder.status(event.status)
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "run.status",
                        payload={"status": event.status},
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, UsageEvent):
                metadata["input_tokens"] = event.input_tokens
                metadata["output_tokens"] = event.output_tokens
                metadata["estimated_cost"] = event.estimated_cost
                metadata["provider"] = event.provider
                metadata["model"] = event.model
                sse = EventBuilder.usage(
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    estimated_cost=event.estimated_cost,
                    provider=event.provider,
                    model=event.model,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "usage.recorded",
                        payload={
                            "input_tokens": event.input_tokens,
                            "output_tokens": event.output_tokens,
                            "estimated_cost": event.estimated_cost,
                            "provider": event.provider,
                            "model": event.model,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, DoneEvent):
                for sandbox_snapshot in agent_manager.snapshot_sandbox_backend_events(session_id):
                    yield await _sandbox_lifecycle_sse(
                        sandbox_snapshot,
                        projection=projection,
                        run=run,
                    )

                active_runtime_count = agent_manager.active_runtime_count(session_id)
                completion_status = (
                    SessionStatus.ACTIVE.value
                    if active_runtime_count > 1
                    else SessionStatus.IDLE.value
                )
                state_sse: dict[str, Any] | None = None
                if projection is not None and run is not None:
                    if completion_status == SessionStatus.IDLE.value:
                        run, durable_state = await projection.transition_run_with_event(
                            run,
                            RunStatus.DONE,
                        )
                        state_sse = enrich_sse_event(
                            EventBuilder.run_state(
                                from_status="active",
                                to_status=SessionStatus.IDLE.value,
                                scope_keys=scope_keys,
                            ),
                            durable_state,
                        )
                        await projection.rebuild_messages_from_checkpoint(service, run)
                    else:
                        durable_status = await projection.append_event(
                            run,
                            "run.status",
                            payload={"status": completion_status},
                        )
                        state_sse = enrich_sse_event(
                            EventBuilder.status("active"),
                            durable_status,
                        )
                else:
                    await store.update_session(
                        session_id=session_id,
                        status=completion_status,
                        effective_scope=scope,
                    )
                    state_sse = EventBuilder.run_state(
                        from_status="active",
                        to_status=completion_status,
                        scope_keys=scope_keys,
                    )
                yield state_sse
                assistant_content = "".join(assistant_content_parts)
                sse = EventBuilder.done(
                    assistant_data={
                        "content": assistant_content,
                        "tool_calls": tool_calls or None,
                        "token_count": metadata.get("output_tokens"),
                        "model_used": metadata.get("model"),
                        "metadata": metadata,
                    },
                    scope_keys=scope_keys,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "message.assistant.completed",
                        payload={"content_length": len(assistant_content)},
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, ErrorEvent):
                for teardown_sse in await _release_sandbox_lifecycle_sse_events(
                    agent_manager,
                    session_id,
                    projection=projection,
                    run=run,
                ):
                    yield teardown_sse
                if event.code == "ABORTED":
                    error_state_sse: dict[str, Any] | None = None
                    if projection is not None and run is not None:
                        run, durable_state = await projection.transition_run_with_event(
                            run,
                            RunStatus.ABORTED,
                            reason="Execution aborted",
                            error_code=event.code,
                        )
                        error_state_sse = enrich_sse_event(
                            EventBuilder.run_state(
                                from_status="active",
                                to_status=SessionStatus.ABORTED.value,
                                reason="Execution aborted",
                                scope_keys=scope_keys,
                            ),
                            durable_state,
                        )
                        await projection.rebuild_messages_from_checkpoint(service, run)
                    else:
                        await store.update_session(
                            session_id=session_id,
                            status=SessionStatus.ABORTED.value,
                            effective_scope=scope,
                        )
                        error_state_sse = EventBuilder.run_state(
                            from_status="active",
                            to_status=SessionStatus.ABORTED.value,
                            reason="Execution aborted",
                            scope_keys=scope_keys,
                        )
                    yield error_state_sse
                else:
                    error_state_sse = None
                    if projection is not None and run is not None:
                        run, durable_state = await projection.transition_run_with_event(
                            run,
                            RunStatus.FAILED,
                            reason=event.message,
                            error_code=event.code,
                        )
                        error_state_sse = enrich_sse_event(
                            EventBuilder.run_state(
                                from_status="active",
                                to_status=SessionStatus.FAILED.value,
                                reason=event.message,
                                scope_keys=scope_keys,
                            ),
                            durable_state,
                        )
                        await projection.rebuild_messages_from_checkpoint(service, run)
                    else:
                        await store.update_session(
                            session_id=session_id,
                            status=SessionStatus.FAILED.value,
                            effective_scope=scope,
                        )
                        error_state_sse = EventBuilder.run_state(
                            from_status="active",
                            to_status=SessionStatus.FAILED.value,
                            reason=event.message,
                            scope_keys=scope_keys,
                        )
                    yield error_state_sse
                sse = EventBuilder.error(event.message, code=event.code)
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "run.error",
                        payload={"message": event.message, "code": event.code},
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse
                return

            elif isinstance(event, HeartbeatEvent):
                await _touch_session_activity(
                    store,
                    session_id,
                    effective_scope=scope,
                )
                sse = EventBuilder.heartbeat(
                    step_label=event.step_label,
                    last_model_call=event.last_model_call,
                    last_tool_call=event.last_tool_call,
                    active_subagent_count=event.active_subagent_count,
                    sandbox_ready=event.sandbox_ready,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        "run.heartbeat",
                        payload={
                            "step_label": event.step_label,
                            "last_model_call": event.last_model_call,
                            "last_tool_call": event.last_tool_call,
                            "active_subagent_count": event.active_subagent_count,
                            "sandbox_ready": event.sandbox_ready,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, RunStateEvent):
                if event.to_status:
                    await store.update_session(
                        session_id=session_id,
                        status=event.to_status,
                        effective_scope=scope,
                    )
                else:
                    await _touch_session_activity(
                        store,
                        session_id,
                        effective_scope=scope,
                    )
                sse = EventBuilder.run_state(
                    from_status=event.from_status,
                    to_status=event.to_status,
                    reason=event.reason,
                    scope_keys=scope_keys,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        f"run.{event.to_status or 'state'}",
                        payload={
                            "from_status": event.from_status,
                            "to_status": event.to_status,
                            "reason": event.reason,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

            elif isinstance(event, CallbackEvent):
                sse = EventBuilder.callback(
                    callback_id=event.callback_id,
                    url=event.url,
                    status=event.status,
                    attempt=event.attempt,
                    response_status=event.response_status,
                    error_message=event.error_message,
                )
                if projection is not None and run is not None:
                    durable = await projection.append_event(
                        run,
                        f"callback.delivery.{event.status}",
                        payload={
                            "callback_id": event.callback_id,
                            "status": event.status,
                            "attempt": event.attempt,
                            "response_status": event.response_status,
                            "has_error": event.error_message is not None,
                        },
                    )
                    sse = enrich_sse_event(sse, durable)
                yield sse

    except Exception as e:
        logger.error(
            "Agent streaming error",
            error_type=type(e).__name__,
            session_id=session_id,
            exc_info=True,
        )
        error_sse = EventBuilder.error(str(e), code="AGENT_ERROR")
        if projection is not None and run is not None:
            run, durable_state = await projection.transition_run_with_event(
                run,
                RunStatus.FAILED,
                reason=str(e),
                error_code="AGENT_ERROR",
            )
            service = agent_manager.get_service(session_id)
            if service is not None:
                await projection.rebuild_messages_from_checkpoint(service, run)
            error_sse = enrich_sse_event(error_sse, durable_state)
        yield error_sse


async def _post_completion_callback(
    callback_url: str,
    callback_origin: str,
    payload: dict[str, Any],
    session_id: str,
    projection: RuntimeProjectionService | None = None,
    run: SessionRun | None = None,
) -> None:
    """POST final completion payload to an external callback URL."""
    if projection is not None and run is not None:
        await projection.append_event(
            run,
            "callback.delivery.started",
            payload={"origin": callback_origin},
        )
    try:
        async with httpx.AsyncClient(
            timeout=15.0,
            follow_redirects=False,
            trust_env=False,
        ) as client:
            response = await client.post(callback_url, json=payload)
            response.raise_for_status()
        if projection is not None and run is not None:
            await projection.append_event(
                run,
                "callback.delivery.completed",
                payload={
                    "origin": callback_origin,
                    "response_status": response.status_code,
                },
            )
        logger.info(
            "message_completion_callback_sent",
            session_id=session_id,
            callback_origin=callback_origin,
            status_code=response.status_code,
        )
    except Exception as exc:
        if projection is not None and run is not None:
            await projection.append_event(
                run,
                "callback.delivery.failed",
                payload={
                    "origin": callback_origin,
                    "error_type": type(exc).__name__,
                },
            )
        logger.warning(
            "message_completion_callback_failed",
            session_id=session_id,
            callback_origin=callback_origin,
            error_type=type(exc).__name__,
        )


def _approved_callback_origin(callback_url: str, settings: Settings) -> str:
    """Return an approved HTTPS origin or fail closed."""
    try:
        parsed = urlsplit(callback_url)
        port = parsed.port
    except ValueError as exc:
        STRICT_EXECUTION_REJECTIONS_TOTAL.labels(reason="callback_origin").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Completion callback URL is invalid",
        ) from exc
    if (
        parsed.scheme.lower() != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        STRICT_EXECUTION_REJECTIONS_TOTAL.labels(reason="callback_origin").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Completion callbacks require an operator-approved HTTPS origin",
        )
    origin = f"https://{parsed.hostname.lower()}"
    if port is not None and port != 443:
        origin = f"{origin}:{port}"
    approved = {candidate.rstrip("/").lower() for candidate in settings.callback_allowed_origins}
    if origin.lower() not in approved:
        STRICT_EXECUTION_REJECTIONS_TOTAL.labels(reason="callback_origin").inc()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Completion callback origin is not approved by the operator",
        )
    return origin


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        409: {"model": ErrorResponse, "description": "Session already has an active run"},
        500: {"model": ErrorResponse, "description": "Agent error"},
    },
)
async def send_message(
    session_id: str,
    request: MessageCreate,
    http_request: Request,
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
    agent_manager: SessionAgentManager = Depends(get_session_agent_manager_dep),  # noqa: B008
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
    artifact_store: ArtifactStore = Depends(get_artifact_store),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    rate_limiter: RateLimiter = Depends(get_rate_limiter_dep),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> StreamingResponse:
    """Send a message to the agent.

    Sends a message to the agent and streams back the response as Server-Sent Events.

    The response is an SSE stream with the following event types:
    - `token`: Streaming LLM token
    - `tool_call`: Agent invoking a tool
    - `tool_result`: Tool execution result
    - `planning`: Agent is creating a plan for complex tasks
    - `step_complete`: A step in the plan has been completed
    - `usage`: Token usage and cost information
    - `error`: Error occurred
    - `done`: Stream complete
    """
    # Check rate limit
    # Use scope-based key if scoping is enabled, otherwise use IP
    if settings.scoping_enabled and not scope.is_empty():
        rate_limit_key = f"session:{session_id}:scope:{scope.get_all()}"
    else:
        client_ip = http_request.client.host if http_request.client else "unknown"
        rate_limit_key = f"session:{session_id}:ip:{client_ip}"

    await rate_limiter.check_rate_limit(rate_limit_key)

    callback_origin = (
        _approved_callback_origin(str(request.callback_url), settings)
        if request.callback_url
        else None
    )

    session = await _get_scoped_session_for_messages(store, session_id, scope.get_all())

    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    workspace_path = session.workspace_path

    session_status = SessionStatus(session.status)
    if SessionStatus.is_terminal(session_status):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Session '{session_id}' is in terminal state '{session_status.value}'",
        )

    if request.idempotency_key:
        existing_run = await store.get_run_by_idempotency_key(
            session_id,
            request.idempotency_key,
            session.scopes,
        )
        if existing_run is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Run idempotency key '{request.idempotency_key}' was already used "
                    f"for run '{existing_run.id}'. Poll that run instead of sending "
                    "the message again."
                ),
            )

    if agent_manager.active_runtime_count(session_id) > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Session '{session_id}' already has an active runtime turn. "
                "Poll the session state or wait for the current turn to finish before "
                "sending another message."
            ),
        )

    effective_scope = scope.get_all() if not scope.is_empty() else session.scopes
    task_runtime = AgentTaskRuntime(
        store,
        default_workspace_path=workspace_path,
        artifact_store=artifact_store,
        config_store=config_store,
    )
    try:
        execution = await task_runtime.submit(
            SubmitTask(
                context_id=session.id,
                agent_name=session.agent_name,
                effective_scope=effective_scope,
                content=request.content,
                parent_message_id=request.parent_id,
                idempotency_key=(
                    f"native:{session.id}:{request.idempotency_key}"
                    if request.idempotency_key
                    else None
                ),
                workspace_path=workspace_path,
                session_config=session.config,
                metadata={"source": "native-rest"},
            )
        )
        if execution.reused:
            raise RuntimeTaskConflictError(
                f"Idempotency key was already used for task '{execution.task.id}'",
                task_id=execution.task.id,
            )
        run = execution.run
        user_message = execution.user_message
        projection = task_runtime.projection
    except RuntimeTaskConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    except ActiveRunConflictError as exc:
        # Kept for compatibility with callers of the projection service.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Session '{session_id}' already has active run '{exc.run.id}'. "
                "Poll the run or wait for it to finish before sending another message."
            ),
        ) from exc

    # Get thread_id from session for state persistence
    # The session should have a thread_id for DeepAgents checkpointing
    thread_id = getattr(session, "thread_id", None)
    if not thread_id:
        thread_id = str(uuid.uuid4())
        # Store thread_id on session for persistence
        session.thread_id = thread_id

    event_stream = agent_event_stream(
        session_id,
        thread_id,
        request.content,
        workspace_path,
        settings,
        agent_manager,
        store=store,
        scope=scope.get_all() if not scope.is_empty() else None,
        parent_message_id=user_message.id,
        run=run,
        projection=projection,
    )

    # Wrap the event stream to persist assistant message on completion
    async def wrapped_event_stream() -> AsyncGenerator[dict[str, Any], None]:
        assistant_data = None
        message_id = None
        completion_status = "error"
        callback_error: dict[str, Any] | None = None
        terminal_error_seen = False
        async for event in event_stream:
            # Capture assistant data and message_id from done event
            if event.get("event") == "done":
                if terminal_error_seen:
                    continue
                if event.get("data", {}).get("assistant_data"):
                    assistant_data = event["data"]["assistant_data"]
                # ISSUE-019: Capture message_id from done event
                message_id = event.get("data", {}).get("message_id")
                completion_status = "done"
                if assistant_data:
                    try:
                        tc_objects = None
                        if assistant_data.get("tool_calls"):
                            tc_objects = [
                                ToolCall(name=tc["name"], args=tc.get("args", {}), id=tc["id"])
                                for tc in assistant_data["tool_calls"]
                            ]

                        persist_message_id = message_id or str(uuid.uuid4())
                        persisted = await task_runtime.persist_assistant_message(
                            execution,
                            message_id=persist_message_id,
                            content=assistant_data.get("content") or "",
                            tool_calls=tc_objects,
                            token_count=assistant_data.get("token_count"),
                            model_used=assistant_data.get("model_used"),
                            metadata=assistant_data.get("metadata"),
                        )
                        message_id = persisted.id
                        event_data = dict(event.get("data", {}))
                        event_data["message_id"] = persist_message_id
                        event = {**event, "data": event_data}
                    except Exception as e:
                        logger.error(
                            "Failed to persist assistant message",
                            error=str(e),
                            session_id=session_id,
                        )
            elif event.get("event") == "error":
                callback_error = event.get("data")
                terminal_error_seen = True
                error_code = (
                    callback_error.get("code") if isinstance(callback_error, dict) else None
                )
                completion_status = "aborted" if error_code == "ABORTED" else "failed"
            yield event

        if request.callback_url:
            callback_payload = {
                "session_id": session_id,
                "message_id": message_id,
                "status": completion_status,
                "output": assistant_data.get("content") if assistant_data else None,
                "token_usage": {
                    "input": assistant_data.get("metadata", {}).get("input_tokens", 0)
                    if assistant_data
                    else 0,
                    "output": assistant_data.get("metadata", {}).get("output_tokens", 0)
                    if assistant_data
                    else 0,
                },
                "model_used": assistant_data.get("model_used") if assistant_data else None,
                "completed_at": __import__("datetime")
                .datetime.now(__import__("datetime").UTC)
                .isoformat(),
            }
            if callback_error:
                callback_payload["error"] = callback_error
            await _post_completion_callback(
                str(request.callback_url),
                callback_origin or "",
                callback_payload,
                session_id,
                projection=projection,
                run=run,
            )

    # Check for Last-Event-ID header for stream resumption
    last_event_id = get_last_event_id(http_request)

    # Create SSE stream with settings-based configuration
    sse_stream = SSEStream.from_settings(settings)
    return sse_stream.create_response(wrapped_event_stream(), http_request, last_event_id)


@router.get(
    "",
    response_model=MessageList,
)
async def list_messages(
    session_id: str,
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    limit: int = 50,
    offset: int = 0,
) -> MessageList:
    """List messages in a session.

    Returns a paginated list of messages for the specified session.
    """
    _ = str(settings.workspace_path)

    session = await _get_scoped_session_for_messages(store, session_id, scope.get_all())
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    messages, total = await store.get_messages_by_session(
        session_id,
        limit,
        offset,
        session.scopes,
    )

    # Convert domain models to API models
    paginated = [
        MessageResponse(
            id=m.id,
            session_id=m.session_id,
            role=m.role,
            content=m.content,
            parent_id=m.parent_id,
            created_at=m.created_at,
            tool_calls=[
                ToolCallResponse(name=tc.name, args=tc.args, id=tc.id) for tc in m.tool_calls
            ]
            if m.tool_calls
            else None,
            tool_call_id=m.tool_call_id,
            token_count=m.token_count,
            model_used=m.model_used,
            metadata=m.metadata,
        )
        for m in messages
    ]

    has_more = offset + limit < total

    return MessageList(
        messages=paginated,
        total=total,
        has_more=has_more,
    )


@router.get(
    "/{message_id}",
    response_model=MessageResponse,
    responses={
        404: {"model": ErrorResponse, "description": "Message not found"},
    },
)
async def get_message(
    session_id: str,
    message_id: str,
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
    store: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> MessageResponse:
    """Get a specific message.

    Returns detailed information about a specific message.
    """
    _ = str(settings.workspace_path)

    session = await _get_scoped_session_for_messages(store, session_id, scope.get_all())
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    message = await store.get_message(message_id, session.scopes)

    if message and message.session_id == session_id:
        return MessageResponse(
            id=message.id,
            session_id=message.session_id,
            role=message.role,
            content=message.content,
            parent_id=message.parent_id,
            created_at=message.created_at,
            tool_calls=[
                ToolCallResponse(name=tc.name, args=tc.args, id=tc.id) for tc in message.tool_calls
            ]
            if message.tool_calls
            else None,
            tool_call_id=message.tool_call_id,
            token_count=message.token_count,
            model_used=message.model_used,
            metadata=message.metadata,
        )

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Message not found: {message_id}",
    )
