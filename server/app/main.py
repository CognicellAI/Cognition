"""FastAPI server with REST API and SSE streaming."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.app.api.middleware import ObservabilityMiddleware, SecurityHeadersMiddleware
from server.app.api.models import HealthStatus, ReadyStatus
from server.app.api.routes import agents, config, messages, models, sessions, tools
from server.app.exceptions import RateLimitError
from server.app.observability import setup_metrics, setup_tracing
from server.app.observability.mlflow_config import setup_mlflow_tracing
from server.app.rate_limiter import get_rate_limiter
from server.app.agent.agent_definition_registry import (
    initialize_agent_definition_registry,
)
from server.app.agent_registry import AgentRegistry, initialize_agent_registry
from server.app.file_watcher import WorkspaceWatcher
from server.app.session_manager import initialize_session_manager
from server.app.settings import get_settings
from server.app.storage import create_storage_backend, get_storage_backend, set_storage_backend

logger = structlog.get_logger(__name__)

# Global file watcher instance
file_watcher: WorkspaceWatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    global file_watcher

    logger.info("Starting Cognition server")
    settings = get_settings()

    # Initialize storage backend
    storage_backend = create_storage_backend(settings)
    await storage_backend.initialize()
    set_storage_backend(storage_backend)
    logger.info("Storage backend initialized")

    # Initialize session manager
    initialize_session_manager(storage_backend, settings)
    logger.info("Session manager initialized")

    # Initialize agent definition registry
    initialize_agent_definition_registry(settings.workspace_path)
    logger.info("Agent definition registry initialized")

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
    setup_mlflow_tracing(settings)
    rate_limiter = get_rate_limiter()
    await rate_limiter.start()
    logger.info(
        "Server configuration",
        llm_provider=settings.llm_provider,
        otel_enabled=settings.otel_enabled,
    )
    yield
    logger.info("Shutting down Cognition server")

    # Stop file watcher
    if file_watcher:
        file_watcher.stop()
        logger.info("File watcher stopped")

    await rate_limiter.stop()
    # Close storage backend connections
    if storage_backend:
        await storage_backend.close()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="Cognition",
    description="AI-powered coding assistant",
    version="0.1.0",
    lifespan=lifespan,
)

# Add CORS middleware first (must be before other middlewares)
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=settings.cors_methods,
    allow_headers=settings.cors_headers,
    allow_credentials=settings.cors_credentials,
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ObservabilityMiddleware)

app.include_router(sessions.router)
app.include_router(messages.router)
app.include_router(config.router)
app.include_router(agents.router)
app.include_router(models.router)  # ISSUE-008: GET /models endpoint
app.include_router(tools.router)


@app.get("/health", response_model=HealthStatus, tags=["health"])
async def health_check() -> HealthStatus:
    """Health check endpoint."""
    from server.app.api.models import CircuitBreakerStatus
    from server.app.execution.circuit_breaker import get_circuit_breaker_registry

    storage_backend = get_storage_backend()
    sessions_list = await storage_backend.list_sessions()

    # Get circuit breaker status for all providers
    breaker_registry = get_circuit_breaker_registry()
    circuit_breakers = []

    for name, breaker in breaker_registry.items():
        if name.startswith("llm_provider_"):
            provider = name.replace("llm_provider_", "")
            metrics = breaker.metrics
            circuit_breakers.append(
                CircuitBreakerStatus(
                    provider=provider,
                    state=metrics.state,
                    total_calls=metrics.total_calls,
                    successful_calls=metrics.successful_calls,
                    failed_calls=metrics.failed_calls,
                    consecutive_failures=metrics.consecutive_failures,
                    last_failure_time=metrics.last_failure_time,
                )
            )

    return HealthStatus(
        status="healthy",
        version="0.1.0",
        active_sessions=len(sessions_list),
        circuit_breakers=circuit_breakers,
        timestamp=datetime.now(UTC),
    )


@app.get("/ready", response_model=ReadyStatus, tags=["health"])
async def ready_check() -> ReadyStatus:
    """Readiness probe endpoint."""
    return ReadyStatus(ready=True)


@app.exception_handler(RateLimitError)
async def rate_limit_exception_handler(request, exc):
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
async def general_exception_handler(request, exc):
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
