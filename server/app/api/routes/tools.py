"""Tool management API routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.agent_registry import get_agent_registry
from server.app.api.dependencies import get_config_store
from server.app.api.models import ToolCreate, ToolList, ToolResponse, ToolUpdate
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=ToolList)
async def list_tools(
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> ToolList:
    """List all registered tools from both AgentRegistry and ConfigStore.

    Returns tools from two sources:
    - File-discovered tools (``source_type="file"``) from AgentRegistry
    - API-registered tools (``source_type="api_code"`` or ``source_type="api_path"``)
      from ConfigStore
    """
    tool_responses: list[ToolResponse] = []

    # File-discovered tools from in-memory AgentRegistry
    try:
        registry = get_agent_registry()
        for t in registry.list_tools():
            tool_responses.append(
                ToolResponse(
                    name=t.name,
                    source_type="file",
                    source="file",
                    module=t.module,
                    description=None,
                    enabled=True,
                    interrupt_on=False,
                )
            )
    except RuntimeError:
        pass  # Registry not initialized — skip, not an error

    # API-registered tools from ConfigStore
    try:
        api_tools = await config_store.list_tools()
        known_names = {t.name for t in tool_responses}
        for ct in api_tools:
            if ct.name in known_names:
                continue  # file-discovered wins, don't duplicate
            source_type = "api_code" if ct.code else "api_path"
            tool_responses.append(
                ToolResponse(
                    name=ct.name,
                    source_type=source_type,
                    source=source_type,
                    module=ct.path,
                    description=ct.description,
                    enabled=ct.enabled,
                    interrupt_on=ct.interrupt_on,
                )
            )
    except RuntimeError:
        pass  # ConfigStore not initialized

    return ToolList(tools=tool_responses, count=len(tool_responses))


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
async def register_tool(
    body: ToolCreate,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> ToolResponse:
    """Register a tool in the ConfigStore.

    The tool is persisted in the DB and will be available to agents on the
    next invocation. Exactly one of ``path`` or ``code`` must be provided.

    **Security note:** Tool code executes with full Python privileges inside
    the sandbox backend. Restrict this endpoint to authorized administrators
    at the Gateway/proxy layer.
    """
    if not body.path and not body.code:
        raise HTTPException(
            status_code=422,
            detail="Either 'path' or 'code' must be provided.",
        )
    if body.path and body.code:
        raise HTTPException(
            status_code=422,
            detail="Provide either 'path' or 'code', not both.",
        )

    try:
        from server.app.storage.config_models import ToolRegistration

        tool = ToolRegistration(
            name=body.name,
            path=body.path,
            code=body.code,
            enabled=body.enabled,
            description=body.description,
            interrupt_on=body.interrupt_on,
            scope=body.scope,
            source="api",
        )
        await config_store.upsert_tool(tool)

        source_type = "api_code" if body.code else "api_path"
        return ToolResponse(
            name=tool.name,
            source_type=source_type,
            source=source_type,
            module=tool.path,
            description=tool.description,
            enabled=tool.enabled,
            interrupt_on=tool.interrupt_on,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{name}", status_code=204)
async def unregister_tool(
    name: str,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> None:
    """Remove a tool from the ConfigStore."""
    try:
        deleted = await config_store.delete_tool(name)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Tool '{name}' not found in registry")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/{name}", response_model=ToolResponse)
async def get_tool(
    name: str,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> ToolResponse:
    """Get a specific tool by name.

    Checks AgentRegistry (file-discovered) first, then ConfigStore (API-registered).
    """
    # Check AgentRegistry first
    try:
        registry = get_agent_registry()
        tool = registry.get_tool(name)
        if tool is not None:
            return ToolResponse(
                name=tool.name,
                source_type="file",
                source="file",
                module=tool.module,
                description=None,
                enabled=True,
                interrupt_on=False,
            )
    except RuntimeError:
        pass

    # Fall back to ConfigStore
    try:
        api_tool = await config_store.get_tool(name)
        if api_tool is not None:
            source_type = "api_code" if api_tool.code else "api_path"
            return ToolResponse(
                name=api_tool.name,
                source_type=source_type,
                source=source_type,
                module=api_tool.path,
                description=api_tool.description,
                enabled=api_tool.enabled,
                interrupt_on=api_tool.interrupt_on,
            )
    except RuntimeError:
        pass

    raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")


@router.patch("/{name}", response_model=ToolResponse)
async def update_tool(
    name: str,
    body: ToolUpdate,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> ToolResponse:
    """Partially update an API-registered tool in the ConfigStore."""
    try:
        from server.app.storage.config_models import ToolRegistration

        existing = await config_store.get_tool(name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")

        updates = body.model_dump(exclude_none=True)
        tool = ToolRegistration(
            name=existing.name,
            path=updates.get("path", existing.path),
            code=updates.get("code", existing.code),
            enabled=updates.get("enabled", existing.enabled),
            description=updates.get("description", existing.description),
            interrupt_on=updates.get("interrupt_on", existing.interrupt_on),
            scope=existing.scope,
            source=existing.source,
        )
        await config_store.upsert_tool(tool)

        source_type = "api_code" if tool.code else "api_path"
        return ToolResponse(
            name=tool.name,
            source_type=source_type,
            source=source_type,
            module=tool.path,
            description=tool.description,
            enabled=tool.enabled,
            interrupt_on=tool.interrupt_on,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
