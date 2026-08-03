"""MCP (Model Context Protocol) adapter for Cognition.

Wraps langchain-mcp-adapters for Cognition's remote-only MCP server integration.
Replaces the previous custom McpSseClient / McpManager / McpAdapterTool layer.

Security stance:
- Remote MCP servers: Allowed (SSE transport, HTTP/HTTPS URLs only).
- Local (stdio) MCP servers: NOT supported for security reasons.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from datetime import datetime
from typing import Any

import structlog
from langchain_mcp_adapters.callbacks import CallbackContext, Callbacks
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import ToolCallInterceptor
from langchain_mcp_adapters.sessions import StreamableHttpConnection
from mcp.types import LoggingMessageNotificationParams

from server.app.agent.mcp_config import AgentMcpServer
from server.app.agent.outbound_auth import (
    OutboundAuthError,
    OutboundAuthProviderRegistry,
    OutboundAuthRequest,
    OutboundAuthResult,
    get_outbound_auth_provider_registry,
)

logger = structlog.get_logger(__name__)


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
) -> MultiServerMCPClient:
    """Create a client for exactly one validated Agent MCP server.

    Discovery is deliberately isolated per server. The caller applies required
    versus optional failure semantics without letting one unavailable optional
    service remove tools from healthy services.
    """
    headers: dict[str, str] | None = None
    if config.auth.type == "static_bearer":
        assert config.auth.env is not None
        token = os.environ.get(config.auth.env)
        if not token:
            raise McpServerUnavailableError(config.alias, "auth_unavailable")
        headers = {"Authorization": f"Bearer {token}"}
    elif config.auth.type == "mcp_oauth":
        # These modes require the v0.14 transport-security provider. Do not
        # downgrade a protected endpoint to anonymous transport while wiring is
        # incomplete.
        raise McpServerUnavailableError(config.alias, "auth_provider_unavailable")

    connection: StreamableHttpConnection = {"transport": "streamable_http", "url": config.url}
    if headers:
        connection["headers"] = headers
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
