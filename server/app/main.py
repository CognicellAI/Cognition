"""FastAPI server with REST API and SSE streaming.

Phase 5: REST API Migration
- Replaces WebSocket with REST + Server-Sent Events
- Full OpenAPI 3.1 documentation
- Standard HTTP tooling support
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from server.app.api.models import HealthStatus, ReadyStatus, ErrorResponse
from server.app.api.routes import config, messages, sessions
from server.app.middleware import ObservabilityMiddleware, SecurityHeadersMiddleware
from server.app.observability import get_logger, setup_logging, setup_metrics, setup_tracing
from server.app.rate_limiter import get_rate_limiter
from server.app.settings import get_settings

# Setup logging on module import
setup_logging()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan context manager.

    Handles startup and shutdown events.
    """
    # Startup
    logger.info("Starting Cognition server")

    settings = get_settings()

    # Setup observability
    setup_tracing(endpoint=settings.otel_endpoint)
    setup_metrics()

    # Initialize rate limiter
    rate_limiter = get_rate_limiter()
    await rate_limiter.start()

    logger.info(
        "Server configuration",
        host=settings.host,
        port=settings.port,
        llm_provider=settings.llm_provider,
    )

    yield

    # Shutdown
    logger.info("Shutting down Cognition server")

    # Stop rate limiter
    await rate_limiter.stop()

    logger.info("Server shutdown complete")


# Create FastAPI app with OpenAPI documentation
app = FastAPI(
    title="Cognition",
    description="AI-powered coding assistant with REST API and SSE streaming",
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
    lifespan=lifespan,
)

# Add middleware
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(ObservabilityMiddleware)


# Include API routes
app.include_router(sessions.router)
app.include_router(messages.router)
app.include_router(config.router)


@app.get("/health", response_model=HealthStatus, tags=["health"])
async def health_check() -> HealthStatus:
    """Health check endpoint.

    Returns server health status and basic information.
    """
    return HealthStatus(
        status="healthy",
        version="0.1.0",
        active_sessions=0,  # TODO: Get from session manager
        timestamp=datetime.utcnow(),
    )


@app.get("/ready", response_model=ReadyStatus, tags=["health"])
async def ready_check() -> ReadyStatus:
    """Readiness probe endpoint.

    Returns whether the server is ready to accept requests.
    Used by Kubernetes and load balancers.
    """
    return ReadyStatus(ready=True)


@app.exception_handler(Exception)
async def general_exception_handler(request, exc):
    """Handle unhandled exceptions."""
    logger.error(
        "Unhandled exception",
        error=str(exc),
        path=request.url.path,
        method=request.method,
    )
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error", "code": "INTERNAL_ERROR"},
    )


def main():
    """Run the server."""
    import uvicorn

    settings = get_settings()

    uvicorn.run(
        "server.app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        reload=False,
    )


if __name__ == "__main__":
    main()
