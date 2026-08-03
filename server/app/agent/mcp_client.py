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
from urllib.parse import urlsplit

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
    McpWorkloadTokenExchangeAuthConfig,
)
from server.app.settings import McpWorkloadTokenExchangeProfile, Settings

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
    ) -> McpServerConfig:
        workload_profile = None
        if isinstance(config.auth, McpWorkloadTokenExchangeAuthConfig):
            workload_profile = settings.get_mcp_auth_profile(config.auth.profile)
        return cls(
            name=alias,
            url=config.url,
            required=config.required,
            transport=config.transport,
            auth=config.auth,
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


def mcp_config_to_connection(config: McpServerConfig) -> StreamableHttpConnection:
    if not isinstance(config.auth, McpNoAuthConfig):
        raise McpTransportAuthenticationError(config.name)
    return {"transport": "streamable_http", "url": config.url}


def create_mcp_client(
    configs: Sequence[McpServerConfig],
    callbacks: Callbacks | None = None,
    tool_interceptors: list[ToolCallInterceptor] | None = None,
) -> MultiServerMCPClient:
    connections: dict[str, Any] = {c.name: mcp_config_to_connection(c) for c in configs}
    return MultiServerMCPClient(
        connections=connections or None,
        callbacks=callbacks,
        tool_interceptors=tool_interceptors,
        tool_name_prefix=True,
    )


async def load_mcp_tools_per_server(
    configs: Sequence[McpServerConfig],
    callbacks: Callbacks | None = None,
    tool_interceptors: list[ToolCallInterceptor] | None = None,
) -> list[BaseTool]:
    """Load MCP tools server-by-server and enforce canonical identity uniqueness."""

    tools: list[BaseTool] = []
    seen: set[str] = set()
    for config in configs:
        client = create_mcp_client(
            [config],
            callbacks=callbacks,
            tool_interceptors=tool_interceptors,
        )
        try:
            server_tools = await client.get_tools(server_name=config.name)
        except Exception as exc:
            logger.warning(
                "mcp_server_discovery_failed",
                server=config.name,
                required=config.required,
                error_type=type(exc).__name__,
            )
            if config.required:
                raise McpServerDiscoveryError(config.name) from exc
            continue

        for tool in server_tools:
            canonical_name = str(getattr(tool, "name", ""))
            if canonical_name in seen:
                raise McpServerDiscoveryError(config.name, "duplicate_tool_identity")
            seen.add(canonical_name)
            tools.append(tool)
    return tools


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
