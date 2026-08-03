"""Interactive authorization endpoints for direct agent MCP OAuth."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import HTMLResponse

from server.app.agent.mcp_client import (
    McpServerUnavailableError,
    create_agent_mcp_client,
    create_mcp_oauth_flow,
)
from server.app.agent.mcp_config import agent_mcp_runtime_snapshot
from server.app.agent.mcp_oauth import McpOAuthStorageError, record_mcp_oauth_callback
from server.app.api.dependencies import get_config_store, get_scope_dep, get_settings_dep
from server.app.api.scoping import SessionScope
from server.app.settings import Settings
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/agents", tags=["mcp-oauth"])
logger = structlog.get_logger(__name__)
_authorization_tasks: set[asyncio.Task[None]] = set()


def _remember(task: asyncio.Task[None], *, alias: str) -> None:
    _authorization_tasks.add(task)

    def done(completed: asyncio.Task[None]) -> None:
        _authorization_tasks.discard(completed)
        try:
            completed.result()
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning("mcp_oauth_authorization_failed", server=alias, error_type=type(exc).__name__)

    task.add_done_callback(done)


@router.post("/{name}/mcp/{alias}/oauth/authorize", status_code=status.HTTP_202_ACCEPTED)
async def start_mcp_oauth_authorization(
    name: str,
    alias: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
) -> dict[str, str]:
    """Start one user-mediated OAuth flow for a trusted agent MCP binding.

    The returned URL is provider-issued and must be opened by the authorized
    builder/user.  It contains no Cognition scope or agent identifiers.
    """
    scope_data = scope.get_all()
    agent = await config_store.get_agent_definition(name, scope_data or None)
    if agent is None or agent.hidden:
        raise HTTPException(status_code=404, detail="Agent not found")
    binding = next((server for server in agent.mcp_servers if server.alias == alias), None)
    if binding is None or binding.auth.type != "mcp_oauth":
        raise HTTPException(status_code=404, detail="OAuth MCP binding not found")
    runtime_snapshot = agent_mcp_runtime_snapshot(agent)
    try:
        flow = create_mcp_oauth_flow(
            binding,
            agent_identity=agent.name,
            runtime_snapshot=runtime_snapshot,
            trusted_context=scope_data,
            settings=settings,
        )
        task = asyncio.create_task(
            _complete_authorization(binding, flow, agent.name, runtime_snapshot, scope_data, settings),
            name=f"mcp-oauth-{alias}",
        )
        _remember(task, alias=alias)
        authorization_url = await flow.wait_for_redirect()
    except McpServerUnavailableError as exc:
        raise HTTPException(status_code=503, detail=exc.category) from exc
    return {"authorization_url": authorization_url}


async def _complete_authorization(
    binding: Any,
    flow: Any,
    agent_identity: str,
    runtime_snapshot: str,
    scope: dict[str, str],
    settings: Settings,
) -> None:
    """Drive MCP discovery through its native OAuth client until token persistence."""
    await create_agent_mcp_client(
        binding,
        agent_identity=agent_identity,
        runtime_snapshot=runtime_snapshot,
        trusted_context=scope,
        settings=settings,
        oauth_flow=flow,
    ).get_tools()


@router.get("/mcp/oauth/callback", response_class=HTMLResponse, include_in_schema=False)
async def complete_mcp_oauth_authorization(
    code: str = Query(..., min_length=1),
    state: str = Query(..., min_length=1),
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
) -> HTMLResponse:
    """Accept a one-time provider callback without trusting browser-provided scope."""
    if settings.mcp_oauth_encryption_key is None:
        raise HTTPException(status_code=404, detail="OAuth callback unavailable")
    try:
        accepted = await record_mcp_oauth_callback(
            persistence_backend=settings.persistence_backend,
            persistence_uri=settings.persistence_uri,
            encryption_key=settings.mcp_oauth_encryption_key.get_secret_value(),
            workspace_path=settings.workspace_path,
            state=state,
            code=code,
        )
    except McpOAuthStorageError as exc:
        raise HTTPException(status_code=503, detail="oauth_storage_unavailable") from exc
    if not accepted:
        raise HTTPException(status_code=400, detail="invalid_or_expired_oauth_state")
    return HTMLResponse("<html><body>Authorization received. You may close this window.</body></html>")
