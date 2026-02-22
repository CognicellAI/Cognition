"""FastAPI server with REST API and SSE streaming."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.app.api.models import ErrorResponse, HealthStatus, ReadyStatus
from server.app.api.routes import config, messages, sessions
from server.app.exceptions import RateLimitError
from server.app.api.middleware import ObservabilityMiddleware, SecurityHeadersMiddleware
from server.app.observability.mlflow_tracing import setup_mlflow_tracing
from server.app.observability import setup_metrics, setup_tracing
from server.app.rate_limiter import get_rate_limiter
from server.app.settings import get_settings
from server.app.storage import create_storage_backend, get_storage_backend, set_storage_backend

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager."""
    logger.info("Starting Cognition server")
    settings = get_settings()

    # Initialize storage backend
    storage_backend = create_storage_backend(settings)
    await storage_backend.initialize()
    set_storage_backend(storage_backend)
    logger.info("Storage backend initialized")

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


@app.get("/health", response_model=HealthStatus, tags=["health"])
async def health_check() -> HealthStatus:
    """Health check endpoint."""
    storage_backend = get_storage_backend()
    sessions_list = await storage_backend.list_sessions()

    return HealthStatus(
        status="healthy",
        version="0.1.0",
        active_sessions=len(sessions_list),
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
