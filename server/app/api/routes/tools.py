"""Tool management API routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.api.dependencies import get_config_store, get_scope_dep
from server.app.api.models import ToolCreate, ToolList, ToolResponse, ToolUpdate
from server.app.api.scoping import SessionScope
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/tools", tags=["tools"])


@router.get("", response_model=ToolList)
async def list_tools(
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> ToolList:
    """List all registered tools visible in the given scope."""
    tool_responses = []
    api_tools = await config_store.list_tools(scope=scope.get_all() or None)
    for ct in api_tools:
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
    return ToolList(tools=tool_responses, count=len(tool_responses))


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
        tool_data: dict[str, Any] = {
            "name": body.name,
            "path": body.path,
            "code": body.code,
            "enabled": body.enabled,
            "description": body.description,
            "interrupt_on": body.interrupt_on,
            "scope": body.scope,
            "source": "api",
        }
        await config_store.upsert_tool_from_dict(tool_data)

        source_type = "api_code" if body.code else "api_path"
        return ToolResponse(
            name=body.name,
            source_type=source_type,
            source=source_type,
            module=body.path,
            description=body.description,
            enabled=body.enabled,
            interrupt_on=body.interrupt_on,
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
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> ToolResponse:
    """Get a specific tool by name."""
    api_tool = await config_store.get_tool(name, scope=scope.get_all() or None)
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

    raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")


@router.patch("/{name}", response_model=ToolResponse)
async def update_tool(
    name: str,
    body: ToolUpdate,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> ToolResponse:
    """Partially update an API-registered tool in the ConfigStore."""
    try:
        existing = await config_store.get_tool(name)
        if existing is None:
            raise HTTPException(status_code=404, detail=f"Tool '{name}' not found")

        updates = body.model_dump(exclude_none=True)
        tool_data: dict[str, Any] = {
            "name": existing.name,
            "path": updates.get("path", existing.path),
            "code": updates.get("code", existing.code),
            "enabled": updates.get("enabled", existing.enabled),
            "description": updates.get("description", existing.description),
            "interrupt_on": updates.get("interrupt_on", existing.interrupt_on),
            "scope": existing.scope,
            "source": existing.source,
        }
        await config_store.upsert_tool_from_dict(tool_data)

        source_type = "api_code" if tool_data["code"] else "api_path"
        return ToolResponse(
            name=existing.name,
            source_type=source_type,
            source=source_type,
            module=tool_data["path"],
            description=tool_data["description"],
            enabled=tool_data["enabled"],
            interrupt_on=tool_data["interrupt_on"],
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
