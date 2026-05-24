"""A2A route mounting for Cognition's FastAPI application.

Uses catch-all dynamic dispatch so agents created at runtime are
immediately available via A2A without a server restart. Both the
card discovery and JSON-RPC endpoints resolve agents at request time
using scope-aware ConfigRegistry lookups.

Endpoints:
  GET  /.well-known/agent-card.json?assistant_id={name}  ->  agent card
  GET  /.well-known/agent-card.json                      ->  available agents list
  POST /a2a/{agent_name}                                 ->  JSON-RPC A2A methods
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from server.app.protocols.a2a.card import build_agent_card_for_agent
from server.app.protocols.a2a.executor import CognitionA2AExecutor

if TYPE_CHECKING:
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.settings import Settings
    from server.app.storage.backend import StorageBackend
    from server.app.storage.config_store import ConfigStore

logger = structlog.get_logger(__name__)


def _extract_scope(
    headers: dict[str, str],
    scope_keys: list[str],
) -> dict[str, str] | None:
    """Extract scope from X-Cognition-Scope-* headers."""
    if not scope_keys:
        return None
    scope: dict[str, str] = {}
    for key in scope_keys:
        header_name = f"x-cognition-scope-{key.replace('_', '-')}"
        val = headers.get(header_name)
        if val:
            scope[key] = val
    return scope or None


async def mount_a2a_routes(
    app: FastAPI,
    settings: Settings,
    config_store: ConfigStore,
    session_agent_manager: SessionAgentManager,
    store: StorageBackend,
    version: str,
) -> None:
    """Mount A2A protocol routes on the FastAPI app.

    Registers two routes:
    1. Scope-aware card discovery at /.well-known/agent-card.json
    2. Catch-all JSON-RPC at /a2a/{agent_name}

    Both resolve agents at request time. No restart needed when agents
    are created or have a2a_exposed toggled.
    """
    base_url = f"http://{settings.host}:{settings.port}"
    scope_keys = list(settings.scope_keys)

    # --- Scope-aware card discovery route ---
    async def agent_card_handler(request: Request) -> JSONResponse:
        """GET /.well-known/agent-card.json?assistant_id={name}"""
        assistant_id = request.query_params.get("assistant_id")
        scope = _extract_scope(dict(request.headers), scope_keys)

        agents = await config_store.list_agent_definitions(scope=scope or None)
        a2a_agents = [
            a for a in agents
            if a.a2a_exposed and a.mode != "subagent" and not a.hidden
        ]

        if not assistant_id:
            return JSONResponse({
                "agents": [
                    {"name": a.name, "description": a.description}
                    for a in a2a_agents
                ]
            })

        agent = next((a for a in a2a_agents if a.name == assistant_id), None)
        if not agent:
            return JSONResponse(
                {"error": "Agent not found"},
                status_code=404,
            )

        card = build_agent_card_for_agent(agent, base_url, version)
        return JSONResponse(MessageToDict(card))

    # --- Catch-all JSON-RPC endpoint ---
    async def a2a_jsonrpc_handler(request: Request) -> Response:
        """POST /a2a/{agent_name} — dynamic dispatch to A2A JSON-RPC."""
        agent_name = request.path_params.get("agent_name", "")
        scope = _extract_scope(dict(request.headers), scope_keys)

        # Look up agent at request time
        agent = await config_store.get_agent_definition(
            agent_name, scope=scope or None
        )
        if (
            agent is None
            or not agent.a2a_exposed
            or agent.mode == "subagent"
            or agent.hidden
        ):
            return JSONResponse(
                {"error": "Agent not found"},
                status_code=404,
            )

        # Build executor and handler per-request
        executor = CognitionA2AExecutor(
            settings=settings,
            session_agent_manager=session_agent_manager,
            store=store,
            agent_name=agent.name,
        )
        card = build_agent_card_for_agent(agent, base_url, version)
        handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        dispatcher = JsonRpcDispatcher(request_handler=handler)
        return await dispatcher.handle_requests(request)

    app.routes.append(
        Route(
            path="/.well-known/agent-card.json",
            endpoint=agent_card_handler,
            methods=["GET"],
        )
    )
    app.routes.append(
        Route(
            path="/a2a/{agent_name}",
            endpoint=a2a_jsonrpc_handler,
            methods=["POST"],
        )
    )

    logger.info(
        "A2A adapter mounted",
        card_endpoint="/.well-known/agent-card.json",
        rpc_endpoint="/a2a/{agent_name}",
        mode="dynamic-dispatch",
    )
