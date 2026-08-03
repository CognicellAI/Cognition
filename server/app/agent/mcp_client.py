"""MCP (Model Context Protocol) adapter for Cognition.

Wraps langchain-mcp-adapters for Cognition's remote-only MCP server integration.
Replaces the previous custom McpSseClient / McpManager / McpAdapterTool layer.

Security stance:
- Remote MCP servers: Allowed (SSE transport, HTTP/HTTPS URLs only).
- Local (stdio) MCP servers: NOT supported for security reasons.
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import Sequence
from datetime import datetime
from typing import Any
from urllib.parse import parse_qs, urlparse

import structlog
from langchain_mcp_adapters.callbacks import CallbackContext, Callbacks
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import ToolCallInterceptor
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from mcp.client.auth import OAuthClientProvider
from mcp.shared.auth import OAuthClientMetadata
from mcp.types import LoggingMessageNotificationParams
from pydantic import AnyUrl

from server.app.agent.mcp_config import AgentMcpServer
from server.app.agent.mcp_oauth import (
    EncryptedMcpOAuthTokenStorage,
    McpOAuthAuthorizationStore,
    McpOAuthPartition,
    McpOAuthStorageError,
)
from server.app.agent.outbound_auth import (
    OutboundAuthError,
    OutboundAuthProviderRegistry,
    OutboundAuthRequest,
    OutboundAuthResult,
    get_outbound_auth_provider_registry,
)
from server.app.settings import Settings, get_settings

logger = structlog.get_logger(__name__)


class McpOAuthAuthorizationFlow:
    """Bridge the MCP SDK's PKCE flow to Cognition's durable callback store."""

    def __init__(self, storage: EncryptedMcpOAuthTokenStorage, *, timeout: float = 300.0) -> None:
        self._store = McpOAuthAuthorizationStore(storage)
        self._timeout = timeout
        self._redirect_url: str | None = None
        self._state: str | None = None
        self._redirect_ready = asyncio.Event()

    async def redirect_handler(self, authorization_url: str) -> None:
        state = parse_qs(urlparse(authorization_url).query).get("state", [None])[0]
        if not state:
            raise McpServerUnavailableError("oauth", "oauth_authorization_unavailable")
        await self._store.register(state, ttl_seconds=self._timeout)
        self._state = state
        self._redirect_url = authorization_url
        self._redirect_ready.set()

    async def callback_handler(self) -> tuple[str, str | None]:
        if self._state is None:
            raise McpServerUnavailableError("oauth", "oauth_authorization_unavailable")
        deadline = asyncio.get_running_loop().time() + self._timeout
        while asyncio.get_running_loop().time() < deadline:
            code = await self._store.consume_callback(self._state)
            if code is not None:
                return code, self._state
            await asyncio.sleep(0.2)
        raise McpServerUnavailableError("oauth", "oauth_callback_timeout")

    async def wait_for_redirect(self, *, timeout: float = 20.0) -> str:
        try:
            await asyncio.wait_for(self._redirect_ready.wait(), timeout=timeout)
        except TimeoutError as exc:
            raise McpServerUnavailableError("oauth", "oauth_authorization_unavailable") from exc
        assert self._redirect_url is not None
        return self._redirect_url

    async def record_callback(self, state: str, code: str) -> bool:
        """Save an OAuth callback only if it belongs to this exact partition."""
        return await self._store.record_callback(state, code)


class McpServerUnavailableError(RuntimeError):
    """A required MCP server could not be discovered without exposing secrets."""

    def __init__(self, alias: str, category: str) -> None:
        super().__init__(f"Required MCP server '{alias}' is unavailable ({category})")
        self.alias = alias
        self.category = category


def create_agent_mcp_client(
    config: AgentMcpServer,
    *,
    callbacks: Callbacks | None = None,
    tool_interceptors: list[ToolCallInterceptor] | None = None,
    outbound_auth: OutboundAuthResult | None = None,
    agent_identity: str = "",
    runtime_snapshot: str = "",
    trusted_context: dict[str, str] | None = None,
    settings: Settings | None = None,
    oauth_flow: McpOAuthAuthorizationFlow | None = None,
) -> MultiServerMCPClient:
    """Create a client for exactly one validated Agent MCP server.

    Discovery is deliberately isolated per server. The caller applies required
    versus optional failure semantics without letting one unavailable optional
    service remove tools from healthy services.
    """
    headers: dict[str, str] | None = None
    if config.auth.type == "static_bearer":
        resolved_settings = settings or get_settings()
        if resolved_settings.deployment_mode == "production":
            raise McpServerUnavailableError(config.alias, "static_bearer_not_allowed")
        assert config.auth.env is not None
        token = os.environ.get(config.auth.env)
        if not token:
            raise McpServerUnavailableError(config.alias, "auth_unavailable")
        headers = {"Authorization": f"Bearer {token}"}
    elif config.auth.type == "mcp_oauth":
        resolved_settings = settings or get_settings()
        if resolved_settings.mcp_oauth_encryption_key is None:
            raise McpServerUnavailableError(config.alias, "oauth_storage_unavailable")
        try:
            storage = EncryptedMcpOAuthTokenStorage(
                persistence_backend=resolved_settings.persistence_backend,
                persistence_uri=resolved_settings.persistence_uri,
                encryption_key=resolved_settings.mcp_oauth_encryption_key.get_secret_value(),
                partition=McpOAuthPartition(
                    agent_identity=agent_identity,
                    runtime_snapshot=runtime_snapshot,
                    effective_scope=trusted_context or {},
                    server_url=config.url,
                ),
                workspace_path=resolved_settings.workspace_path,
            )
            oauth_auth = OAuthClientProvider(
                server_url=config.url,
                client_metadata=OAuthClientMetadata(
                    redirect_uris=[AnyUrl(resolved_settings.mcp_oauth_callback_url)]
                    if resolved_settings.mcp_oauth_callback_url
                    else None
                ),
                storage=storage,
                redirect_handler=oauth_flow.redirect_handler if oauth_flow else None,
                callback_handler=oauth_flow.callback_handler if oauth_flow else None,
            )
        except McpOAuthStorageError as exc:
            raise McpServerUnavailableError(config.alias, "oauth_storage_unavailable") from exc

    connection: StreamableHttpConnection = {"transport": "streamable_http", "url": config.url}
    if headers:
        connection["headers"] = headers
    if config.auth.type == "mcp_oauth":
        connection["auth"] = oauth_auth
    if outbound_auth is not None:
        if outbound_auth.headers:
            connection["headers"] = dict(outbound_auth.headers)
        if outbound_auth.auth is not None:
            connection["auth"] = outbound_auth.auth
    return MultiServerMCPClient(
        connections={config.alias: connection},
        callbacks=callbacks,
        tool_interceptors=tool_interceptors,
        tool_name_prefix=True,
    )


def create_mcp_oauth_flow(
    config: AgentMcpServer,
    *,
    agent_identity: str,
    runtime_snapshot: str,
    trusted_context: dict[str, str],
    settings: Settings,
) -> McpOAuthAuthorizationFlow:
    """Create a durable interactive OAuth flow for one trusted MCP binding."""
    if config.auth.type != "mcp_oauth" or settings.mcp_oauth_encryption_key is None:
        raise McpServerUnavailableError(config.alias, "oauth_storage_unavailable")
    if not settings.mcp_oauth_callback_url:
        raise McpServerUnavailableError(config.alias, "oauth_callback_unavailable")
    try:
        storage = EncryptedMcpOAuthTokenStorage(
            persistence_backend=settings.persistence_backend,
            persistence_uri=settings.persistence_uri,
            encryption_key=settings.mcp_oauth_encryption_key.get_secret_value(),
            partition=McpOAuthPartition(
                agent_identity=agent_identity,
                runtime_snapshot=runtime_snapshot,
                effective_scope=trusted_context,
                server_url=config.url,
            ),
            workspace_path=settings.workspace_path,
        )
        return McpOAuthAuthorizationFlow(storage)
    except McpOAuthStorageError as exc:
        raise McpServerUnavailableError(config.alias, "oauth_storage_unavailable") from exc


async def load_agent_mcp_tools(
    configs: Sequence[AgentMcpServer],
    *,
    callbacks: Callbacks | None = None,
    tool_interceptors: list[ToolCallInterceptor] | None = None,
    agent_identity: str = "",
    runtime_snapshot: str = "",
    trusted_context: dict[str, str] | None = None,
    deadline: datetime | None = None,
    auth_providers: OutboundAuthProviderRegistry | None = None,
    settings: Settings | None = None,
) -> list[Any]:
    """Discover agent MCP tools one server at a time with fail-closed semantics."""
    tools: list[Any] = []
    identities: set[tuple[str, str]] = set()
    registry = auth_providers or get_outbound_auth_provider_registry()
    for config in configs:
        try:
            outbound_auth = await _resolve_outbound_auth(
                config,
                registry=registry,
                agent_identity=agent_identity,
                runtime_snapshot=runtime_snapshot,
                trusted_context=trusted_context or {},
                deadline=deadline,
            )
            discovered = await create_agent_mcp_client(
                config,
                callbacks=callbacks,
                tool_interceptors=tool_interceptors,
                outbound_auth=outbound_auth,
                agent_identity=agent_identity,
                runtime_snapshot=runtime_snapshot,
                trusted_context=trusted_context,
                settings=settings,
            ).get_tools()
            for tool in discovered:
                provider_name = str(getattr(tool, "name", ""))
                identity = (config.alias, provider_name)
                if identity in identities:
                    raise McpServerUnavailableError(config.alias, "duplicate_tool_identity")
                identities.add(identity)
            tools.extend(discovered)
        except McpServerUnavailableError:
            if config.required:
                raise
            logger.warning("optional_mcp_server_unavailable", server=config.alias)
        except Exception as exc:
            if config.required:
                raise McpServerUnavailableError(config.alias, "discovery_failed") from exc
            logger.warning(
                "optional_mcp_server_unavailable",
                server=config.alias,
                error_type=type(exc).__name__,
            )
    return tools


async def _resolve_outbound_auth(
    config: AgentMcpServer,
    *,
    registry: OutboundAuthProviderRegistry,
    agent_identity: str,
    runtime_snapshot: str,
    trusted_context: dict[str, str],
    deadline: datetime | None,
) -> OutboundAuthResult | None:
    """Resolve and validate one provider result outside model-controlled data."""
    if config.auth.type != "outbound_auth_provider":
        return None
    assert config.auth.profile is not None
    try:
        registered = registry.resolve(config.auth.profile)
        result = await registered.provider.get_auth(
            OutboundAuthRequest(
                agent_identity=agent_identity,
                runtime_snapshot=runtime_snapshot,
                server_alias=config.alias,
                trusted_context=trusted_context,
                deadline=deadline,
            )
        )
    except OutboundAuthError as exc:
        raise McpServerUnavailableError(config.alias, exc.category) from exc
    except Exception as exc:
        raise McpServerUnavailableError(config.alias, "auth_provider_failed") from exc

    disallowed = {header.lower() for header in result.headers} - registered.allowed_headers
    if disallowed:
        raise McpServerUnavailableError(config.alias, "auth_header_not_allowlisted")
    return result


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
            message=message,
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
            data=str(params.data),
        )

    return Callbacks(
        on_progress=on_progress,
        on_logging_message=on_logging_message,
    )
