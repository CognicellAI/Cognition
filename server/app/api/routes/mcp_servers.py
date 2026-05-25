"""Remote MCP server management API routes."""

from fastapi import APIRouter, Depends, HTTPException

from server.app.api.dependencies import get_config_store, get_scope_dep
from server.app.api.models import (
    McpServerCreate,
    McpServerList,
    McpServerResponse,
    McpServerUpdate,
)
from server.app.api.scoping import SessionScope
from server.app.storage.config_models import McpServerRegistration
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/mcp-servers", tags=["mcp-servers"])


def _to_response(server: McpServerRegistration) -> McpServerResponse:
    return McpServerResponse(
        name=server.name,
        url=server.url,
        headers={},
        enabled=server.enabled,
        transport=server.transport,
        scope=dict(server.scope),
        source=server.source,
    )


async def _get_existing(
    config_store: ConfigStore,
    name: str,
    scope: dict[str, str] | None,
) -> McpServerRegistration | None:
    servers = await config_store.list_mcp_servers(scope=scope)
    return next((server for server in servers if server.name == name), None)


@router.get("", response_model=McpServerList)
async def list_mcp_servers(
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> McpServerList:
    """List remote MCP servers visible in the current scope."""
    servers = await config_store.list_mcp_servers(scope=scope.get_all() or None)
    responses = [_to_response(server) for server in servers]
    return McpServerList(servers=responses, count=len(responses))


@router.post("", response_model=McpServerResponse, status_code=201)
async def register_mcp_server(
    body: McpServerCreate,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> McpServerResponse:
    """Register or replace a remote MCP server in the current scope.

    Cognition intentionally supports remote MCP servers only. Local stdio MCP
    servers are rejected by ``McpServerRegistration`` URL validation.
    """
    effective_scope = scope.get_all() or body.scope or {}
    existing = await _get_existing(config_store, body.name, effective_scope or None)
    if existing is not None and existing.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"MCP server '{body.name}' is file-managed and cannot be modified via API",
        )

    server = McpServerRegistration(
        name=body.name,
        url=body.url,
        headers=body.headers,
        enabled=body.enabled,
        scope=effective_scope,
        source="api",
        transport=body.transport,
    )
    await config_store.upsert_mcp_server(server)
    return _to_response(server)


@router.get("/{name}", response_model=McpServerResponse)
async def get_mcp_server(
    name: str,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> McpServerResponse:
    """Get a remote MCP server visible in the current scope."""
    server = await _get_existing(config_store, name, scope.get_all() or None)
    if server is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    return _to_response(server)


@router.patch("/{name}", response_model=McpServerResponse)
async def update_mcp_server(
    name: str,
    body: McpServerUpdate,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> McpServerResponse:
    """Partially update an API-registered remote MCP server."""
    effective_scope = scope.get_all() or None
    existing = await _get_existing(config_store, name, effective_scope)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
    if existing.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"MCP server '{name}' is file-managed and cannot be modified via API",
        )

    updates = body.model_dump(exclude_none=True)
    server = McpServerRegistration(
        name=existing.name,
        url=updates.get("url", existing.url),
        headers=updates.get("headers", existing.headers),
        enabled=updates.get("enabled", existing.enabled),
        scope=existing.scope,
        source=existing.source,
        transport=updates.get("transport", existing.transport),
    )
    await config_store.upsert_mcp_server(server)
    return _to_response(server)


@router.delete("/{name}", status_code=204)
async def delete_mcp_server(
    name: str,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> None:
    """Delete an API-registered remote MCP server from the current scope."""
    effective_scope = scope.get_all() or None
    existing = await _get_existing(config_store, name, effective_scope)
    if existing is not None and existing.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"MCP server '{name}' is file-managed and cannot be modified via API",
        )
    deleted = await config_store.delete_mcp_server(name, scope=effective_scope)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"MCP server '{name}' not found")
