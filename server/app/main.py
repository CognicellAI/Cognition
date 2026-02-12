"""FastAPI server with WebSocket support and production hardening."""

from __future__ import annotations

import asyncio
import functools
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, status
from fastapi.responses import JSONResponse
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from server.app.agent import create_cognition_agent
from server.app.middleware import ObservabilityMiddleware, SecurityHeadersMiddleware
from server.app.observability import (
    LLM_CALL_DURATION,
    SESSION_COUNT,
    TOOL_CALL_COUNT,
    get_logger,
    get_tracer,
    setup_logging,
    setup_metrics,
    setup_tracing,
    span,
    timed,
)
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

# Setup logging on module import
setup_logging()
logger = get_logger(__name__)
tracer = get_tracer(__name__)


class CircuitBreaker:
    """Simple circuit breaker for external API calls."""

    def __init__(self, failure_threshold: int = 5, recovery_timeout: float = 30.0):
        self.failure_count = 0
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.last_failure_time: float | None = None
        self.state = "closed"  # closed, open, half-open

    def can_execute(self) -> bool:
        """Check if execution is allowed."""
        if self.state == "closed":
            return True

        if self.state == "open":
            if (
                self.last_failure_time
                and (asyncio.get_event_loop().time() - self.last_failure_time)
                > self.recovery_timeout
            ):
                self.state = "half-open"
                return True
            return False

        return True  # half-open

    def record_success(self) -> None:
        """Record a successful execution."""
        self.failure_count = 0
        self.state = "closed"

    def record_failure(self) -> None:
        """Record a failed execution."""
        self.failure_count += 1
        self.last_failure_time = asyncio.get_event_loop().time()

        if self.failure_count >= self.failure_threshold:
            self.state = "open"
            logger.warning(f"Circuit breaker opened after {self.failure_count} failures")


# Circuit breakers for external services
llm_circuit_breaker = CircuitBreaker(failure_threshold=3, recovery_timeout=60.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    settings = get_settings()

    # Startup
    logger.info("Starting Cognition server", version="0.1.0")

    # Setup observability
    setup_tracing(endpoint=getattr(settings, "otel_endpoint", None))
    setup_metrics(port=getattr(settings, "metrics_port", 9090))

    # Start session manager
    session_manager = get_session_manager()
    await session_manager.start()

    logger.info(
        "Cognition server started",
        host=settings.host,
        port=settings.port,
    )

    yield

    # Shutdown
    logger.info("Shutting down Cognition server")
    await session_manager.stop()
    logger.info("Cognition server stopped")


# Create FastAPI app with middleware
app = FastAPI(
    title="Cognition",
    version="0.1.0",
    lifespan=lifespan,
)

# Add middleware (order matters - last added runs first)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ObservabilityMiddleware)


@app.get("/health")
async def health() -> JSONResponse:
    """Health check endpoint with detailed status."""
    session_manager = get_session_manager()

    health_status = {
        "status": "healthy",
        "version": "0.1.0",
        "active_sessions": len(session_manager.list_sessions()),
        "llm_circuit_breaker": llm_circuit_breaker.state,
    }

    # Check if circuit breaker is open
    if llm_circuit_breaker.state == "open":
        health_status["status"] = "degraded"
        return JSONResponse(health_status, status_code=status.HTTP_503_SERVICE_UNAVAILABLE)

    return JSONResponse(health_status)


@app.get("/ready")
async def ready() -> JSONResponse:
    """Readiness probe for Kubernetes."""
    return JSONResponse({"ready": True})


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket endpoint for client communication."""
    await websocket.accept()
    session_manager = get_session_manager()
    settings = get_settings()

    client_id = str(uuid.uuid4())[:8]
    logger.info("Client connected", client_id=client_id)

    try:
        with span("websocket_session", {"client_id": client_id}):
            while True:
                # Receive message from client
                data = await websocket.receive_text()

                with span("handle_message"):
                    try:
                        message = parse_message(data)
                        logger.debug(
                            "Received message",
                            client_id=client_id,
                            msg_type=message.msg_type,
                        )
                    except ValueError as e:
                        logger.warning(
                            "Invalid message received",
                            client_id=client_id,
                            error=str(e),
                        )
                        await send_error(websocket, f"Invalid message: {e}", "INVALID_MESSAGE")
                        continue

                    # Handle message based on type
                    if isinstance(message, CreateProject):
                        await handle_create_project(websocket, message, settings)
                    elif isinstance(message, CreateSession):
                        await handle_create_session(websocket, message, session_manager, settings)
                    elif isinstance(message, UserMessage):
                        await handle_user_message(websocket, message, session_manager)
                    else:
                        logger.warning(
                            "Unknown message type",
                            client_id=client_id,
                            msg_type=message.msg_type,
                        )
                        await send_error(
                            websocket,
                            f"Unknown message type: {message.msg_type}",
                            "UNKNOWN_TYPE",
                        )

    except WebSocketDisconnect:
        logger.info("Client disconnected", client_id=client_id)
    except Exception as e:
        logger.exception("WebSocket error", client_id=client_id, error=str(e))
        await send_error(websocket, str(e), "INTERNAL_ERROR")


async def send_error(websocket: WebSocket, message: str, code: str) -> None:
    """Send error message to client."""
    try:
        await websocket.send_text(message_to_json(Error(message=message, code=code)))
    except Exception as e:
        logger.error("Failed to send error to client", error=str(e))


@timed(None, {})  # Will be timed by middleware for HTTP, manual for WebSocket
async def handle_create_project(
    websocket: WebSocket,
    message: CreateProject,
    settings: Settings,
) -> None:
    """Handle project creation request."""
    project_id = str(uuid.uuid4())

    with span("create_project", {"project_id": project_id}):
        try:
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

            logger.info(
                "Created project",
                project_id=project_id,
                project_path=str(project_path),
            )

        except Exception as e:
            logger.exception("Failed to create project", project_id=project_id, error=str(e))
            await send_error(websocket, f"Failed to create project: {e}", "PROJECT_CREATE_FAILED")


async def handle_create_session(
    websocket: WebSocket,
    message: CreateSession,
    session_manager: SessionManager,
    settings: Settings,
) -> None:
    """Handle session creation request."""
    with span("create_session"):
        try:
            # Get model with circuit breaker
            if not llm_circuit_breaker.can_execute():
                await send_error(
                    websocket,
                    "LLM service temporarily unavailable. Please try again later.",
                    "LLM_UNAVAILABLE",
                )
                return

            try:
                model = await get_llm_model_with_retry(settings)
                llm_circuit_breaker.record_success()
            except Exception as e:
                llm_circuit_breaker.record_failure()
                raise

            # Create agent
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

            SESSION_COUNT.labels(event_type="created").inc()

            await websocket.send_text(
                message_to_json(
                    SessionStarted(
                        session_id=session.session_id,
                        thread_id=session.thread_id,
                        project_id=message.project_id,
                    )
                )
            )

            logger.info(
                "Created session",
                session_id=session.session_id,
                project_id=message.project_id,
            )

        except Exception as e:
            logger.exception("Failed to create session", error=str(e))
            await send_error(websocket, f"Failed to create session: {e}", "SESSION_CREATE_FAILED")


@retry(
    retry=retry_if_exception_type((ConnectionError, TimeoutError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=10),
)
async def get_llm_model_with_retry(settings: Settings):
    """Get LLM model with retry logic."""
    with span("get_llm_model"):
        return settings.get_llm_model()


async def handle_user_message(
    websocket: WebSocket,
    message: UserMessage,
    session_manager: SessionManager,
) -> None:
    """Handle user message and stream agent response."""
    session = session_manager.get_session(message.session_id)

    if not session:
        logger.warning("Session not found", session_id=message.session_id)
        await send_error(websocket, "Session not found", "SESSION_NOT_FOUND")
        return

    with span("handle_user_message", {"session_id": message.session_id}):
        try:
            # Run agent and stream events
            async for event in session.agent.astream_events(
                {"messages": [{"role": "user", "content": message.content}]},
                config={"configurable": {"thread_id": session.thread_id}},
            ):
                await stream_event(websocket, event, session.session_id)

            # Send done event
            await websocket.send_text(message_to_json(Done()))

        except Exception as e:
            logger.exception(
                "Agent error",
                session_id=message.session_id,
                error=str(e),
            )
            await send_error(websocket, str(e), "AGENT_ERROR")


async def stream_event(
    websocket: WebSocket,
    event: dict[str, Any],
    session_id: str,
) -> None:
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
        tool_name = tool_data.get("name", "unknown")

        TOOL_CALL_COUNT.labels(
            tool_name=tool_name,
            status="started",
        ).inc()

        await websocket.send_text(
            message_to_json(
                ToolCall(
                    name=tool_name,
                    args=tool_data.get("input", {}),
                    id=event.get("run_id", "unknown"),
                )
            )
        )

    elif event_type == "on_tool_end":
        # Tool call completed
        tool_data = event.get("data", {})
        tool_name = tool_data.get("name", "unknown")
        output = tool_data.get("output", "")

        # Convert output to string if needed
        if not isinstance(output, str):
            output = json.dumps(output)

        TOOL_CALL_COUNT.labels(
            tool_name=tool_name,
            status="completed",
        ).inc()

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
