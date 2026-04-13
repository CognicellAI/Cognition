"""FastAPI server with REST API and SSE streaming."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.app.agent.agent_definition_registry import (
    initialize_agent_definition_registry,
)
from server.app.agent.resolver import RuntimeResolver
from server.app.agent_registry import initialize_agent_registry
from server.app.api.dependencies import (
    get_storage_backend_dep,
    set_agent_registry_dep,
    set_config_store,
    set_model_catalog_dep,
    set_runtime_resolver,
    set_session_agent_manager_dep,
    set_storage_backend_dep,
)
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
from server.app.storage import create_storage_backend
from server.app.storage.backend import StorageBackend
from server.app.storage.config_store import DefaultConfigStore, set_default_config_store

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
    set_storage_backend_dep(storage_backend)
    logger.info("Storage backend initialized")

    # Initialize ConfigRegistry
    from server.app.storage.factory import create_config_dispatcher, create_config_registry

    config_registry = create_config_registry(settings)
    if hasattr(config_registry, "initialize_schema"):
        await config_registry.initialize_schema()
    logger.info("ConfigRegistry initialized")

    # Initialize agent definition registry (file-based agents)
    def_registry = initialize_agent_definition_registry(settings.workspace_path)
    logger.info("Agent definition registry initialized")

    # Initialize ConfigStore (unified interface)
    config_store = DefaultConfigStore(
        config_registry=config_registry,
        agent_definition_registry=def_registry,
    )
    set_config_store(config_store)
    set_default_config_store(config_store)
    logger.info("ConfigStore initialized")

    # Seed provider config from config.yaml (insert-if-absent)
    from server.app.bootstrap import seed_providers_from_config
    from server.app.config_loader import load_config

    yaml_config = load_config(cwd=settings.workspace_root)
    await seed_providers_from_config(yaml_config, config_store)

    # Seed store-backed agent definitions after ConfigStore is available.
    await def_registry.seed_from_store(config_store)

    # Initialize RuntimeResolver (agent runtime bridge)
    runtime_resolver = RuntimeResolver(config_store=config_store, settings=settings)
    set_runtime_resolver(runtime_resolver)
    logger.info("RuntimeResolver initialized")

    # Initialize ConfigChangeDispatcher and wire hot-reload subscribers
    dispatcher = create_config_dispatcher(settings)
    dispatcher.subscribe(def_registry.on_config_change)
    await dispatcher.start()
    logger.info("ConfigChangeDispatcher started")

    # Initialize session manager
    initialize_session_manager(storage_backend, settings)
    logger.info("Session manager initialized")

    # Initialize agent registry for tools and middleware
    agent_reg = initialize_agent_registry(settings=settings)
    set_agent_registry_dep(agent_reg)
    logger.info("Agent registry initialized")

    # Initialize SessionAgentManager for DI
    from server.app.llm.deep_agent_service import SessionAgentManager

    session_agent_manager = SessionAgentManager(settings)
    set_session_agent_manager_dep(session_agent_manager)
    logger.info("SessionAgentManager initialized")

    # Initialize ModelCatalog for DI
    from server.app.llm.model_catalog import ModelCatalog

    model_catalog = ModelCatalog(
        catalog_url=settings.model_catalog_url,
        ttl_seconds=settings.model_catalog_ttl_seconds,
    )
    set_model_catalog_dep(model_catalog)
    logger.info("ModelCatalog initialized")

    # Validate K8s sandbox prerequisites if backend is kubernetes
    if settings.sandbox_backend == "kubernetes":
        from server.app.agent.sandbox_backend import validate_k8s_sandbox_config

        try:
            validate_k8s_sandbox_config(
                namespace=settings.k8s_sandbox_namespace,
                router_url=settings.k8s_sandbox_router_url,
            )
        except RuntimeError as e:
            logger.error("K8s sandbox validation failed", error=str(e))
            raise

    # Set up file watcher for hot-reload
    try:
        file_watcher = WorkspaceWatcher(agent_registry=agent_reg)

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
    version="0.5.0",
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
async def health_check(
    storage_backend: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> HealthStatus:
    """Health check endpoint."""
    sessions_list = await storage_backend.list_sessions()

    return HealthStatus(
        status="healthy",
        version="0.5.0",
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
