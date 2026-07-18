"""A2A route mounting for Cognition's FastAPI application.

Uses catch-all dynamic dispatch so agents created at runtime are
immediately available via A2A without a server restart. Both the
card discovery and JSON-RPC endpoints resolve agents at request time
using scope-aware ConfigRegistry lookups.

Endpoints:
  GET  /.well-known/agent-card.json?assistant_id={name}  ->  agent card
  GET  /.well-known/agent-card.json                      ->  primary agent card
  GET  /a2a/{agent_name}/.well-known/agent-card.json      ->  agent card
  POST /a2a/{agent_name}                                 ->  JSON-RPC A2A methods
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from email.utils import format_datetime
from typing import TYPE_CHECKING, Any

import structlog
from a2a.server.agent_execution import RequestContext, SimpleRequestContextBuilder
from a2a.server.context import ServerCallContext
from a2a.server.events import Event
from a2a.server.request_handlers import DefaultRequestHandler
from a2a.server.request_handlers.request_handler import validate_request_params
from a2a.server.routes.jsonrpc_dispatcher import (
    DefaultServerCallContextBuilder,
    JsonRpcDispatcher,
    build_error_response,
)
from a2a.types import (
    CancelTaskRequest,
    SendMessageRequest,
    SubscribeToTaskRequest,
    Task,
    TaskState,
)
from a2a.utils.errors import (
    ContentTypeNotSupportedError,
    TaskNotCancelableError,
    TaskNotFoundError,
    UnsupportedOperationError,
)
from fastapi import FastAPI
from google.protobuf.json_format import (  # type: ignore[import-untyped]
    MessageToDict,
    ParseDict,
)
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from server.app.agent.task_runtime import (
    AgentTaskRuntime,
    CancelTask,
    GetTask,
    SubscribeTask,
)
from server.app.exceptions import (
    RuntimeTaskNotCancelableError,
    RuntimeTaskNotFoundError,
)
from server.app.models import TaskStatus
from server.app.protocols.a2a.card import build_agent_card_for_agent
from server.app.protocols.a2a.executor import CognitionA2AExecutor
from server.app.protocols.a2a.security import A2ACardSecurity, parse_a2a_card_security
from server.app.protocols.a2a.task_store import (
    CognitionTaskStore,
    effective_scope_from_context,
)

CURRENT_A2A_VERSION = "1.0"

if TYPE_CHECKING:
    from server.app.llm.deep_agent_service import SessionAgentManager
    from server.app.settings import Settings
    from server.app.storage.artifact_store import ArtifactStore
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


def _request_base_url(request: Request) -> str:
    """Return the externally visible request base URL without a trailing slash."""
    return str(request.base_url).rstrip("/")


def _missing_scope_response(
    request: Request,
    scope_keys: list[str],
    *,
    scoping_enabled: bool,
) -> JSONResponse | None:
    """Reject partial trusted scope before resolving an agent or runtime task."""
    if not scoping_enabled:
        return None
    scope = _extract_scope(dict(request.headers), scope_keys) or {}
    missing = [key for key in scope_keys if key not in scope]
    if not missing:
        return None
    return JSONResponse(
        {
            "error": "Missing required Cognition scope headers",
            "code": "PERMISSION_DENIED",
            "details": {"missing_scope_keys": missing},
        },
        status_code=403,
    )


class _ScopedCallContextBuilder(DefaultServerCallContextBuilder):
    """Attach Cognition's trusted effective scope to every SDK call context."""

    def __init__(self, scope_keys: list[str]) -> None:
        self._scope_keys = scope_keys

    def build(self, request: Request) -> ServerCallContext:
        context = super().build(request)
        context.state["effective_scope"] = (
            _extract_scope(dict(request.headers), self._scope_keys) or {}
        )
        return context


class _IdempotentRequestContextBuilder(SimpleRequestContextBuilder):
    """Generate stable server IDs from the A2A message idempotency key."""

    def __init__(
        self,
        task_store: CognitionTaskStore,
        agent_name: str,
        *,
        message_id_idempotency: bool,
    ) -> None:
        super().__init__(task_store=task_store)
        self._cognition_task_store = task_store
        self._agent_name = agent_name
        self._message_id_idempotency = message_id_idempotency

    def task_id_for(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> str | None:
        """Return the stable task ID for a new idempotent message."""
        if params.message.task_id:
            return params.message.task_id
        if not self._message_id_idempotency:
            return None
        if not params.message.message_id:
            return None
        scope_key = json.dumps(
            effective_scope_from_context(context),
            sort_keys=True,
            separators=(",", ":"),
        )
        return str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"cognition:a2a:{self._agent_name}:{scope_key}:{params.message.message_id}",
            )
        )

    async def build(
        self,
        context: ServerCallContext,
        params: SendMessageRequest | None = None,
        task_id: str | None = None,
        context_id: str | None = None,
        task: Task | None = None,
    ) -> RequestContext:
        """Resolve repeat submissions to their original task and context IDs."""
        if params is not None and task_id is None:
            task_id = self.task_id_for(params, context)
        if task_id is not None and context_id is None:
            existing = await self._cognition_task_store.get(task_id, context)
            if existing is not None:
                context_id = existing.context_id
            else:
                context_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"cognition:a2a-context:{task_id}"))
        return await super().build(
            context=context,
            params=params,
            task_id=task_id,
            context_id=context_id,
            task=task,
        )


class _ScopedRequestHandler(DefaultRequestHandler):
    """Use durable runtime operations for cancellation and resubscription."""

    def __init__(
        self,
        *,
        runtime: AgentTaskRuntime,
        cognition_task_store: CognitionTaskStore,
        agent_name: str,
        session_agent_manager: SessionAgentManager,
        request_context_builder: _IdempotentRequestContextBuilder,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            task_store=cognition_task_store,
            request_context_builder=request_context_builder,
            **kwargs,
        )
        self._runtime = runtime
        self._cognition_task_store = cognition_task_store
        self._agent_name = agent_name
        self._agent_manager = session_agent_manager
        self._idempotent_context_builder = request_context_builder

    @validate_request_params
    async def on_message_send(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Any:
        existing = await self._idempotent_task_response(params, context)
        return existing if existing is not None else await super().on_message_send(params, context)

    @validate_request_params
    async def on_message_send_stream(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        existing = await self._idempotent_task_response(params, context)
        if existing is not None:
            yield existing
            return
        async for event in super().on_message_send_stream(params, context):
            yield event

    @validate_request_params
    async def on_cancel_task(
        self,
        params: CancelTaskRequest,
        context: ServerCallContext,
    ) -> Any:
        scope = effective_scope_from_context(context)
        try:
            task = await self._runtime.cancel(
                CancelTask(params.id, self._agent_name, scope),
                abort_execution=self._agent_manager.abort_session,
            )
        except RuntimeTaskNotFoundError as exc:
            raise TaskNotFoundError from exc
        except RuntimeTaskNotCancelableError as exc:
            raise TaskNotCancelableError from exc
        return await self._cognition_task_store.project(task)

    @validate_request_params
    async def on_subscribe_to_task(
        self,
        params: SubscribeToTaskRequest,
        context: ServerCallContext,
    ) -> AsyncGenerator[Event, None]:
        scope = effective_scope_from_context(context)
        current = await self._cognition_task_store.get(params.id, context)
        if current is None:
            raise TaskNotFoundError
        if _is_terminal_task(current):
            raise UnsupportedOperationError
        yield current

        signature = _task_signature(current)
        try:
            async for _event in self._runtime.subscribe(
                SubscribeTask(params.id, self._agent_name, scope)
            ):
                projected = await self._cognition_task_store.get(params.id, context)
                if projected is None:
                    raise TaskNotFoundError
                new_signature = _task_signature(projected)
                if new_signature != signature:
                    signature = new_signature
                    yield projected
        except RuntimeTaskNotFoundError as exc:
            raise TaskNotFoundError from exc

    async def _idempotent_task_response(
        self,
        params: SendMessageRequest,
        context: ServerCallContext,
    ) -> Task | None:
        task_id = self._idempotent_context_builder.task_id_for(params, context)
        if task_id is None:
            return None
        task = await self._runtime.get(
            GetTask(
                task_id,
                self._agent_name,
                effective_scope_from_context(context),
            )
        )
        if task is None:
            return None
        if params.message.task_id:
            if TaskStatus.is_terminal(task.status):
                raise UnsupportedOperationError
            # A distinct message naming an input-required task is a
            # continuation, not a duplicate submission.
            return None
        if task.metadata.get("interaction_mode") == "message":
            return await self._cognition_task_store.project_message(task)
        return await self._cognition_task_store.project(task)


def _is_terminal_task(task: Task) -> bool:
    return task.status.state in {
        TaskState.TASK_STATE_COMPLETED,
        TaskState.TASK_STATE_FAILED,
        TaskState.TASK_STATE_CANCELED,
        TaskState.TASK_STATE_REJECTED,
    }


def _task_signature(task: Task) -> bytes:
    """Return a deterministic projection signature for subscription changes."""
    return bytes(task.SerializeToString(deterministic=True))


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
    headers.setdefault("a2a-version", CURRENT_A2A_VERSION)
    return headers


async def _request_for_sdk(request: Request, payload: dict[str, object]) -> Request:
    """Build a Starlette request without changing the public 1.0 wire shape."""
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    scope = dict(request.scope)
    raw_headers = [
        (key, value)
        for key, value in request.scope.get("headers", [])
        if key.lower() != b"content-length"
    ]
    raw_headers.append((b"content-length", str(len(body)).encode("ascii")))
    scope["headers"] = raw_headers

    sent = False

    async def receive() -> dict[str, object]:
        nonlocal sent
        if sent:
            return {"type": "http.request", "body": b"", "more_body": False}
        sent = True
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(scope, receive)


def _discard_unknown_proto_fields(payload: dict[str, object]) -> dict[str, object]:
    """Canonicalize known A2A params while ignoring forward-compatible fields."""
    method = payload.get("method")
    params = payload.get("params")
    if not isinstance(method, str) or not isinstance(params, dict):
        return payload
    model_class = JsonRpcDispatcher.METHOD_TO_MODEL.get(method)
    if model_class is None:
        return payload
    try:
        parsed = ParseDict(
            params,
            model_class(),
            ignore_unknown_fields=True,
        )
    except Exception:
        # Preserve invalid recognized values for the SDK's normal structured
        # InvalidParamsError handling.
        return payload
    return {
        **payload,
        "params": MessageToDict(parsed, preserving_proto_field_name=False),
    }


async def mount_a2a_routes(
    app: FastAPI,
    settings: Settings,
    config_store: ConfigStore,
    session_agent_manager: SessionAgentManager,
    store: StorageBackend,
    version: str,
    artifact_store: ArtifactStore | None = None,
    message_id_idempotency: bool = True,
    card_security: A2ACardSecurity | None = None,
) -> None:
    """Mount A2A protocol routes on the FastAPI app.

    Registers two routes:
    1. Scope-aware card discovery at /.well-known/agent-card.json
    2. Catch-all JSON-RPC at /a2a/{agent_name}

    Both resolve agents at request time. No restart needed when agents
    are created or have a2a_exposed toggled.
    """
    scope_keys = list(settings.scope_keys)
    scoping_enabled = bool(getattr(settings, "scoping_enabled", False))
    if card_security is None:
        card_security = parse_a2a_card_security(
            getattr(settings, "a2a_security_schemes", {}),
            getattr(settings, "a2a_security_requirements", []),
        )

    runtime = AgentTaskRuntime(
        store,
        default_workspace_path=str(settings.workspace_path),
        artifact_store=artifact_store,
    )
    handlers: dict[str, _ScopedRequestHandler] = {}
    cards_last_modified = format_datetime(datetime.now(UTC), usegmt=True)

    def get_handler(agent_name: str, card: Any) -> _ScopedRequestHandler:
        handler = handlers.get(agent_name)
        if handler is None:
            task_store = CognitionTaskStore(
                runtime,
                store,
                agent_name=agent_name,
                artifact_store=artifact_store,
            )
            executor = CognitionA2AExecutor(
                runtime=runtime,
                task_store=task_store,
                session_agent_manager=session_agent_manager,
                agent_name=agent_name,
                message_id_idempotency=message_id_idempotency,
            )
            context_builder = _IdempotentRequestContextBuilder(
                task_store,
                agent_name,
                message_id_idempotency=message_id_idempotency,
            )
            handler = _ScopedRequestHandler(
                runtime=runtime,
                cognition_task_store=task_store,
                agent_name=agent_name,
                session_agent_manager=session_agent_manager,
                request_context_builder=context_builder,
                agent_executor=executor,
                agent_card=card,
            )
            handlers[agent_name] = handler
        return handler

    def card_response(card: Any) -> JSONResponse:
        """Serialize a schema-valid Agent Card with cache metadata."""
        payload = MessageToDict(card, preserving_proto_field_name=False)
        etag = hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        return JSONResponse(
            payload,
            headers={
                # Agent definitions are scope-visible and may differ between
                # host-authorized application scopes. Never permit a shared
                # intermediary to reuse one scope's card for another caller.
                "cache-control": "private, max-age=60",
                "etag": f'"{etag}"',
                "last-modified": cards_last_modified,
            },
        )

    # --- Scope-aware card discovery route ---
    async def agent_card_handler(request: Request) -> JSONResponse:
        """GET /.well-known/agent-card.json[?assistant_id={name}]."""
        scope_error = _missing_scope_response(
            request,
            scope_keys,
            scoping_enabled=scoping_enabled,
        )
        if scope_error is not None:
            return scope_error
        assistant_id = request.query_params.get("assistant_id")
        scope = _extract_scope(dict(request.headers), scope_keys)

        agents = await config_store.list_agent_definitions(scope=scope or None)
        a2a_agents = sorted(
            [a for a in agents if a.a2a_exposed and a.mode != "subagent" and not a.hidden],
            key=lambda agent: agent.name,
        )

        # A2A defines this well-known URI as one Agent Card, not a catalog.
        # `assistant_id` remains available for Cognition's multi-agent routing;
        # without it, use the deterministic first visible agent.
        agent = (
            next((a for a in a2a_agents if a.name == assistant_id), None)
            if assistant_id
            else (a2a_agents[0] if a2a_agents else None)
        )
        if not agent:
            return JSONResponse(
                {"error": "Agent not found"},
                status_code=404,
            )

        card = build_agent_card_for_agent(
            agent,
            _request_base_url(request),
            version,
            security=card_security,
        )
        return card_response(card)

    async def per_agent_card_handler(request: Request) -> JSONResponse:
        """GET /a2a/{agent_name}/.well-known/agent-card.json."""
        scope_error = _missing_scope_response(
            request,
            scope_keys,
            scoping_enabled=scoping_enabled,
        )
        if scope_error is not None:
            return scope_error
        agent_name = request.path_params.get("agent_name", "")
        scope = _extract_scope(dict(request.headers), scope_keys)
        agent = await config_store.get_agent_definition(agent_name, scope=scope or None)
        if agent is None or not agent.a2a_exposed or agent.mode == "subagent" or agent.hidden:
            return JSONResponse(
                {"error": "Agent not found"},
                status_code=404,
            )

        card = build_agent_card_for_agent(
            agent,
            _request_base_url(request),
            version,
            security=card_security,
        )
        return card_response(card)

    # --- Catch-all JSON-RPC endpoint ---
    async def a2a_jsonrpc_handler(request: Request) -> Response:
        """POST /a2a/{agent_name} — dynamic dispatch to A2A JSON-RPC."""
        scope_error = _missing_scope_response(
            request,
            scope_keys,
            scoping_enabled=scoping_enabled,
        )
        if scope_error is not None:
            return scope_error
        agent_name = request.path_params.get("agent_name", "")
        scope = _extract_scope(dict(request.headers), scope_keys)

        # Look up agent at request time
        agent = await config_store.get_agent_definition(agent_name, scope=scope or None)
        if agent is None or not agent.a2a_exposed or agent.mode == "subagent" or agent.hidden:
            return JSONResponse(
                {"error": "Agent not found"},
                status_code=404,
            )

        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != (
            "application/json"
        ):
            request_id: str | int | None = None
            try:
                candidate = (await request.json()).get("id")
                if isinstance(candidate, str | int):
                    request_id = candidate
            except (AttributeError, json.JSONDecodeError):
                pass
            return JSONResponse(
                build_error_response(request_id, ContentTypeNotSupportedError()),
                status_code=200,
                headers={"a2a-version": CURRENT_A2A_VERSION},
            )

        try:
            payload = await request.json()
        except json.JSONDecodeError:
            return _jsonrpc_error_response(None, -32700, "Parse error")
        if not isinstance(payload, dict):
            return _jsonrpc_error_response(None, -32600, "Invalid request")

        card = build_agent_card_for_agent(
            agent,
            _request_base_url(request),
            version,
            security=card_security,
        )
        handler = get_handler(agent.name, card)
        dispatcher = JsonRpcDispatcher(
            request_handler=handler,
            context_builder=_ScopedCallContextBuilder(scope_keys),
            # Strict A2A 1.0 means the v0.3 compatibility adapter is disabled.
            enable_v0_3_compat=False,
        )
        sdk_request = await _request_for_sdk(
            request,
            _discard_unknown_proto_fields(payload),
        )
        sdk_response = await dispatcher.handle_requests(sdk_request)
        sdk_response.headers.setdefault("a2a-version", CURRENT_A2A_VERSION)
        return sdk_response

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
    # A2A clients are allowed to normalize an interface URL with a trailing
    # slash. Register both forms explicitly so POST bodies are never sent
    # through Starlette's 307 slash redirect (some conformance clients
    # deliberately do not follow redirects for protocol operations).
    app.routes.append(
        Route(
            path="/a2a/{agent_name}/",
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
