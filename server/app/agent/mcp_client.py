"""MCP (Model Context Protocol) adapter for Cognition.

Wraps langchain-mcp-adapters for Cognition's remote-only MCP server integration.
Replaces the previous custom McpSseClient / McpManager / McpAdapterTool layer.

Security stance:
- Remote MCP servers: Allowed (Streamable HTTP transport, HTTP/HTTPS URLs only).
- Local (stdio) MCP servers: NOT supported for security reasons.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
import structlog
from langchain_core.tools import BaseTool
from langchain_mcp_adapters.callbacks import CallbackContext, Callbacks
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import ToolCallInterceptor
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from mcp.types import LoggingMessageNotificationParams
from pydantic import BaseModel, ConfigDict, Field, field_validator

from server.app.agent.definition import (
    AgentMcpServerConfig,
    McpAuthConfig,
    McpNoAuthConfig,
    McpOAuthConfig,
    McpStaticBearerAuthConfig,
    McpWorkloadTokenExchangeAuthConfig,
)
from server.app.agent.mcp_auth import (
    AmbientWorkloadIdentity,
    McpAuthenticationError,
    McpTrustedContextInterceptor,
    StaticBearerAuth,
    WorkloadTokenExchangeAuth,
    create_mcp_oauth_auth,
    resolve_workload_client_secret,
    trusted_context_headers,
)
from server.app.settings import McpWorkloadTokenExchangeProfile, Settings
from server.app.storage.mcp_oauth import McpOAuthStateRepository
from server.app.storage.mcp_readiness import (
    McpReadinessObservation,
    McpReadinessRepository,
)

logger = structlog.get_logger(__name__)


class McpServerDiscoveryError(RuntimeError):
    """Raised when a required MCP server cannot be discovered."""

    def __init__(self, server_alias: str, category: str = "discovery_failed") -> None:
        self.server_alias = server_alias
        self.category = category
        super().__init__(f"MCP server '{server_alias}' failed: {category}")


class McpTransportAuthenticationError(RuntimeError):
    """Raised when selected MCP transport authentication cannot be applied."""

    def __init__(self, server_alias: str, category: str = "auth_unavailable") -> None:
        self.server_alias = server_alias
        self.category = category
        super().__init__(f"MCP server '{server_alias}' failed: {category}")


class McpServerConfig(BaseModel):
    """Configuration for a remote MCP server connection."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Unique name for this MCP server")
    url: str = Field(..., description="MCP Streamable HTTP endpoint")
    required: bool = Field(default=True, description="Whether discovery failure is fatal")
    transport: Literal["streamable_http"] = Field(
        default="streamable_http", description="MCP transport protocol"
    )
    auth: McpAuthConfig = Field(default_factory=McpNoAuthConfig)
    agent_name: str = Field(default="", exclude=True)
    agent_revision: int = Field(default=1, ge=1, exclude=True)
    effective_scope: dict[str, str] = Field(default_factory=dict, exclude=True)
    workload_profile: McpWorkloadTokenExchangeProfile | None = Field(
        default=None,
        exclude=True,
        repr=False,
        description="Resolved deployment profile; never an Agent/API projection",
    )

    @classmethod
    def from_agent_config(
        cls,
        alias: str,
        config: AgentMcpServerConfig,
        settings: Settings,
        *,
        agent_name: str,
        agent_revision: int,
        effective_scope: Mapping[str, str],
    ) -> McpServerConfig:
        workload_profile = None
        if isinstance(config.auth, McpWorkloadTokenExchangeAuthConfig):
            workload_profile = settings.get_mcp_auth_profile(config.auth.profile)
        return cls(
            name=alias,
            url=_canonical_server_uri(config.url),
            required=config.required,
            transport=config.transport,
            auth=config.auth,
            agent_name=agent_name,
            agent_revision=agent_revision,
            effective_scope=dict(effective_scope),
            workload_profile=workload_profile,
        )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str, info: Any) -> str:
        if not v or any(character.isspace() for character in v):
            raise ValueError("MCP server URL must not be empty or contain whitespace")
        try:
            parsed = urlsplit(v)
            hostname = parsed.hostname
            _ = parsed.port
        except ValueError as exc:
            raise ValueError("MCP server URL is malformed") from exc
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or not hostname:
            raise ValueError(
                f"MCP server '{info.data.get('name', 'unknown')}' has invalid URL: {v}. "
                "Only HTTP/HTTPS URLs are supported. "
                "Local (stdio) MCP servers are not supported for security reasons."
            )
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("MCP server URLs must not contain credentials")
        if parsed.fragment:
            raise ValueError("MCP server URLs must not contain fragments")
        return v


class McpToolInfo(BaseModel):
    """Information about an MCP tool, including upstream annotations."""

    name: str
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    task_support: str | None = None


def mcp_config_to_connection(
    config: McpServerConfig,
    settings: Settings,
    oauth_repository: McpOAuthStateRepository | None = None,
) -> StreamableHttpConnection:
    if isinstance(config.auth, McpNoAuthConfig):
        return {"transport": "streamable_http", "url": config.url}
    if isinstance(config.auth, McpStaticBearerAuthConfig):
        try:
            static_auth = StaticBearerAuth.from_environment(config.auth.env)
        except McpAuthenticationError as exc:
            raise McpTransportAuthenticationError(config.name, exc.category) from exc
        return {
            "transport": "streamable_http",
            "url": config.url,
            "auth": static_auth,
        }
    if isinstance(config.auth, McpWorkloadTokenExchangeAuthConfig):
        profile = config.workload_profile
        if profile is None:
            raise McpTransportAuthenticationError(config.name, "auth_profile_unavailable")
        audience = config.url if profile.audience == "canonical_server_uri" else profile.audience
        try:
            workload_auth = WorkloadTokenExchangeAuth(
                profile=profile,
                audience=audience,
                identity=AmbientWorkloadIdentity.from_settings(settings),
                client_secret=resolve_workload_client_secret(profile),
            )
            headers = trusted_context_headers(**_trusted_context_kwargs(config))
        except McpAuthenticationError as exc:
            raise McpTransportAuthenticationError(config.name, exc.category) from exc
        return {
            "transport": "streamable_http",
            "url": config.url,
            "headers": headers,
            "auth": workload_auth,
        }
    if isinstance(config.auth, McpOAuthConfig):
        try:
            oauth_auth = create_mcp_oauth_auth(
                settings=settings,
                repository=oauth_repository,
                agent_name=config.agent_name,
                effective_scope=config.effective_scope,
                canonical_server_uri=config.url,
            )
        except McpAuthenticationError as exc:
            raise McpTransportAuthenticationError(config.name, exc.category) from exc
        return {
            "transport": "streamable_http",
            "url": config.url,
            "auth": oauth_auth,
        }
    raise McpTransportAuthenticationError(config.name, "auth_invalid")


def create_mcp_client(
    configs: Sequence[McpServerConfig],
    settings: Settings,
    callbacks: Callbacks | None = None,
    tool_interceptors: list[ToolCallInterceptor] | None = None,
    *,
    oauth_repository: McpOAuthStateRepository | None = None,
) -> MultiServerMCPClient:
    connections: dict[str, Any] = {
        c.name: mcp_config_to_connection(c, settings, oauth_repository) for c in configs
    }
    workload_contexts = {
        config.name: _trusted_context_kwargs(config)
        for config in configs
        if isinstance(config.auth, McpWorkloadTokenExchangeAuthConfig)
    }
    interceptors = list(tool_interceptors or [])
    if workload_contexts:
        # Security projection is innermost so earlier interceptors cannot add or
        # retain arbitrary per-call headers after this default-deny overwrite.
        interceptors.append(McpTrustedContextInterceptor(workload_contexts))
    return MultiServerMCPClient(
        connections=connections or None,
        callbacks=callbacks,
        tool_interceptors=interceptors or None,
        tool_name_prefix=True,
    )


async def load_mcp_tools_per_server(
    configs: Sequence[McpServerConfig],
    settings: Settings,
    callbacks: Callbacks | None = None,
    tool_interceptors: list[ToolCallInterceptor] | None = None,
    *,
    oauth_repository: McpOAuthStateRepository | None = None,
    readiness_repository: McpReadinessRepository | None = None,
) -> list[BaseTool]:
    """Load MCP tools server-by-server and enforce canonical identity uniqueness."""

    tools: list[BaseTool] = []
    seen_canonical: set[tuple[str, str]] = set()
    seen_visible: set[str] = set()
    for config in configs:
        try:
            client_kwargs: dict[str, Any] = {
                "callbacks": callbacks,
                "tool_interceptors": tool_interceptors,
            }
            if oauth_repository is not None:
                client_kwargs["oauth_repository"] = oauth_repository
            client = create_mcp_client([config], settings, **client_kwargs)
            server_tools = await client.get_tools(server_name=config.name)
        except Exception as exc:
            category = (
                exc.category
                if isinstance(exc, McpTransportAuthenticationError)
                else "discovery_failed"
            )
            logger.warning(
                "mcp_server_discovery_failed",
                server=config.name,
                required=config.required,
                category=category,
                error_type=type(exc).__name__,
            )
            await _record_readiness(
                readiness_repository,
                config,
                settings,
                status="unavailable",
                failure_category=category,
            )
            if config.required:
                raise McpServerDiscoveryError(config.name, category) from exc
            continue

        server_identities: list[tuple[tuple[str, str], str, BaseTool]] = []
        for tool in server_tools:
            visible_name = str(getattr(tool, "name", ""))
            prefix = f"{config.name}__"
            provider_name = (
                visible_name.removeprefix(prefix)
                if visible_name.startswith(prefix)
                else visible_name
            )
            canonical_identity = (config.name, provider_name)
            if canonical_identity in seen_canonical or visible_name in seen_visible:
                await _record_readiness(
                    readiness_repository,
                    config,
                    settings,
                    status="unavailable",
                    failure_category="duplicate_tool_identity",
                )
                raise McpServerDiscoveryError(config.name, "duplicate_tool_identity")
            server_identities.append((canonical_identity, visible_name, tool))

        for canonical_identity, visible_name, tool in server_identities:
            seen_canonical.add(canonical_identity)
            seen_visible.add(visible_name)
            tools.append(tool)
        await _record_readiness(
            readiness_repository,
            config,
            settings,
            status="ready",
            tools=server_tools,
        )
    return tools


async def _record_readiness(
    repository: McpReadinessRepository | None,
    config: McpServerConfig,
    settings: Settings,
    *,
    status: Literal["ready", "unavailable"],
    tools: Sequence[BaseTool] = (),
    failure_category: str | None = None,
) -> None:
    """Persist a redacted observation without making telemetry a runtime dependency."""
    observed_at = datetime.now(UTC)
    observation = McpReadinessObservation(
        agent_name=config.agent_name,
        agent_revision=config.agent_revision,
        server_alias=config.name,
        required=config.required,
        status=status,
        tool_count=len(tools),
        schema_digest=_tool_schema_digest(tools) if status == "ready" else None,
        failure_category=failure_category,
        observed_at=observed_at,
        fresh_until=observed_at + timedelta(seconds=settings.mcp_readiness_ttl_seconds),
    )
    logger.info(
        "mcp_readiness_observed",
        required=config.required,
        status=status,
        tool_count=len(tools),
        failure_category=failure_category,
    )
    if repository is None:
        return
    try:
        await repository.record(observation, config.effective_scope)
    except Exception as exc:
        logger.warning(
            "mcp_readiness_persistence_failed",
            error_type=type(exc).__name__,
        )


def _tool_schema_digest(tools: Sequence[BaseTool]) -> str:
    schemas: list[dict[str, Any]] = []
    for tool in tools:
        args_schema = getattr(tool, "args_schema", None)
        model_json_schema = getattr(args_schema, "model_json_schema", None)
        if callable(model_json_schema):
            input_schema = model_json_schema()
        elif isinstance(args_schema, dict):
            input_schema = args_schema
        else:
            input_schema = {}
        schemas.append(
            {
                "name": str(getattr(tool, "name", "")),
                "input_schema": input_schema,
            }
        )
    payload = json.dumps(schemas, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _trusted_context_kwargs(config: McpServerConfig) -> dict[str, Any]:
    return {
        "agent_name": config.agent_name,
        "agent_revision": config.agent_revision,
        "effective_scope": config.effective_scope,
        "server_alias": config.name,
        "canonical_server_uri": config.url,
    }


def _canonical_server_uri(value: str) -> str:
    """Canonicalize an already validated MCP HTTP endpoint."""
    return str(httpx.URL(value))


def _build_mcp_callbacks() -> Callbacks:
    async def on_progress(
        progress: float,
        total: float | None,
        message: str | None,
        context: CallbackContext,
    ) -> None:
        logger.info(
            "mcp_tool_progress",
            server=context.server_name,
            tool=context.tool_name,
            progress=progress,
            total=total,
            message_present=message is not None,
        )

    async def on_logging_message(
        params: LoggingMessageNotificationParams,
        context: CallbackContext,
    ) -> None:
        log_method = logger.warning if params.level == "error" else logger.info
        log_method(
            "mcp_server_log",
            server=context.server_name,
            level=params.level,
            data_present=params.data is not None,
        )

    return Callbacks(
        on_progress=on_progress,
        on_logging_message=on_logging_message,
    )
