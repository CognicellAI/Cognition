"""Tool management API routes."""

from typing import Any

from fastapi import APIRouter, HTTPException

from server.app.agent_registry import get_agent_registry
from server.app.api.models import ToolCreate, ToolList, ToolResponse

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=ToolList)
async def list_tools() -> ToolList:
    """List all registered tools."""
    try:
        registry = get_agent_registry()
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Agent registry unavailable: {e}",
        ) from e

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
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"Agent registry unavailable: {e}",
        ) from e

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


@router.post("", response_model=ToolResponse, status_code=201)
async def register_tool(body: ToolCreate) -> ToolResponse:
    """Register a tool in the ConfigRegistry.

    The tool is persisted in the DB and will be available across restarts.
    """
    try:
        from server.app.storage.config_models import ToolRegistration
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        tool = ToolRegistration(
            name=body.name,
            path=body.path,
            enabled=body.enabled,
            description=body.description,
            scope=body.scope,
            source="api",
        )
        await reg.upsert_tool(tool)

        return ToolResponse(
            name=tool.name,
            source="api",
            module=tool.path,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{name}", status_code=204)
async def unregister_tool(name: str) -> None:
    """Remove a tool from the ConfigRegistry."""
    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        deleted = await reg.delete_tool(name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Tool '{name}' not found in registry")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


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
