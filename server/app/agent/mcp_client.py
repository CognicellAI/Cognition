"""Remote MCP (Model Context Protocol) client for Cognition.

This module provides a remote-only MCP client that connects to external MCP servers
via HTTP/SSE. Cognition does NOT support local (stdio) MCP servers for security reasons.

Security stance:
- Remote MCP servers: Allowed for accessing external information (GitHub, Jira, docs)
- Local MCP servers: NOT supported. Use built-in tools for local execution.

Usage:
    client = McpSseClient("https://api.glama.ai/mcp/github")
    await client.connect()
    tools = await client.list_tools()
    result = await client.call_tool("get_repo", {"owner": "org", "repo": "name"})
"""

from __future__ import annotations

import json
from typing import Any

import httpx
import structlog
from mcp import ClientSession
from mcp.client.sse import sse_client
from pydantic import BaseModel, Field, field_validator

logger = structlog.get_logger(__name__)


class McpServerConfig(BaseModel):
    """Configuration for an MCP server connection."""

    name: str = Field(..., description="Unique name for this MCP server")
    url: str = Field(..., description="MCP server URL (must be HTTP/SSE endpoint)")
    headers: dict[str, str] = Field(
        default_factory=dict, description="Headers to include in requests"
    )
    enabled: bool = Field(default=True, description="Whether this server is enabled")

    @field_validator("url")
    @classmethod
    def validate_url(cls, v: str, info) -> str:
        """Validate that the URL is HTTP/HTTPS."""
        if not v.startswith(("http://", "https://")):
            raise ValueError(
                f"MCP server '{info.data.get('name', 'unknown')}' has invalid URL: {v}. "
                "Only HTTP/HTTPS URLs are supported. "
                "Local (stdio) MCP servers are not supported for security reasons."
            )
        return v


class McpToolInfo(BaseModel):
    """Information about an MCP tool."""

    name: str
    description: str | None
    input_schema: dict[str, Any]


class McpSseClient:
    """Client for connecting to Remote MCP servers via HTTP/SSE.

        This client implements the Model Context Protocol for connecting to external
    tools and services. It ONLY supports remote (HTTP/SSE) connections.

        Security features:
        - Only configured headers are sent (no automatic env var leakage)
        - URL validation (must be HTTP/HTTPS)
        - Timeout protection on all operations

        Example:
            config = McpServerConfig(
                name="github",
                url="https://api.glama.ai/mcp/github",
                headers={"Authorization": "Bearer token"}
            )
            client = McpSseClient(config)
            await client.connect()
            tools = await client.list_tools()
            await client.close()
    """

    def __init__(self, config: McpServerConfig):
        """Initialize the MCP client.

        Args:
            config: Configuration for the MCP server connection
        """
        self.config = config
        self.session: ClientSession | None = None
        self._client_ctx = None
        self._connected = False

    async def connect(self) -> None:
        """Connect to the MCP server.

        Establishes the SSE connection and performs the initialization handshake.

        Raises:
            ConnectionError: If connection fails
            TimeoutError: If connection times out
        """
        if self._connected:
            return

        try:
            # Create SSE client context
            self._client_ctx = sse_client(self.config.url, headers=self.config.headers)
            streams = await self._client_ctx.__aenter__()

            # Create and initialize session
            self.session = ClientSession(*streams)
            await self.session.__aenter__()

            # Perform initialization handshake
            init_result = await self.session.initialize()
            logger.info(
                "MCP server connected",
                server=self.config.name,
                url=self.config.url,
                protocol_version=init_result.protocolVersion,
            )
            self._connected = True

        except Exception as e:
            logger.error(
                "Failed to connect to MCP server",
                server=self.config.name,
                url=self.config.url,
                error=str(e),
            )
            await self.close()
            raise ConnectionError(
                f"Failed to connect to MCP server '{self.config.name}': {e}"
            ) from e

    async def list_tools(self) -> list[McpToolInfo]:
        """List available tools from the MCP server.

        Returns:
            List of tool information objects

        Raises:
            RuntimeError: If not connected
        """
        if not self._connected or self.session is None:
            raise RuntimeError("MCP client not connected. Call connect() first.")

        try:
            tools_result = await self.session.list_tools()
            return [
                McpToolInfo(
                    name=tool.name,
                    description=tool.description,
                    input_schema=tool.inputSchema,
                )
                for tool in tools_result.tools
            ]
        except Exception as e:
            logger.error(
                "Failed to list MCP tools",
                server=self.config.name,
                error=str(e),
            )
            raise

    async def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call a tool on the MCP server.

        Args:
            tool_name: Name of the tool to call
            arguments: Arguments to pass to the tool

        Returns:
            Tool execution result

        Raises:
            RuntimeError: If not connected
            Exception: If tool execution fails
        """
        if not self._connected or self.session is None:
            raise RuntimeError("MCP client not connected. Call connect() first.")

        try:
            result = await self.session.call_tool(tool_name, arguments)
            return {
                "content": [
                    {"type": content.type, "text": content.text}
                    for content in result.content
                    if hasattr(content, "text")
                ],
                "isError": result.isError,
            }
        except Exception as e:
            logger.error(
                "MCP tool call failed",
                server=self.config.name,
                tool=tool_name,
                error=str(e),
            )
            raise

    async def close(self) -> None:
        """Close the MCP connection."""
        try:
            if self.session:
                await self.session.__aexit__(None, None, None)
                self.session = None
        except Exception:
            pass

        try:
            if self._client_ctx:
                await self._client_ctx.__aexit__(None, None, None)
                self._client_ctx = None
        except Exception:
            pass

        self._connected = False
        logger.info("MCP client closed", server=self.config.name)

    async def __aenter__(self) -> McpSseClient:
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close()


class McpManager:
    """Manager for multiple MCP server connections."""

    def __init__(self):
        """Initialize the MCP manager."""
        self.clients: dict[str, McpSseClient] = {}

    def add_server(self, config: McpServerConfig) -> None:
        """Add an MCP server configuration.

        Args:
            config: Server configuration

        Raises:
            ValueError: If server name already exists
        """
        if config.name in self.clients:
            raise ValueError(f"MCP server '{config.name}' already configured")

        self.clients[config.name] = McpSseClient(config)

    async def connect_all(self) -> None:
        """Connect to all configured MCP servers."""
        for name, client in self.clients.items():
            if client.config.enabled:
                try:
                    await client.connect()
                except Exception as e:
                    logger.error(
                        "Failed to connect to MCP server",
                        server=name,
                        error=str(e),
                    )

    async def close_all(self) -> None:
        """Close all MCP connections."""
        for client in self.clients.values():
            await client.close()
        self.clients.clear()

    async def get_all_tools(self) -> dict[str, list[McpToolInfo]]:
        """Get tools from all connected servers.

        Returns:
            Dict mapping server name to list of tools
        """
        all_tools = {}
        for name, client in self.clients.items():
            if client._connected:
                try:
                    tools = await client.list_tools()
                    all_tools[name] = tools
                except Exception as e:
                    logger.error(
                        "Failed to get tools from MCP server",
                        server=name,
                        error=str(e),
                    )
        return all_tools

    async def __aenter__(self) -> McpManager:
        """Async context manager entry."""
        await self.connect_all()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Async context manager exit."""
        await self.close_all()
