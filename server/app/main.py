"""FastAPI server with REST API and SSE streaming."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from server.app.agent.resolver import RuntimeResolver
from server.app.api.dependencies import (
    get_storage_backend_dep,
    set_artifact_store,
    set_config_store,
    set_mcp_oauth_flow_coordinator,
    set_mcp_oauth_state_repository,
    set_model_catalog_dep,
    set_runtime_resolver,
    set_session_agent_manager_dep,
    set_storage_backend_dep,
)
from server.app.api.middleware import (
    ObservabilityMiddleware,
    SecurityHeadersMiddleware,
    route_template_for_request,
)
from server.app.api.models import HealthStatus, ReadyStatus
from server.app.api.routes import (
    agents,
    artifacts,
    capabilities,
    config,
    mcp_oauth,
    messages,
    models,
    sandbox_profiles,
    sessions,
    skills,
    tools,
)
from server.app.exceptions import RateLimitError
from server.app.file_watcher import WorkspaceWatcher
from server.app.models import SessionStatus
from server.app.observability import setup_logging, setup_metrics, setup_tracing
from server.app.rate_limiter import RateLimitConfig, get_rate_limiter
from server.app.session_manager import initialize_session_manager
from server.app.settings import get_settings
from server.app.storage import create_storage_backend
from server.app.storage.backend import StorageBackend
from server.app.storage.config_store import DefaultConfigStore, set_default_config_store
from server.version import VERSION

logger = structlog.get_logger(__name__)

# Global file watcher instance
file_watcher: WorkspaceWatcher | None = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager."""
    global file_watcher

    settings = get_settings()
    setup_logging(
        log_level=settings.log_level,
        json_format=settings.log_format == "json",
    )
    logger.info("Starting Cognition server")
    settings.validate_deployment_storage_policy()

    # Initialize storage backend
    storage_backend = create_storage_backend(settings)
    await storage_backend.initialize()
    set_storage_backend_dep(storage_backend)
    logger.info("Storage backend initialized")

    from server.app.storage.factory import create_mcp_oauth_state_repository

    mcp_oauth_state_repository = create_mcp_oauth_state_repository(settings)
    await mcp_oauth_state_repository.initialize()
    set_mcp_oauth_state_repository(mcp_oauth_state_repository)
    logger.info("MCP OAuth state repository initialized")

    from server.app.agent.mcp_oauth_flow import McpOAuthFlowCoordinator

    mcp_oauth_flow_coordinator = McpOAuthFlowCoordinator(
        settings=settings,
        repository=mcp_oauth_state_repository,
    )
    set_mcp_oauth_flow_coordinator(mcp_oauth_flow_coordinator)

    # Initialize ConfigRegistry
    from server.app.storage.factory import create_config_dispatcher, create_config_registry

    config_registry = create_config_registry(settings)
    if hasattr(config_registry, "initialize_schema"):
        await config_registry.initialize_schema()
    logger.info("ConfigRegistry initialized")

    # Initialize ConfigStore (unified interface)
    config_store = DefaultConfigStore(
        config_registry=config_registry,
        workspace_path=settings.workspace_path,
    )
    set_config_store(config_store)
    set_default_config_store(config_store)
    logger.info("ConfigStore initialized")

    # Seed provider config from config.yaml (insert-if-absent)
    from server.app.bootstrap import (
        seed_providers_from_config,
        seed_sandbox_profiles_from_config,
        seed_skills_from_sources,
        seed_tools_from_sources,
    )
    from server.app.config_loader import load_config

    yaml_config = load_config(cwd=settings.workspace_path)
    logger.debug("Loaded YAML config", keys=list(yaml_config.keys()))
    await seed_providers_from_config(yaml_config, config_store)
    sandbox_profiles_seeded = await seed_sandbox_profiles_from_config(yaml_config, config_store)
    skills_seeded = await seed_skills_from_sources(
        yaml_config, config_store, settings.workspace_path
    )
    tools_seeded = await seed_tools_from_sources(yaml_config, config_store, settings.workspace_path)
    if sandbox_profiles_seeded or skills_seeded or tools_seeded:
        logger.info(
            "Bootstrapped file sources",
            sandbox_profiles=sandbox_profiles_seeded,
            skills=skills_seeded,
            tools=tools_seeded,
        )

    # Seed store-backed agent definitions after ConfigStore is available.
    await config_store.seed_agent_definitions()

    # Initialize ArtifactStore
    from server.app.storage.factory import create_artifact_store

    artifact_store = create_artifact_store(settings)
    if hasattr(artifact_store, "initialize"):
        await artifact_store.initialize()
    set_artifact_store(artifact_store)
    logger.info("ArtifactStore initialized")

    # Initialize RuntimeResolver (agent runtime bridge)
    runtime_resolver = RuntimeResolver(config_store=config_store, settings=settings)
    set_runtime_resolver(runtime_resolver)
    logger.info("RuntimeResolver initialized")

    # Initialize ConfigChangeDispatcher and wire hot-reload subscribers
    dispatcher = create_config_dispatcher(settings)
    dispatcher.subscribe(config_store.on_config_change)
    await dispatcher.start()
    logger.info("ConfigChangeDispatcher started")

    # Initialize session manager
    initialize_session_manager(storage_backend, settings)
    logger.info("Session manager initialized")

    # Initialize SessionAgentManager for DI
    from server.app.llm.deep_agent_service import SessionAgentManager

    session_agent_manager = SessionAgentManager(
        settings,
        storage_backend=storage_backend,
        runtime_resolver=runtime_resolver,
        config_store=config_store,
        mcp_oauth_repository=mcp_oauth_state_repository,
    )
    set_session_agent_manager_dep(session_agent_manager)
    logger.info("SessionAgentManager initialized")

    # Mount A2A protocol adapter (requires COGNITION_A2A_ENABLED=true)
    if settings.a2a_enabled:
        from server.app.protocols.a2a.security import parse_a2a_card_security

        card_security = parse_a2a_card_security(
            settings.a2a_security_schemes,
            settings.a2a_security_requirements,
        )
        try:
            from server.app.protocols.a2a.routes import mount_a2a_routes

            await mount_a2a_routes(
                app=app,
                settings=settings,
                config_store=config_store,
                session_agent_manager=session_agent_manager,
                store=storage_backend,
                version=VERSION,
                artifact_store=artifact_store,
                card_security=card_security,
            )
            logger.info("A2A protocol adapter mounted")
        except Exception as e:
            logger.warning("A2A adapter not mounted", error=str(e))
    else:
        logger.info("A2A protocol adapter disabled (COGNITION_A2A_ENABLED=false)")

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
        file_watcher = WorkspaceWatcher()

        # Watch tools and middleware directories
        tools_path = settings.workspace_path / ".cognition" / "tools"
        middleware_path = settings.workspace_path / ".cognition" / "middleware"

        # Create directories if they don't exist
        tools_path.mkdir(parents=True, exist_ok=True)
        middleware_path.mkdir(parents=True, exist_ok=True)

        file_watcher.watch_tools(str(tools_path))
        file_watcher.watch_middleware(str(middleware_path))
        file_watcher.start()
        logger.info("File watcher started", watched_paths=["tools", "middleware"])
    except Exception as e:
        logger.warning("Failed to start file watcher", error_type=type(e).__name__)

    setup_tracing(
        endpoint=settings.otel_endpoint,
        app=app,
        enabled=settings.otel_enabled,
        max_export_bytes=settings.otel_max_export_bytes,
        queue_size=settings.otlp_queue_size,
        export_timeout_millis=settings.otlp_export_timeout_ms,
        trace_sample_ratio=settings.trace_sample_ratio,
        metric_export_interval_millis=settings.otlp_metric_export_interval_ms,
        trace_detail=settings.trace_detail,
        observability_scope_hmac_key=(
            settings.observability_scope_hmac_key.get_secret_value()
            if settings.observability_scope_hmac_key is not None
            else None
        ),
    )
    setup_metrics(
        port=settings.metrics_port,
        enabled=settings.metrics_enabled,
    )
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
        metrics_enabled=settings.metrics_enabled,
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
    await mcp_oauth_flow_coordinator.close()
    await mcp_oauth_state_repository.close()
    logger.info("Server shutdown complete")


app = FastAPI(
    title="Cognition",
    description="AI-powered coding assistant",
    version=VERSION,
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
app.include_router(sandbox_profiles.router)
app.include_router(agents.router)
app.include_router(skills.router)
app.include_router(models.router)
app.include_router(tools.router)
app.include_router(artifacts.router)
app.include_router(capabilities.router)
app.include_router(mcp_oauth.router)


@app.get("/health", response_model=HealthStatus, tags=["health"])
async def health_check(
    storage_backend: StorageBackend = Depends(get_storage_backend_dep),  # noqa: B008
) -> HealthStatus:
    """Health check endpoint."""
    sessions_list = await storage_backend.list_sessions()
    active_sessions = sum(
        1
        for session in sessions_list
        if not SessionStatus.is_terminal(getattr(session, "status", "active"))
    )

    return HealthStatus(
        status="healthy",
        version=VERSION,
        active_sessions=active_sessions,
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
        error_type=type(exc).__name__,
        endpoint=route_template_for_request(request),
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
        error_type=type(exc).__name__,
        endpoint=route_template_for_request(request),
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
