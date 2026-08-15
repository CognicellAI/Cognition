"""Builder-facing MCP OAuth authorization handoff API."""

from __future__ import annotations

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from server.app.agent.definition import McpOAuthConfig
from server.app.agent.mcp_oauth_flow import (
    McpOAuthFlowCoordinator,
    McpOAuthFlowError,
    McpOAuthFlowStatus,
    McpOAuthFlowView,
)
from server.app.api.dependencies import (
    get_config_store,
    get_mcp_oauth_flow_coordinator,
    get_scope_dep,
)
from server.app.api.scoping import SessionScope
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/mcp/oauth", tags=["mcp-oauth"])


class McpOAuthAuthorizationResponse(BaseModel):
    """Credential-free OAuth transaction response."""

    model_config = ConfigDict(extra="forbid")

    flow_id: str | None = None
    status: McpOAuthFlowStatus
    authorization_url: str | None = None
    expires_in_seconds: int | None = Field(default=None, ge=0)
    failure_category: str | None = None


@router.post(
    "/agents/{agent_name}/servers/{server_alias}/authorizations",
    response_model=McpOAuthAuthorizationResponse,
)
async def begin_mcp_oauth_authorization(
    agent_name: str,
    server_alias: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    coordinator: McpOAuthFlowCoordinator = Depends(  # noqa: B008
        get_mcp_oauth_flow_coordinator
    ),
) -> McpOAuthAuthorizationResponse:
    """Begin standard MCP OAuth for one resolved Agent server partition."""
    effective_scope = scope.get_all()
    agent = await config_store.get_agent_definition(agent_name, effective_scope or None)
    if agent is None or agent.hidden:
        raise HTTPException(status_code=404, detail="Agent not found")
    server = agent.mcp.servers.get(server_alias)
    if server is None or not isinstance(server.auth, McpOAuthConfig):
        raise HTTPException(status_code=404, detail="MCP OAuth server not found")
    try:
        view = await coordinator.begin(
            agent_name=agent.name,
            server_alias=server_alias,
            server_url=str(httpx.URL(server.url)),
            effective_scope=effective_scope,
        )
    except McpOAuthFlowError as exc:
        status_code = 503 if exc.category.startswith("oauth_configuration") else 409
        raise HTTPException(status_code=status_code, detail=exc.category) from exc
    return _response(view)


@router.post("/callback", response_model=McpOAuthAuthorizationResponse)
async def complete_mcp_oauth_authorization(
    request: Request,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    coordinator: McpOAuthFlowCoordinator = Depends(  # noqa: B008
        get_mcp_oauth_flow_coordinator
    ),
) -> McpOAuthAuthorizationResponse:
    """Accept a builder-relayed OAuth code without placing it in a request URL."""
    try:
        body = await request.json()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="authorization_callback_invalid") from exc
    if not isinstance(body, dict) or set(body) != {"code", "state"}:
        raise HTTPException(status_code=400, detail="authorization_callback_invalid")
    code = body.get("code")
    state = body.get("state")
    if (
        not isinstance(code, str)
        or not 0 < len(code) <= 8192
        or not isinstance(state, str)
        or not 0 < len(state) <= 512
    ):
        raise HTTPException(status_code=400, detail="authorization_callback_invalid")
    try:
        view = await coordinator.complete(
            code=code,
            state=state,
            effective_scope=scope.get_all(),
        )
    except McpOAuthFlowError as exc:
        raise HTTPException(status_code=400, detail=exc.category) from exc
    return _response(view)


@router.get(
    "/authorizations/{flow_id}",
    response_model=McpOAuthAuthorizationResponse,
)
async def get_mcp_oauth_authorization(
    flow_id: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    coordinator: McpOAuthFlowCoordinator = Depends(  # noqa: B008
        get_mcp_oauth_flow_coordinator
    ),
) -> McpOAuthAuthorizationResponse:
    """Observe a transaction only inside its exact trusted scope."""
    try:
        view = coordinator.get(flow_id=flow_id, effective_scope=scope.get_all())
    except McpOAuthFlowError as exc:
        raise HTTPException(status_code=404, detail=exc.category) from exc
    return _response(view)


def _response(view: McpOAuthFlowView) -> McpOAuthAuthorizationResponse:
    return McpOAuthAuthorizationResponse(
        flow_id=view.flow_id,
        status=view.status,
        authorization_url=view.authorization_url,
        expires_in_seconds=view.expires_in_seconds,
        failure_category=view.failure_category,
    )


__all__ = ["router"]
