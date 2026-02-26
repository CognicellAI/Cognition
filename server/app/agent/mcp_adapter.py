"""LangChain adapter for MCP tools.

Converts MCP tools to LangChain BaseTool instances that can be used by DeepAgents.

Security note: MCP tools only support information access. Local execution is handled
by Cognition's built-in tools (shell, filesystem).
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, create_model

from server.app.agent.mcp_client import McpSseClient, McpToolInfo


class McpAdapterTool(BaseTool):
    """Adapter that wraps an MCP tool for use with LangChain/DeepAgents.

    This adapter allows MCP tools (which access remote information) to be used
    seamlessly alongside Cognition's built-in tools (which handle local execution).

    Example:
        client = McpSseClient(config)
        await client.connect()

        tools_info = await client.list_tools()
        for tool_info in tool_infos:
            tool = McpAdapterTool(client, tool_info)
            # tool is now a LangChain BaseTool
    """

    # Instance attributes (not Pydantic fields to avoid conflicts)
    _mcp_client: McpSseClient
    _tool_input_schema: dict[str, Any]

    def __init__(
        self,
        mcp_client: McpSseClient,
        tool_info: McpToolInfo,
    ):
        """Initialize the MCP tool adapter.

        Args:
            mcp_client: Connected MCP client
            tool_info: Tool information from the MCP server
        """
        # Create Pydantic model from JSON schema
        args_schema = self._create_args_schema(tool_info.input_schema)

        # Initialize base tool with required fields
        super().__init__(
            name=tool_info.name,
            description=tool_info.description or f"MCP tool: {tool_info.name}",
            args_schema=args_schema,
        )

        # Store MCP-specific attributes
        self._mcp_client = mcp_client
        self._tool_input_schema = tool_info.input_schema

    @staticmethod
    def _create_args_schema(input_schema: dict[str, Any]) -> type[BaseModel]:
        """Create a Pydantic model from JSON schema.

        Args:
            input_schema: JSON schema from MCP server

        Returns:
            Pydantic BaseModel class
        """
        properties = input_schema.get("properties", {})
        required = input_schema.get("required", [])

        fields = {}
        for prop_name, prop_schema in properties.items():
            field_type = McpAdapterTool._json_schema_to_python_type(prop_schema)
            field_info = Field(
                description=prop_schema.get("description", ""),
                default=... if prop_name in required else None,
            )
            fields[prop_name] = (field_type | None, field_info)

        # Create dynamic model
        return create_model("McpToolArgs", **fields)

    @staticmethod
    def _json_schema_to_python_type(prop_schema: dict[str, Any]) -> type:
        """Convert JSON schema type to Python type.

        Args:
            prop_schema: Property schema

        Returns:
            Python type
        """
        schema_type = prop_schema.get("type", "any")

        type_map = {
            "string": str,
            "integer": int,
            "number": float,
            "boolean": bool,
            "array": list,
            "object": dict,
            "any": Any,
        }

        return type_map.get(schema_type, Any)

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """Synchronous execution - not supported for MCP tools."""
        raise RuntimeError(
            "MCP tools only support async execution. Please use _arun() or await the tool directly."
        )

    async def _arun(self, **kwargs: Any) -> str:
        """Execute the MCP tool asynchronously.

        Args:
            **kwargs: Tool arguments

        Returns:
            Tool result as string
        """
        # Filter out None values
        arguments = {k: v for k, v in kwargs.items() if v is not None}

        try:
            result = await self._mcp_client.call_tool(self.name, arguments)

            # Format result as string for LangChain
            if result.get("isError"):
                error_msg = result.get("content", [{}])[0].get("text", "Unknown error")
                return f"Error: {error_msg}"

            # Extract text content
            texts = []
            for item in result.get("content", []):
                if item.get("type") == "text" and "text" in item:
                    texts.append(item["text"])

            return "\n".join(texts) if texts else json.dumps(result)

        except Exception as e:
            return f"Error calling MCP tool '{self.name}': {e}"


def create_mcp_tools(
    mcp_client: McpSseClient, tool_infos: list[McpToolInfo]
) -> list[McpAdapterTool]:
    """Create LangChain tools from MCP tool information.

    Args:
        mcp_client: Connected MCP client
        tool_infos: List of tool information from MCP server

    Returns:
        List of LangChain BaseTool instances
    """
    return [McpAdapterTool(mcp_client, info) for info in tool_infos]
