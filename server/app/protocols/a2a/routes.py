"""A2A route mounting for Cognition's FastAPI application.

Uses catch-all dynamic dispatch so agents created at runtime are
immediately available via A2A without a server restart. Both the
card discovery and JSON-RPC endpoints resolve agents at request time
using scope-aware ConfigRegistry lookups.

Endpoints:
  GET  /.well-known/agent-card.json?assistant_id={name}  ->  agent card
  GET  /.well-known/agent-card.json                      ->  available agents list
  GET  /a2a/{agent_name}/.well-known/agent-card.json      ->  agent card
  POST /a2a/{agent_name}                                 ->  JSON-RPC A2A methods
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any

import structlog
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import InMemoryTaskStore
from fastapi import FastAPI
from google.protobuf.json_format import MessageToDict  # type: ignore[import-untyped]
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from server.app.protocols.a2a.card import build_agent_card_for_agent
from server.app.protocols.a2a.executor import CognitionA2AExecutor
from server.app.protocols.a2a.wire import (
    CURRENT_A2A_VERSION,
    is_public_a2a_method,
    normalize_request_for_sdk,
    normalize_response_to_public,
    normalize_stream_item_to_public,
)

if TYPE_CHECKING:
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.settings import Settings
    from server.app.storage.backend import StorageBackend
    from server.app.storage.config_store import ConfigStore

logger = structlog.get_logger(__name__)


_JSONRPC_METHOD_NOT_FOUND = -32601


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


def _request_base_url(request: Request) -> str:
    """Return the externally visible request base URL without a trailing slash."""
    return str(request.base_url).rstrip("/")


def _jsonrpc_error_response(
    request_id: str | int | None,
    code: int,
    message: str,
) -> JSONResponse:
    """Return a JSON-RPC error response with HTTP 200 transport semantics."""
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {
                "code": code,
                "message": message,
            },
        },
        status_code=200,
    )


def _response_headers(response: Response) -> dict[str, str]:
    """Copy response headers that remain valid after body transformation."""
    headers = dict(response.headers)
    headers.pop("content-length", None)
    return headers


async def _request_for_sdk(request: Request, payload: dict[str, object]) -> Request:
    """Build a Starlette request for the SDK dispatcher with normalized JSON."""
    body = json.dumps(normalize_request_for_sdk(payload)).encode("utf-8")
    scope = dict(request.scope)
    raw_headers = [
        (key, value)
        for key, value in request.scope.get("headers", [])
        if key.lower() not in {b"a2a-version", b"content-length"}
    ]
    raw_headers.extend(
        [
            (b"a2a-version", CURRENT_A2A_VERSION.encode("ascii")),
            (b"content-length", str(len(body)).encode("ascii")),
        ]
    )
    scope["headers"] = raw_headers

    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


async def _normalize_dispatcher_response(response: Response) -> Response:
    """Normalize SDK response JSON/SSE data to Cognition's public A2A shape."""
    if isinstance(response, EventSourceResponse):
        stream = response.body_iterator

        async def event_generator() -> AsyncGenerator[Any, None]:
            async for item in stream:
                if isinstance(item, dict):
                    data = item.get("data")
                    if isinstance(data, str):
                        try:
                            parsed = json.loads(data)
                        except json.JSONDecodeError:
                            yield item
                            continue
                        next_item = dict(item)
                        next_item["data"] = json.dumps(
                            normalize_stream_item_to_public(parsed)
                        )
                        yield next_item
                    else:
                        yield normalize_stream_item_to_public(item)
                else:
                    yield item

        return EventSourceResponse(
            event_generator(),
            status_code=response.status_code,
            headers=_response_headers(response),
        )

    body = getattr(response, "body", b"")
    if not body:
        return response
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return response
    if not isinstance(payload, dict):
        return response
    return JSONResponse(
        normalize_response_to_public(payload),
        status_code=response.status_code,
        headers=_response_headers(response),
    )


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

        card = build_agent_card_for_agent(agent, _request_base_url(request), version)
        return JSONResponse(MessageToDict(card))

    async def per_agent_card_handler(request: Request) -> JSONResponse:
        """GET /a2a/{agent_name}/.well-known/agent-card.json."""
        agent_name = request.path_params.get("agent_name", "")
        scope = _extract_scope(dict(request.headers), scope_keys)
        agent = await config_store.get_agent_definition(agent_name, scope=scope or None)
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

        card = build_agent_card_for_agent(agent, _request_base_url(request), version)
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

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return _jsonrpc_error_response(None, -32700, "Parse error")
        if not isinstance(payload, dict):
            return _jsonrpc_error_response(None, -32600, "Invalid request")

        request_id = payload.get("id")
        if not isinstance(request_id, str | int | None):
            request_id = None
        method = payload.get("method")
        if not is_public_a2a_method(method if isinstance(method, str) else None):
            return _jsonrpc_error_response(
                request_id,
                _JSONRPC_METHOD_NOT_FOUND,
                "Method not found",
            )

        # Build executor and handler per-request
        executor = CognitionA2AExecutor(
            settings=settings,
            session_agent_manager=session_agent_manager,
            store=store,
            agent_name=agent.name,
        )
        card = build_agent_card_for_agent(agent, _request_base_url(request), version)
        handler = DefaultRequestHandler(
            agent_executor=executor,
            task_store=InMemoryTaskStore(),
            agent_card=card,
        )
        dispatcher = JsonRpcDispatcher(request_handler=handler)
        sdk_request = await _request_for_sdk(request, payload)
        sdk_response = await dispatcher.handle_requests(sdk_request)
        return await _normalize_dispatcher_response(sdk_response)

    app.routes.append(
        Route(
            path="/.well-known/agent-card.json",
            endpoint=agent_card_handler,
            methods=["GET"],
        )
    )
    app.routes.append(
        Route(
            path="/a2a/{agent_name}/.well-known/agent-card.json",
            endpoint=per_agent_card_handler,
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
        per_agent_card_endpoint="/a2a/{agent_name}/.well-known/agent-card.json",
        rpc_endpoint="/a2a/{agent_name}",
        mode="dynamic-dispatch",
    )
