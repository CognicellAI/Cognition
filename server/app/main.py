"""FastAPI server with REST API and SSE streaming."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.app.agent.agent_definition_registry import (
    initialize_agent_definition_registry,
)
from server.app.agent_registry import initialize_agent_registry
from server.app.api.middleware import ObservabilityMiddleware, SecurityHeadersMiddleware
from server.app.api.models import HealthStatus, ReadyStatus
from server.app.api.routes import agents, config, messages, models, sessions, skills, tools
from server.app.exceptions import RateLimitError
from server.app.file_watcher import WorkspaceWatcher
from server.app.observability import setup_metrics, setup_tracing
from server.app.observability.mlflow_config import setup_mlflow_tracing
from server.app.rate_limiter import RateLimitConfig, get_rate_limiter
from server.app.session_manager import initialize_session_manager
from server.app.settings import get_settings
from server.app.storage import create_storage_backend, get_storage_backend, set_storage_backend

logger = structlog.get_logger(__name__)

# Global file watcher instance
file_watcher: WorkspaceWatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager."""
    global file_watcher

    logger.info("Starting Cognition server")
    settings = get_settings()

    # Initialize storage backend
    storage_backend = create_storage_backend(settings)
    await storage_backend.initialize()
    set_storage_backend(storage_backend)
    logger.info("Storage backend initialized")

    # Initialize ConfigRegistry and wire it globally
    from server.app.storage.config_registry import set_config_registry
    from server.app.storage.factory import create_config_dispatcher, create_config_registry

    config_registry = create_config_registry(settings)
    set_config_registry(config_registry)
    logger.info("ConfigRegistry initialized")

    # Seed provider config from config.yaml (insert-if-absent)
    from server.app.bootstrap import seed_providers_from_config
    from server.app.config_loader import load_config

    yaml_config = load_config()
    await seed_providers_from_config(yaml_config)

    # Initialize agent definition registry (file-based agents)
    def_registry = initialize_agent_definition_registry(settings.workspace_path)
    logger.info("Agent definition registry initialized")

    # Seed ConfigRegistry from file-based agents (insert-if-absent)
    await def_registry.seed_from_registry(config_registry)

    # Initialize ConfigChangeDispatcher and wire hot-reload subscribers
    dispatcher = create_config_dispatcher(settings)
    dispatcher.subscribe(def_registry.on_config_change)
    await dispatcher.start()
    logger.info("ConfigChangeDispatcher started")

    # Initialize session manager
    initialize_session_manager(storage_backend, settings)
    logger.info("Session manager initialized")

    # Initialize agent registry for tools and middleware
    initialize_agent_registry(settings=settings)
    logger.info("Agent registry initialized")

    # Set up file watcher for hot-reload
    try:
        from server.app.agent_registry import get_agent_registry

        registry = get_agent_registry()
        file_watcher = WorkspaceWatcher(agent_registry=registry)

        # Watch tools and middleware directories
        tools_path = settings.workspace_path / ".cognition" / "tools"
        middleware_path = settings.workspace_path / ".cognition" / "middleware"

        # Create directories if they don't exist
        tools_path.mkdir(parents=True, exist_ok=True)
        middleware_path.mkdir(parents=True, exist_ok=True)

        file_watcher.watch_tools(str(tools_path))
        file_watcher.watch_middleware(str(middleware_path))
        file_watcher.start()
        logger.info("File watcher started", tools=str(tools_path), middleware=str(middleware_path))
    except Exception as e:
        logger.warning("Failed to start file watcher", error=str(e))

    setup_tracing(
        endpoint=settings.otel_endpoint,
        app=app,
        enabled=settings.otel_enabled,
    )
    setup_metrics(
        port=settings.metrics_port,
        enabled=settings.otel_enabled,
    )
    setup_mlflow_tracing()
    rate_limiter = get_rate_limiter(
        RateLimitConfig(
            requests_per_minute=settings.rate_limit_per_minute,
            burst_size=settings.rate_limit_burst,
        )
    )
    await rate_limiter.start()
    logger.info(
        "Server configuration",
        otel_enabled=settings.otel_enabled,
        persistence_backend=settings.persistence_backend,
    )
    yield
    logger.info("Shutting down Cognition server")

    # Stop file watcher
    if file_watcher:
        file_watcher.stop()
        logger.info("File watcher stopped")

    await rate_limiter.stop()

    # Stop ConfigChangeDispatcher
    await dispatcher.stop()
    logger.info("ConfigChangeDispatcher stopped")

    # Close storage backend connections
    if storage_backend:
        await storage_backend.close()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="Cognition",
    description="AI-powered coding assistant",
    version="0.4.0",
    lifespan=lifespan,
)

# Add CORS middleware first (must be before other middlewares)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "PUT", "DELETE", "PATCH"],
    allow_headers=["Content-Type", "Authorization"],
    allow_credentials=settings.cors_credentials,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ObservabilityMiddleware)

app.include_router(sessions.router)
app.include_router(messages.router)
app.include_router(config.router)
app.include_router(agents.router)
app.include_router(skills.router)
app.include_router(models.router)
app.include_router(tools.router)


@app.get("/health", response_model=HealthStatus, tags=["health"])
async def health_check() -> HealthStatus:
    """Health check endpoint."""
    storage_backend = get_storage_backend()
    sessions_list = await storage_backend.list_sessions()

    return HealthStatus(
        status="healthy",
        version="0.4.0",
        active_sessions=len(sessions_list),
        circuit_breakers=[],
        timestamp=datetime.now(UTC),
    )


@app.get("/ready", response_model=ReadyStatus, tags=["health"])
async def ready_check() -> ReadyStatus:
    """Readiness probe endpoint."""
    return ReadyStatus(ready=True)


@app.exception_handler(RateLimitError)
async def rate_limit_exception_handler(request: Request, exc: RateLimitError) -> JSONResponse:
    """Handle rate limit exceeded errors."""
    logger.warning(
        "Rate limit exceeded",
        error=str(exc),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded: {exc.message}"},
    )


@app.exception_handler(Exception)
async def general_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Handle unhandled exceptions."""
    logger.error(
        "Unhandled exception",
        error=str(exc),
        path=request.url.path,
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "error": str(exc)},
    )
