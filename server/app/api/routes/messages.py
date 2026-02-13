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
from server.app.llm.streaming_service import (
    get_session_llm_manager,
    StreamingConfig,
    SessionLLMManager,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
    DoneEvent,
    ErrorEvent,
)

router = APIRouter(prefix="/sessions/{session_id}/messages", tags=["messages"])


# In-memory message store (replace with database in production)
_messages: dict[str, list[MessageResponse]] = {}


def get_settings_dependency() -> Settings:
    """Get settings dependency."""
    return get_settings()


def get_llm_manager(settings: Settings = Depends(get_settings_dependency)) -> SessionLLMManager:
    """Get the session LLM manager."""
    return get_session_llm_manager(settings)


async def agent_event_stream(
    session_id: str,
    content: str,
    workspace_path: str,
    settings: Settings,
    llm_manager: SessionLLMManager,
) -> AsyncGenerator[dict, None]:
    """Generate agent events as SSE.

    Uses the real LLM streaming service to:
    1. Call the configured LLM provider
    2. Stream tokens as they are generated
    3. Handle tool calls and execute them
    4. Track usage and costs
    5. Yield events for SSE
    """
    try:
        # Get or create LLM service for this session
        service = llm_manager.get_service(session_id)

        if not service:
            # Session not registered with LLM manager yet
            # Register with workspace path
            service = llm_manager.register_session(session_id, workspace_path)

        # Get session from store
        store = get_session_store(workspace_path)
        session = store.get_session(session_id)

        if not session:
            yield EventBuilder.error("Session not found", code="SESSION_NOT_FOUND")
            return

        # Build streaming config from session settings
        config = StreamingConfig(
            system_prompt=session.config.system_prompt
            if session and session.config.system_prompt
            else (
                "You are Cognition, an expert AI coding assistant.\n\n"
                "Your goal is to help users write, edit, and understand code. "
                "You have access to a filesystem and can execute commands.\n\n"
                "Key capabilities:\n"
                "- Read and write files in the workspace\n"
                "- List directory contents\n"
                "- Search files using patterns\n"
                "- Execute shell commands (tests, git, etc.)\n\n"
                "Best practices:\n"
                "1. Always check what files exist before making changes\n"
                "2. Read relevant files before editing\n"
                "3. Use edit_file for precise changes\n"
                "4. Run tests after making changes\n"
                "5. Explain your reasoning before taking actions"
            ),
            temperature=session.config.temperature
            if session and session.config.temperature
            else 0.7,
            max_tokens=session.config.max_tokens if session and session.config.max_tokens else None,
        )

        # Stream LLM response
        async for event in service.stream_response(session_id, content, config):
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
    llm_manager: SessionLLMManager = Depends(get_llm_manager),
):
    """Send a message to the agent.

    Sends a message to the agent and streams back the response as Server-Sent Events.

    The response is an SSE stream with the following event types:
    - `token`: Streaming LLM token
    - `tool_call`: Agent invoking a tool
    - `tool_result`: Tool execution result
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

    # Create SSE stream with real LLM
    event_stream = agent_event_stream(
        session_id, request.content, workspace_path, settings, llm_manager
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
