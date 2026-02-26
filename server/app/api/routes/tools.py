"""Tool management API routes."""

from typing import Any

from fastapi import APIRouter, HTTPException

from server.app.agent_registry import get_agent_registry
from server.app.api.models import ToolList, ToolResponse

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=ToolList)
async def list_tools() -> ToolList:
    """List all registered tools."""
    try:
        registry = get_agent_registry()
    except RuntimeError:
        return ToolList(tools=[], count=0)

    tools = registry.list_tools()
    return ToolList(
        tools=[
            ToolResponse(
                name=t.name,
                source=t.source,
                module=t.module,
            )
            for t in tools
        ],
        count=len(tools),
    )


@router.get("/errors")
async def get_tool_errors() -> list[dict[str, Any]]:
    """Get accumulated tool load errors.

    Returns a list of error records from the most recent tool discovery/reload.
    Errors are cleared when a file is successfully reloaded.
    """
    try:
        registry = get_agent_registry()
    except RuntimeError:
        return []

    errors = registry.get_load_errors()
    return [e.to_dict() for e in errors]


@router.post("/reload")
async def reload_tools() -> dict[str, Any]:
    """Trigger a reload of tools from the discovery path.

    Clears existing file-based tools and re-discovers them.
    Returns the count of tools loaded and any errors encountered.
    """
    try:
        registry = get_agent_registry()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail="Tool registry not initialized") from e

    result = registry.reload_tools()
    return result


@router.get("/{name}", response_model=ToolResponse)
async def get_tool(name: str) -> ToolResponse:
    """Get a specific tool by name."""
    try:
        registry = get_agent_registry()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail="Tool registry not initialized") from e

    tool = registry.get_tool(name)
    if tool is None:
        raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")

    return ToolResponse(
        name=tool.name,
        source=tool.source,
        module=tool.module,
    )
