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
)
from server.app.api.sse import SSEStream, EventBuilder
from server.app.session_store import get_session_store
from server.app.settings import Settings, get_settings
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
)

router = APIRouter(prefix="/sessions/{session_id}/messages", tags=["messages"])


# In-memory message store (replace with database in production)
_messages: dict[str, list[MessageResponse]] = {}


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
        session = store.get_session(session_id)

        if not session:
            yield EventBuilder.error("Session not found", code="SESSION_NOT_FOUND")
            return

        # Get system prompt from session or use default
        system_prompt = None
        if session and session.config.system_prompt:
            system_prompt = session.config.system_prompt

        # Stream response using DeepAgents with multi-step support
        async for event in service.stream_response(
            session_id=session_id,
            thread_id=thread_id,
            project_path=workspace_path,
            content=content,
            system_prompt=system_prompt,
        ):
            if isinstance(event, TokenEvent):
                yield EventBuilder.token(event.content)

            elif isinstance(event, ToolCallEvent):
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

            elif isinstance(event, UsageEvent):
                yield EventBuilder.usage(
                    input_tokens=event.input_tokens,
                    output_tokens=event.output_tokens,
                    estimated_cost=event.estimated_cost,
                    provider=event.provider,
                    model=event.model,
                )

            elif isinstance(event, DoneEvent):
                yield EventBuilder.done()

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

    # Check if session exists using the store
    store = get_session_store(workspace_path)
    session = store.get_session(session_id)

    if session is None:
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
    message_id = str(uuid.uuid4())
    user_message = MessageResponse(
        id=message_id,
        session_id=session_id,
        role="user",
        content=request.content,
        parent_id=request.parent_id,
        created_at=datetime.utcnow(),
    )

    # Store message
    if session_id not in _messages:
        _messages[session_id] = []
    _messages[session_id].append(user_message)

    # Update session message count in store
    store.update_message_count(session_id, len(_messages[session_id]))

    # Create SSE stream with DeepAgents (multi-step support)
    event_stream = agent_event_stream(
        session_id, thread_id, request.content, workspace_path, settings, agent_manager
    )

    return SSEStream.create_response(event_stream, http_request)


@router.get(
    "",
    response_model=MessageList,
)
async def list_messages(
    session_id: str,
    settings: Settings = Depends(get_settings_dependency),
    limit: int = 50,
    offset: int = 0,
) -> MessageList:
    """List messages in a session.

    Returns a paginated list of messages for the specified session.
    """
    workspace_path = str(settings.workspace_path)

    # Check if session exists
    store = get_session_store(workspace_path)
    if store.get_session(session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Get messages for this session
    messages = _messages.get(session_id, [])

    # Apply pagination
    total = len(messages)
    paginated = messages[offset : offset + limit]
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
) -> MessageResponse:
    """Get a specific message.

    Returns detailed information about a specific message.
    """
    workspace_path = str(settings.workspace_path)

    # Check if session exists
    store = get_session_store(workspace_path)
    if store.get_session(session_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    # Find message
    messages = _messages.get(session_id, [])
    for message in messages:
        if message.id == message_id:
            return message

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Message not found: {message_id}",
    )
