"""FastAPI server with WebSocket support."""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse

from server.app.agent import create_cognition_agent
from server.app.sessions import SessionManager, get_session_manager
from server.app.settings import Settings, get_settings
from shared import (
    CreateProject,
    CreateSession,
    Done,
    Error,
    ProjectCreated,
    SessionStarted,
    Token,
    ToolCall,
    ToolResult,
    UserMessage,
    message_to_json,
    parse_message,
)

logger = structlog.get_logger()

# Create FastAPI app
app = FastAPI(title="Cognition", version="0.1.0")


@app.on_event("startup")
async def startup():
    """Start background tasks on server startup."""
    session_manager = get_session_manager()
    await session_manager.start()
    logger.info("Cognition server started")


@app.on_event("shutdown")
async def shutdown():
    """Stop background tasks on server shutdown."""
    session_manager = get_session_manager()
    await session_manager.stop()
    logger.info("Cognition server stopped")


@app.get("/health")
async def health() -> JSONResponse:
    """Health check endpoint."""
    session_manager = get_session_manager()
    return JSONResponse(
        {
            "status": "healthy",
            "version": "0.1.0",
            "active_sessions": len(session_manager.list_sessions()),
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for client communication."""
    await websocket.accept()
    session_manager = get_session_manager()
    settings = get_settings()

    try:
        while True:
            # Receive message from client
            data = await websocket.receive_text()

            try:
                message = parse_message(data)
            except ValueError as e:
                await websocket.send_text(
                    message_to_json(
                        Error(
                            message=f"Invalid message: {e}",
                            code="INVALID_MESSAGE",
                        )
                    )
                )
                continue

            # Handle message based on type
            if isinstance(message, CreateProject):
                await handle_create_project(websocket, message, settings)
            elif isinstance(message, CreateSession):
                await handle_create_session(websocket, message, session_manager, settings)
            elif isinstance(message, UserMessage):
                await handle_user_message(websocket, message, session_manager)
            else:
                await websocket.send_text(
                    message_to_json(
                        Error(
                            message=f"Unknown message type: {message.msg_type}",
                            code="UNKNOWN_TYPE",
                        )
                    )
                )

    except WebSocketDisconnect:
        logger.info("Client disconnected")
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        await websocket.send_text(
            message_to_json(
                Error(
                    message=str(e),
                    code="INTERNAL_ERROR",
                )
            )
        )


async def handle_create_project(
    websocket: WebSocket,
    message: CreateProject,
    settings: Settings,
) -> None:
    """Handle project creation request."""
    project_id = str(uuid.uuid4())

    # Determine project path
    if message.project_path:
        project_path = Path(message.project_path)
    elif message.user_prefix:
        project_path = settings.workspace_root / f"{message.user_prefix}-{project_id[:8]}"
    else:
        project_path = settings.workspace_root / project_id

    # Create project directory
    project_path.mkdir(parents=True, exist_ok=True)

    await websocket.send_text(
        message_to_json(
            ProjectCreated(
                project_id=project_id,
                project_path=str(project_path),
            )
        )
    )

    logger.info(f"Created project {project_id} at {project_path}")


async def handle_create_session(
    websocket: WebSocket,
    message: CreateSession,
    session_manager: SessionManager,
    settings: Settings,
) -> None:
    """Handle session creation request."""
    try:
        # Get model
        model = settings.get_llm_model()

        # Create agent
        # For now, use a default workspace path
        workspace_path = settings.workspace_root / message.project_id
        agent = create_cognition_agent(
            project_path=workspace_path,
            model=model,
        )

        # Create session
        session = session_manager.create_session(
            project_id=message.project_id,
            project_path=str(workspace_path),
            agent=agent,
        )

        await websocket.send_text(
            message_to_json(
                SessionStarted(
                    session_id=session.session_id,
                    thread_id=session.thread_id,
                    project_id=message.project_id,
                )
            )
        )

        logger.info(f"Created session {session.session_id}")

    except Exception as e:
        logger.error(f"Failed to create session: {e}")
        await websocket.send_text(
            message_to_json(
                Error(
                    message=f"Failed to create session: {e}",
                    code="SESSION_CREATE_FAILED",
                )
            )
        )


async def handle_user_message(
    websocket: WebSocket,
    message: UserMessage,
    session_manager: SessionManager,
) -> None:
    """Handle user message and stream agent response."""
    session = session_manager.get_session(message.session_id)

    if not session:
        await websocket.send_text(
            message_to_json(
                Error(
                    message="Session not found",
                    code="SESSION_NOT_FOUND",
                )
            )
        )
        return

    try:
        # Run agent and stream events
        async for event in session.agent.astream_events(
            {"messages": [{"role": "user", "content": message.content}]},
            config={"configurable": {"thread_id": session.thread_id}},
        ):
            await stream_event(websocket, event)

        # Send done event
        await websocket.send_text(message_to_json(Done()))

    except Exception as e:
        logger.error(f"Agent error: {e}")
        await websocket.send_text(
            message_to_json(
                Error(
                    message=str(e),
                    code="AGENT_ERROR",
                )
            )
        )


async def stream_event(websocket: WebSocket, event: dict[str, Any]) -> None:
    """Stream a single event to the client."""
    event_type = event.get("event")

    if event_type == "on_chat_model_stream":
        # LLM token
        chunk = event.get("data", {}).get("chunk", "")
        if chunk:
            content = chunk.content if hasattr(chunk, "content") else str(chunk)
            if content:
                await websocket.send_text(message_to_json(Token(content=content)))

    elif event_type == "on_tool_start":
        # Tool call started
        tool_data = event.get("data", {})
        await websocket.send_text(
            message_to_json(
                ToolCall(
                    name=tool_data.get("name", "unknown"),
                    args=tool_data.get("input", {}),
                    id=event.get("run_id", "unknown"),
                )
            )
        )

    elif event_type == "on_tool_end":
        # Tool call completed
        tool_data = event.get("data", {})
        output = tool_data.get("output", "")
        # Convert output to string if needed
        if not isinstance(output, str):
            output = json.dumps(output)

        await websocket.send_text(
            message_to_json(
                ToolResult(
                    tool_call_id=event.get("run_id", "unknown"),
                    output=output,
                    exit_code=0,
                )
            )
        )


def main():
    """Entry point for running the server."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "server.app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )


if __name__ == "__main__":
    main()
