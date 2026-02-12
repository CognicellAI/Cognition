"""Message API routes.

REST endpoints for sending messages and receiving SSE streams.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import AsyncGenerator

from fastapi import APIRouter, HTTPException, Request, Depends, status

from server.app.api.models import (
    MessageCreate,
    MessageResponse,
    MessageList,
    ErrorResponse,
)
from server.app.api.sse import SSEStream, EventBuilder
from server.app.api.routes.sessions import _sessions
from server.app.settings import Settings, get_settings

router = APIRouter(prefix="/sessions/{session_id}/messages", tags=["messages"])


# In-memory message store (replace with database in production)
_messages: dict[str, list[MessageResponse]] = {}


def get_settings_dependency() -> Settings:
    """Get settings dependency."""
    return get_settings()


async def agent_event_stream(
    session_id: str,
    content: str,
    settings: Settings,
) -> AsyncGenerator[dict, None]:
    """Generate agent events as SSE.

    This is a placeholder implementation. In production, this would:
    1. Look up the agent for this session
    2. Call agent.astream_events() or similar
    3. Yield events as they occur
    4. Handle errors gracefully
    """
    try:
        # Echo back that we received the message
        yield EventBuilder.token(f"You said: {content}\n\n")

        # Simulate agent thinking and responding
        # In production, this would be replaced with actual agent streaming

        # Check if this is a file-related query (simple heuristic)
        if "file" in content.lower() or "read" in content.lower():
            # Simulate tool call
            tool_call_id = str(uuid.uuid4())
            yield EventBuilder.tool_call(
                name="glob",
                args={"pattern": "*.py"},
                tool_call_id=tool_call_id,
            )

            # Simulate tool result
            yield EventBuilder.tool_result(
                tool_call_id=tool_call_id,
                output="main.py\napp.py\ntest_main.py",
                exit_code=0,
            )

            # Continue response
            yield EventBuilder.token("I found several Python files in your project. ")
            yield EventBuilder.token("The main files are main.py and app.py. ")
            yield EventBuilder.token("Would you like me to examine any of them?")
        else:
            # Simple response acknowledging the message
            yield EventBuilder.token("I received your message. ")
            yield EventBuilder.token(
                "In a production implementation, this would invoke the actual "
            )
            yield EventBuilder.token(
                "LangGraph agent with the configured LLM and stream real tokens "
            )
            yield EventBuilder.token("based on your input.")

        # Yield usage info
        yield EventBuilder.usage(
            input_tokens=len(content.split()),
            output_tokens=50,
            estimated_cost=0.002,
            provider=settings.llm_provider,
            model=settings.llm_model,
        )

        # Signal completion
        yield EventBuilder.done()

    except Exception as e:
        # Yield error event
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
    # Check if session exists
    if session_id not in _sessions:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Session not found: {session_id}",
        )

    session = _sessions[session_id]

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

    # Update session message count
    session.message_count = len(_messages[session_id])
    session.updated_at = datetime.utcnow()

    # Create SSE stream
    event_stream = agent_event_stream(session_id, request.content, settings)

    return SSEStream.create_response(event_stream, http_request)


@router.get(
    "",
    response_model=MessageList,
)
async def list_messages(
    session_id: str,
    limit: int = 50,
    offset: int = 0,
) -> MessageList:
    """List messages in a session.

    Returns a paginated list of messages for the specified session.
    """
    # Check if session exists
    if session_id not in _sessions:
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
) -> MessageResponse:
    """Get a specific message.

    Returns detailed information about a specific message.
    """
    # Check if session exists
    if session_id not in _sessions:
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
