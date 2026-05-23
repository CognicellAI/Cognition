"""MCP (Model Context Protocol) adapter for Cognition.

Wraps langchain-mcp-adapters for Cognition's remote-only MCP server integration.
Replaces the previous custom McpSseClient / McpManager / McpAdapterTool layer.

Security stance:
- Remote MCP servers: Allowed (SSE transport, HTTP/HTTPS URLs only).
- Local (stdio) MCP servers: NOT supported for security reasons.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

import structlog
from langchain_mcp_adapters.callbacks import CallbackContext, Callbacks
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_mcp_adapters.interceptors import MCPToolCallRequest, ToolCallInterceptor
from langchain_mcp_adapters.sessions import (
    SSEConnection,
    StreamableHttpConnection,
)
from mcp.types import LoggingMessageNotificationParams
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger(__name__)


class McpServerConfig(BaseModel):
    """Configuration for a remote MCP server connection."""

    name: str = Field(..., description="Unique name for this MCP server")
    url: str = Field(..., description="MCP server URL (must be HTTP/SSE endpoint)")
    headers: dict[str, str] = Field(
        default_factory=dict, description="Headers to include in requests"
    )
    enabled: bool = Field(default=True, description="Whether this server is enabled")
    transport: Literal["sse", "streamable_http"] = Field(
        default="sse", description="Transport protocol"
    )

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str, info: Any) -> str:
        if not v.startswith(("http://", "https://")):
            raise ValueError(
                f"MCP server '{info.data.get('name', 'unknown')}' has invalid URL: {v}. "
                "Only HTTP/HTTPS URLs are supported. "
                "Local (stdio) MCP servers are not supported for security reasons."
            )
        return v


class McpToolInfo(BaseModel):
    """Information about an MCP tool, including upstream annotations."""

    name: str
    description: str | None
    input_schema: dict[str, Any]
    output_schema: dict[str, Any] | None = None
    annotations: dict[str, Any] | None = None
    task_support: str | None = None


def mcp_config_to_connection(config: McpServerConfig) -> SSEConnection | StreamableHttpConnection:
    transport: Literal["sse", "streamable_http"] = config.transport
    if transport == "sse":
        return {"transport": "sse", "url": config.url, "headers": dict(config.headers)}
    return {"transport": "streamable_http", "url": config.url, "headers": dict(config.headers)}


def create_mcp_client(
    configs: Sequence[McpServerConfig],
    callbacks: Callbacks | None = None,
    tool_interceptors: list[ToolCallInterceptor] | None = None,
) -> MultiServerMCPClient:
    connections: dict[str, Any] = {
        c.name: mcp_config_to_connection(c) for c in configs if c.enabled
    }
    return MultiServerMCPClient(
        connections=connections or None,
        callbacks=callbacks,
        tool_interceptors=tool_interceptors,
    )


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


def _build_mcp_interceptors(
    scope: dict[str, str] | None = None,
) -> list[ToolCallInterceptor]:

    async def inject_scope_context(
        request: MCPToolCallRequest,
        handler: Any,
    ) -> Any:
        if scope:
            modified_args = {**request.args}
            for key, value in scope.items():
                if key not in modified_args:
                    modified_args[key] = value
            request = request.override(args=modified_args)
        return await handler(request)

    return [inject_scope_context]
