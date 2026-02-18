"""Message API routes.

REST endpoints for sending messages and receiving SSE streams.

Git-Style Workspace Model:
  The server's current working directory (CWD) is the workspace.
  All messages are scoped to the server's workspace.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, HTTPException, Request, Depends, status

from server.app.api.models import (
    MessageCreate,
    MessageResponse,
    MessageList,
    ErrorResponse,
    ToolCallResponse,
)
from server.app.api.sse import SSEStream, EventBuilder, get_last_event_id
from server.app.session_store import get_session_store
from server.app.message_store import get_message_store
from server.app.settings import Settings, get_settings
from server.app.rate_limiter import get_rate_limiter, RateLimiter
from server.app.scoping import SessionScope, create_scope_dependency
from server.app.llm.deep_agent_service import (
    get_session_agent_manager,
    DeepAgentStreamingService,
    SessionAgentManager,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
    DoneEvent,
    ErrorEvent,
    PlanningEvent,
    StepCompleteEvent,
    StatusEvent,
)

router = APIRouter(prefix="/sessions/{session_id}/messages", tags=["messages"])


def get_settings_dependency() -> Settings:
    """Get settings dependency."""
    return get_settings()


def get_agent_manager(settings: Settings = Depends(get_settings_dependency)) -> SessionAgentManager:
    """Get the session agent manager."""
    return get_session_agent_manager(settings)


async def agent_event_stream(
    session_id: str,
    thread_id: str,
    content: str,
    workspace_path: str,
    settings: Settings,
    agent_manager: SessionAgentManager,
) -> AsyncGenerator[dict, None]:
    """Generate agent events as SSE using DeepAgents.

    Uses DeepAgents for multi-step task completion:
    1. Automatic ReAct loop (LLM → tool → LLM until complete)
    2. State persistence via thread_id checkpointing
    3. Built-in planning with write_todos
    4. Context management and streaming

    Yields:
        SSE events as dictionaries. The final 'done' event contains
        the assistant message data for persistence.
    """
    try:
        # Get or create agent service for this session
        service = agent_manager.get_service(session_id)

        if not service:
            # Session not registered with agent manager yet
            # Register with workspace path
            service = agent_manager.register_session(session_id, workspace_path)

        # Get session from store
        store = get_session_store(workspace_path)
        session = await store.get_session(session_id)

        if not session:
            yield EventBuilder.error("Session not found", code="SESSION_NOT_FOUND")
            return

        # Get system prompt from session or use default
        system_prompt = None
        if session and session.config.system_prompt:
            system_prompt = session.config.system_prompt

        # Accumulate assistant message data for persistence
        accumulated_content = []
        tool_calls = []
        current_tool_call = None
        token_count = 0
        model_used = None
        metadata = {}

        # Stream response using DeepAgents with multi-step support
        async for event in service.stream_response(
            session_id=session_id,
            thread_id=thread_id,
            project_path=workspace_path,
            content=content,
            system_prompt=system_prompt,
        ):
            if isinstance(event, TokenEvent):
                accumulated_content.append(event.content)
                token_count += len(event.content.split())
                yield EventBuilder.token(event.content)

            elif isinstance(event, ToolCallEvent):
                tool_call = {
                    "name": event.name,
                    "args": event.args,
                    "id": event.tool_call_id,
                }
                tool_calls.append(tool_call)
                current_tool_call = event.tool_call_id
                yield EventBuilder.tool_call(
                    name=event.name,
                    args=event.args,
                    tool_call_id=event.tool_call_id,
                )

            elif isinstance(event, ToolResultEvent):
                yield EventBuilder.tool_result(
                    tool_call_id=event.tool_call_id,
                    output=event.output,
                    exit_code=event.exit_code,
                )

            elif isinstance(event, PlanningEvent):
                yield EventBuilder.planning(event.todos)

            elif isinstance(event, StepCompleteEvent):
                yield EventBuilder.step_complete(
                    step_number=event.step_number,
                    total_steps=event.total_steps,
                    description=event.description,
                )

            elif isinstance(event, StatusEvent):
                yield EventBuilder.status(event.status)

            elif isinstance(event, UsageEvent):
                token_count = event.output_tokens
                model_used = event.model
                metadata["input_tokens"] = event.input_tokens
                metadata["output_tokens"] = event.output_tokens
                metadata["estimated_cost"] = event.estimated_cost
                metadata["provider"] = event.provider
                yield EventBuilder.usage(
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    estimated_cost=event.estimated_cost,
                    provider=event.provider,
                    model=event.model,
                )

            elif isinstance(event, DoneEvent):
                # Include assistant message data in the done event for persistence
                assistant_data = {
                    "content": "".join(accumulated_content),
                    "tool_calls": tool_calls if tool_calls else None,
                    "token_count": token_count,
                    "model_used": model_used,
                    "metadata": metadata if metadata else None,
                }
                yield EventBuilder.done(assistant_data=assistant_data)

            elif isinstance(event, ErrorEvent):
                yield EventBuilder.error(event.message, code=event.code)

    except Exception as e:
        logger = __import__("structlog").get_logger(__name__)
        logger.error("Agent streaming error", error=str(e), session_id=session_id)
        yield EventBuilder.error(str(e), code="AGENT_ERROR")


@router.post(
    "",
    status_code=status.HTTP_200_OK,
    responses={
        404: {"model": ErrorResponse, "description": "Session not found"},
        500: {"model": ErrorResponse, "description": "Agent error"},
    },
)
async def send_message(
    session_id: str,
    request: MessageCreate,
    http_request: Request,
    settings: Settings = Depends(get_settings_dependency),
    agent_manager: SessionAgentManager = Depends(get_agent_manager),
    rate_limiter: RateLimiter = Depends(lambda: get_rate_limiter()),
    scope: SessionScope = Depends(create_scope_dependency(get_settings())),
):
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
    workspace_path = str(settings.workspace_path)

    # Check rate limit
    # Use scope-based key if scoping is enabled, otherwise use IP
    if settings.scoping_enabled and not scope.is_empty():
        rate_limit_key = f"session:{session_id}:scope:{scope.get_all()}"
    else:
        client_ip = http_request.client.host if http_request.client else "unknown"
        rate_limit_key = f"session:{session_id}:ip:{client_ip}"

    await rate_limiter.check_rate_limit(rate_limit_key)

    # Check if session exists using the store
    store = get_session_store(workspace_path)
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

    # Get thread_id from session for state persistence
    # The session should have a thread_id for DeepAgents checkpointing
    thread_id = getattr(session, "thread_id", None)
    if not thread_id:
        thread_id = str(uuid.uuid4())
        # Store thread_id on session for persistence
        session.thread_id = thread_id

    # Create user message
    message_store = get_message_store(workspace_path)
    user_message = await message_store.create_message(
        message_id=str(uuid.uuid4()),
        session_id=session_id,
        role="user",
        content=request.content,
        parent_id=request.parent_id,
    )

    # Update session message count in store
    messages_for_session, _ = await message_store.get_messages_by_session(
        session_id, limit=-1, offset=0
    )
    await store.update_message_count(session_id, len(messages_for_session))

    # Create SSE stream with DeepAgents (multi-step support)
    event_stream = agent_event_stream(
        session_id, thread_id, request.content, workspace_path, settings, agent_manager
    )

    # Wrap the event stream to persist assistant message on completion
    async def wrapped_event_stream():
        assistant_data = None
        async for event in event_stream:
            # Capture assistant data from done event
            if event.get("event") == "done" and event.get("data", {}).get("assistant_data"):
                assistant_data = event["data"]["assistant_data"]
            yield event

        # Persist assistant message after stream completes
        if assistant_data:
            try:
                from server.app.models import ToolCall

                # Convert tool_calls dicts to ToolCall objects
                tc_objects = None
                if assistant_data.get("tool_calls"):
                    tc_objects = [
                        ToolCall(name=tc["name"], args=tc.get("args", {}), id=tc["id"])
                        for tc in assistant_data["tool_calls"]
                    ]

                await message_store.create_message(
                    message_id=str(uuid.uuid4()),
                    session_id=session_id,
                    role="assistant",
                    content=assistant_data.get("content"),
                    parent_id=user_message.id,
                    tool_calls=tc_objects,
                    token_count=assistant_data.get("token_count"),
                    model_used=assistant_data.get("model_used"),
                    metadata=assistant_data.get("metadata"),
                )

                # Update session message count
                messages_for_session, _ = await message_store.get_messages_by_session(
                    session_id, limit=-1, offset=0
                )
                await store.update_message_count(session_id, len(messages_for_session))
            except Exception as e:
                logger = __import__("structlog").get_logger(__name__)
                logger.error(
                    "Failed to persist assistant message", error=str(e), session_id=session_id
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
    settings: Settings = Depends(get_settings_dependency),
    scope: SessionScope = Depends(create_scope_dependency(get_settings())),
    limit: int = 50,
    offset: int = 0,
) -> MessageList:
    """List messages in a session.

    Returns a paginated list of messages for the specified session.
    """
    workspace_path = str(settings.workspace_path)

    # Check if session exists and scope matches
    store = get_session_store(workspace_path)
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

    # Get messages for this session from database
    message_store = get_message_store(workspace_path)
    messages, total = await message_store.get_messages_by_session(session_id, limit, offset)

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
    settings: Settings = Depends(get_settings_dependency),
    scope: SessionScope = Depends(create_scope_dependency(get_settings())),
) -> MessageResponse:
    """Get a specific message.

    Returns detailed information about a specific message.
    """
    workspace_path = str(settings.workspace_path)

    # Check if session exists and scope matches
    store = get_session_store(workspace_path)
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

    # Find message
    message_store = get_message_store(workspace_path)
    message = await message_store.get_message(message_id)

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
