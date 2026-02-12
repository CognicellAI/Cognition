"""FastAPI application with WebSocket support."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import JSONResponse
from server.app.exceptions import CognitionError
from server.app.observability import setup_logging, setup_tracing, shutdown_tracing
from server.app.observability.middleware import ObservabilityMiddleware
from server.app.projects.cleanup import ProjectCleanup
from server.app.projects.persistence import MemoryPersistence
from server.app.projects.project import ProjectConfig
from server.app.protocol.messages import CreateSessionRequest, UserMessage
from server.app.protocol.serializer import deserialize_message
from server.app.sessions.manager import get_session_manager
from server.app.settings import Settings, get_settings

# Note: Logger will be properly configured by setup_logging() in startup
logger = structlog.get_logger()

app = FastAPI(
    title="Cognition",
    description="OpenCode-style coding agent",
    version="0.1.0",
)

# Add observability middleware for request correlation and tracing
# Note: This must be added before FastAPIInstrumentor runs in setup_tracing()
app.add_middleware(ObservabilityMiddleware)


@app.on_event("startup")
async def startup_event() -> None:
    """Initialize application on startup."""
    settings = get_settings()

    # Configure logging FIRST (before any other operations)
    setup_logging(settings)

    # Configure tracing
    setup_tracing(app, settings)

    logger.info(
        "Starting Cognition server",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
    )

    if not settings.has_llm_config:
        logger.warning(
            "No LLM configured. Set one of: OPENAI_API_KEY, ANTHROPIC_API_KEY, "
            "or configure AWS Bedrock (AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY or USE_BEDROCK_IAM_ROLE)"
        )
    else:
        logger.info(
            "LLM configured",
            provider=settings.llm_provider,
            model=settings.default_model
            if settings.llm_provider != "bedrock"
            else settings.bedrock_model_id,
        )

    # Initialize session manager with settings
    session_manager = get_session_manager(settings)

    # Start background tasks
    if settings.memory_snapshot_enabled:
        logger.info(
            "Starting memory snapshot task",
            interval=settings.memory_snapshot_interval,
        )
        session_manager.get_memory_persistence().start_periodic_snapshots(
            settings.memory_snapshot_interval
        )

    if settings.project_cleanup_enabled:
        logger.info(
            "Starting project cleanup task",
            interval=settings.project_cleanup_check_interval,
            warning_days=settings.project_cleanup_warning_days,
        )
        cleanup = ProjectCleanup(session_manager.get_project_manager())
        cleanup.start_periodic_cleanup(
            settings.project_cleanup_check_interval,
            settings.project_cleanup_warning_days,
        )


@app.on_event("shutdown")
async def shutdown_event() -> None:
    """Cleanup on shutdown."""
    logger.info("Shutting down Cognition server")
    session_manager = get_session_manager()

    # Stop background tasks
    session_manager.get_memory_persistence().stop_periodic_snapshots()
    # Note: Cleanup task will be stopped automatically on shutdown

    # Disconnect all sessions (preserve projects)
    await session_manager.cleanup_all()

    # Shutdown tracing (flush spans)
    shutdown_tracing()

    logger.info("Shutdown complete")


@app.get("/health")
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    settings = get_settings()
    session_manager = get_session_manager()

    # Build LLM info
    llm_info = {
        "configured": settings.has_llm_config,
        "provider": settings.llm_provider,
    }

    if settings.llm_provider == "bedrock":
        llm_info["model"] = settings.bedrock_model_id
        llm_info["region"] = settings.aws_region
        llm_info["auth_method"] = "iam_role" if settings.use_bedrock_iam_role else "credentials"
    elif settings.llm_provider == "openai_compatible":
        llm_info["model"] = settings.default_model
        llm_info["base_url"] = settings.openai_api_base
    else:
        llm_info["model"] = settings.default_model

    return JSONResponse(
        content={
            "status": "healthy",
            "version": "0.1.0",
            "sessions_active": session_manager.session_count,
            "llm": llm_info,
        }
    )


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """WebSocket endpoint for client communication.

    Connects clients to agent containers. Messages are forwarded to the
    agent runtime running inside the container, and events are streamed
    back in real-time.
    """
    await websocket.accept()
    session_id: str | None = None
    turn_number = 0
    ping_task: asyncio.Task | None = None

    async def handle_agent_event(event: dict[str, Any]) -> None:
        """Forward agent events to the client WebSocket."""
        try:
            await websocket.send_json(event)
        except Exception as e:
            logger.error(
                "Failed to send event to client",
                session_id=session_id,
                error=str(e),
            )

    async def send_ping() -> None:
        """Send periodic ping to keep connection alive."""
        try:
            while True:
                await asyncio.sleep(30)  # Ping every 30 seconds
                if session_id:
                    try:
                        await websocket.send_json({"event": "ping"})
                    except Exception:
                        break
        except asyncio.CancelledError:
            pass

    try:
        # Start ping task
        ping_task = asyncio.create_task(send_ping())

        while True:
            # Receive message
            data = await websocket.receive_text()
            logger.debug("Received message", data=data[:200])

            try:
                message = deserialize_message(data)
            except Exception as e:
                logger.error("Failed to deserialize message", error=str(e))
                await websocket.send_json(
                    {"event": "error", "message": f"Invalid message format: {e}"}
                )
                continue

            if isinstance(message, CreateSessionRequest):
                # Create or resume session
                session_manager = get_session_manager()
                session = session_manager.create_or_resume_session(
                    project_id=message.project_id,
                    user_prefix=message.user_prefix,
                    network_mode=message.network_mode,
                    repo_url=message.repo_url,
                    config=ProjectConfig(
                        network_mode=message.network_mode,
                        repo_url=message.repo_url,
                    )
                    if message.user_prefix
                    else None,
                )
                session_id = session.session_id
                session_manager.attach_websocket(session_id, websocket)

                # Initialize agent bridge (connect to agent container)
                logger.info(
                    "Initializing agent bridge",
                    session_id=session_id,
                    container_id=session.container_id[:12],
                )
                await session_manager.initialize_agent_bridge(
                    session_id=session_id,
                    on_event=handle_agent_event,
                )

                # Send session started event
                await websocket.send_json(
                    {
                        "event": "session_started",
                        "session_id": session_id,
                        "network_mode": session.network_mode,
                        "workspace_path": session.workspace_path,
                    }
                )

                logger.info(
                    "Session created and agent connected",
                    session_id=session_id,
                    project_id=session.project_id,
                    network_mode=session.network_mode,
                )

            elif isinstance(message, UserMessage):
                # Process user message
                if session_id is None:
                    await websocket.send_json(
                        {
                            "event": "error",
                            "message": "No active session. Send create_session first.",
                        }
                    )
                    continue

                if message.session_id != session_id:
                    await websocket.send_json(
                        {
                            "event": "error",
                            "message": "Session ID mismatch",
                        }
                    )
                    continue

                session_manager = get_session_manager()
                session_manager.add_to_history(session_id, "user", message.content)
                turn_number += 1

                # Send message to agent container via bridge
                logger.debug(
                    "Sending message to agent",
                    session_id=session_id,
                    turn_number=turn_number,
                )
                await session_manager.send_to_agent(
                    session_id=session_id,
                    content=message.content,
                    turn_number=turn_number,
                )

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected", session_id=session_id)
        # Disconnect session (preserve workspace and memories)
        if session_id:
            session_manager = get_session_manager()
            try:
                await session_manager.disconnect_session(session_id)
            except Exception as e:
                logger.error("Failed to disconnect session", session_id=session_id, error=str(e))
    except CognitionError as e:
        logger.error("Cognition error", session_id=session_id, error=e.message)
        await websocket.send_json({"event": "error", "message": e.message})
    except Exception as e:
        logger.error("Unexpected error", session_id=session_id, error=str(e))
        await websocket.send_json({"event": "error", "message": str(e)})
    finally:
        # Cancel ping task
        if ping_task:
            ping_task.cancel()
            try:
                await ping_task
            except asyncio.CancelledError:
                pass

        if session_id:
            session_manager = get_session_manager()
            try:
                session_manager.detach_websocket(session_id)
            except Exception:
                # Session might already be disconnected
                pass


# Project API Routes


@app.get("/api/projects")
async def list_projects(
    prefix: str | None = Query(None, description="Filter by project prefix"),
    tags: list[str] | None = Query(None, description="Filter by tags"),
    status: str | None = Query(
        None, description="Filter by status: active, idle, resumable, pinned"
    ),
) -> JSONResponse:
    """List all projects with optional filters.

    Status filters:
    - active: Projects with currently active sessions
    - idle: Projects without active sessions (but can be resumed)
    - resumable: Alias for idle (projects you can reconnect to)
    - pinned: Projects that are pinned (never auto-deleted)
    """
    session_manager = get_session_manager()
    project_manager = session_manager.get_project_manager()

    projects = project_manager.list_projects(prefix_filter=prefix, tags_filter=tags)

    # Apply status filter
    if status:
        status_lower = status.lower()
        if status_lower == "active":
            projects = [p for p in projects if p.active_session]
        elif status_lower in ("idle", "resumable"):
            projects = [p for p in projects if not p.active_session]
        elif status_lower == "pinned":
            projects = [p for p in projects if p.pinned]

    return JSONResponse(
        content={
            "projects": [
                {
                    "project_id": p.project_id,
                    "user_prefix": p.user_prefix,
                    "last_accessed": p.last_accessed.isoformat(),
                    "total_sessions": p.statistics.total_sessions,
                    "status": "active" if p.active_session else "idle",
                    "cleanup_in_days": p.days_until_cleanup,
                    "pinned": p.pinned,
                    "has_memories": (
                        project_manager._get_memories_path(p.project_id)
                        / "persistent"
                        / "snapshot.json"
                    ).exists(),
                }
                for p in projects
            ],
            "total": len(projects),
        }
    )


@app.get("/api/sessions/resumable")
async def list_resumable_sessions() -> JSONResponse:
    """List all projects that can be resumed (have disconnected sessions with saved state).

    These are projects that:
    - Don't have an active session (disconnected)
    - Have workspace data preserved
    - May have saved memories to restore
    """
    session_manager = get_session_manager()
    project_manager = session_manager.get_project_manager()

    # Get all projects without active sessions
    projects = project_manager.list_projects()
    resumable = [p for p in projects if not p.active_session]

    # Sort by last accessed (most recent first)
    resumable.sort(key=lambda p: p.last_accessed, reverse=True)

    return JSONResponse(
        content={
            "sessions": [
                {
                    "project_id": p.project_id,
                    "user_prefix": p.user_prefix,
                    "last_accessed": p.last_accessed.isoformat(),
                    "last_session_duration_seconds": p.sessions[-1].duration_seconds
                    if p.sessions
                    else None,
                    "total_messages": p.statistics.total_messages,
                    "workspace_path": str(project_manager._get_repo_path(p.project_id)),
                    "has_memories": (
                        project_manager._get_memories_path(p.project_id)
                        / "persistent"
                        / "snapshot.json"
                    ).exists(),
                    "cleanup_in_days": p.days_until_cleanup,
                }
                for p in resumable
            ],
            "total": len(resumable),
            "message": f"Found {len(resumable)} project(s) ready to resume"
            if resumable
            else "No resumable projects found. Create a new project with /create <name>",
        }
    )


@app.post("/api/projects")
async def create_project(request: dict[str, Any]) -> JSONResponse:
    """Create a new project."""
    user_prefix = request.get("user_prefix")
    if not user_prefix:
        return JSONResponse(
            status_code=400,
            content={"error": "user_prefix is required"},
        )

    session_manager = get_session_manager()
    project_manager = session_manager.get_project_manager()

    try:
        config = ProjectConfig(
            network_mode=request.get("network_mode", "OFF"),
            repo_url=request.get("repo_url"),
            agent_model=request.get("agent_model") or "claude-haiku-4.5",
            container_image=request.get("container_image") or "opencode-agent:py",
        )

        project = project_manager.create_project(
            user_prefix=user_prefix,
            config=config,
            tags=request.get("tags", []),
            description=request.get("description", ""),
        )

        return JSONResponse(
            content={
                "project_id": project.project_id,
                "user_prefix": project.user_prefix,
                "created_at": project.created_at.isoformat(),
                "workspace_path": str(
                    session_manager.workspace_manager.get_workspace_path(project.project_id)
                ),
            }
        )
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        logger.error("Failed to create project", error=str(e))
        return JSONResponse(status_code=500, content={"error": "Failed to create project"})


@app.get("/api/projects/{project_id}")
async def get_project(project_id: str) -> JSONResponse:
    """Get project details."""
    session_manager = get_session_manager()
    project_manager = session_manager.get_project_manager()

    try:
        project = project_manager.load_project(project_id)
        return JSONResponse(
            content={
                "project_id": project.project_id,
                "user_prefix": project.user_prefix,
                "created_at": project.created_at.isoformat(),
                "last_accessed": project.last_accessed.isoformat(),
                "config": project.config.to_dict(),
                "statistics": project.statistics.to_dict(),
                "sessions": [s.to_dict() for s in project.sessions],
                "tags": project.tags,
                "description": project.description,
                "pinned": project.pinned,
                "cleanup_in_days": project.days_until_cleanup,
            }
        )
    except Exception as e:
        return JSONResponse(status_code=404, content={"error": str(e)})


@app.post("/api/projects/{project_id}/sessions")
async def create_project_session(project_id: str, request: dict[str, Any]) -> JSONResponse:
    """Create a new session for a project."""
    session_manager = get_session_manager()

    try:
        network_mode = request.get("network_mode")

        session = session_manager.create_or_resume_session(
            project_id=project_id,
            network_mode=network_mode,
        )

        return JSONResponse(
            content={
                "session_id": session.session_id,
                "project_id": session.project_id,
                "network_mode": session.network_mode,
                "workspace_path": session.workspace_path,
                "container_id": session.container_id,
            }
        )
    except Exception as e:
        logger.error("Failed to create session", project_id=project_id, error=str(e))
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/projects/{project_id}/extend")
async def extend_project(project_id: str, request: dict[str, Any]) -> JSONResponse:
    """Extend project lifetime or pin it."""
    session_manager = get_session_manager()
    project_manager = session_manager.get_project_manager()

    try:
        days = request.get("days")
        pin = request.get("pin", False)

        if pin:
            project_manager.pin_project(project_id)
            return JSONResponse(content={"message": "Project pinned", "project_id": project_id})
        elif days is not None:
            project_manager.extend_project_lifetime(project_id, days)
            return JSONResponse(
                content={
                    "message": f"Project extended by {days} days",
                    "project_id": project_id,
                }
            )
        else:
            return JSONResponse(
                status_code=400,
                content={"error": "Either 'days' or 'pin' must be specified"},
            )
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.post("/api/projects/{project_id}/unpin")
async def unpin_project(project_id: str) -> JSONResponse:
    """Unpin a project."""
    session_manager = get_session_manager()
    project_manager = session_manager.get_project_manager()

    try:
        project_manager.unpin_project(project_id)
        return JSONResponse(content={"message": "Project unpinned", "project_id": project_id})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


@app.delete("/api/projects/{project_id}")
async def delete_project(
    project_id: str, force: bool = Query(False, description="Force delete even if active")
) -> JSONResponse:
    """Delete a project."""
    session_manager = get_session_manager()
    project_manager = session_manager.get_project_manager()

    try:
        project_manager.delete_project(project_id, force=force)
        return JSONResponse(content={"message": "Project deleted", "project_id": project_id})
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": str(e)})


def main() -> None:
    """Run the server."""
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "server.app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=settings.debug,
    )


if __name__ == "__main__":
    main()
