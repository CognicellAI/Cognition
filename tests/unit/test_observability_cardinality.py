"""Tests for low-cardinality observability behavior."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import Request, Response
from starlette.types import Receive, Scope, Send
from structlog.contextvars import get_contextvars

from server.app.agent.middleware import CognitionObservabilityMiddleware
from server.app.api.middleware import (
    ObservabilityMiddleware,
    route_template_for_request,
    status_class,
)
from server.app.models import RunStatus, SessionEvent, SessionRun
from server.app.observability import (
    bind_observability_context,
    clear_observability_context,
    observability_context,
    redact_event_fields,
    request_id_from_header,
    scope_key_names_from_headers,
)
from server.app.runtime_projection import RuntimeProjectionService


class _Metric:
    """Collect metric labels and observations for assertions."""

    def __init__(self) -> None:
        self.labels_seen: list[dict[str, str]] = []
        self.observed: list[float] = []
        self.incremented = 0

    def labels(self, **labels: str) -> _Metric:
        self.labels_seen.append(labels)
        return self

    def inc(self, amount: float = 1) -> None:
        self.incremented += int(amount)

    def observe(self, value: float) -> None:
        self.observed.append(value)


async def _asgi_app(scope: Scope, receive: Receive, send: Send) -> None:
    """No-op ASGI app for direct middleware dispatch tests."""


def _request(path: str, route_path: str | None = None) -> Request:
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
    }
    if route_path is not None:
        scope["route"] = SimpleNamespace(path=route_path)
    return Request(scope)


def test_route_template_for_request_uses_matched_route_not_concrete_path() -> None:
    request = _request(
        "/sessions/session-123/messages/message-456",
        "/sessions/{session_id}/messages/{message_id}",
    )

    assert route_template_for_request(request) == "/sessions/{session_id}/messages/{message_id}"


def test_route_template_for_request_uses_unmatched_without_raw_path() -> None:
    request = _request("/tenant/acme/private/not-found")

    assert route_template_for_request(request) == "unmatched"


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(200, "2xx"), (404, "4xx"), (503, "5xx")],
)
def test_status_class_is_bounded(status_code: int, expected: str) -> None:
    assert status_class(status_code) == expected


def test_request_id_from_header_accepts_only_bounded_safe_values() -> None:
    assert request_id_from_header("support-ticket-123") == "support-ticket-123"

    generated = request_id_from_header("bad request id with spaces and unicode 🚧")

    assert generated != "bad request id with spaces and unicode 🚧"
    assert len(generated) == 32


def test_scope_key_names_from_headers_redacts_scope_values() -> None:
    request = _request("/sessions")
    request.scope["headers"] = [
        (b"x-cognition-scope-tenant", b"acme"),
        (b"x-cognition-scope-project-id", b"secret-project"),
    ]

    scope_keys = scope_key_names_from_headers(request.headers)

    assert scope_keys == ["project_id", "tenant"]
    assert "acme" not in str(scope_keys)
    assert "secret-project" not in str(scope_keys)


def test_redact_event_fields_removes_raw_scope_and_secrets() -> None:
    event = redact_event_fields(
        None,
        "info",
        {
            "event": "unsafe",
            "scope": {"tenant": "acme"},
            "effective_scope": {"tenant": "acme"},
            "scope_key": "guessable-hash",
            "scope_keys": ["tenant"],
            "headers": {"Authorization": "Bearer secret-token"},
            "api_key": "sk-secret",
            "nested": {"password": "hunter2", "safe": "ok"},
        },
    )

    rendered = str(event)
    assert event["scope"] == "[REDACTED]"
    assert event["effective_scope"] == "[REDACTED]"
    assert event["scope_key"] == "[REDACTED]"
    assert event["scope_keys"] == ["tenant"]
    assert "acme" not in rendered
    assert "secret-token" not in rendered
    assert "sk-secret" not in rendered
    assert "hunter2" not in rendered


def test_observability_context_preserves_outer_request_context() -> None:
    clear_observability_context()
    try:
        bind_observability_context(request_id="request-1", scope_keys=["tenant"])

        with observability_context(
            run_id="run-1",
            effective_scope={"tenant": "acme"},
            api_key="sk-secret",
        ):
            inside = get_contextvars()
            assert inside["request_id"] == "request-1"
            assert inside["scope_keys"] == ["tenant"]
            assert inside["run_id"] == "run-1"
            assert inside["effective_scope"] == "[REDACTED]"
            assert inside["api_key"] == "[REDACTED]"
            assert "acme" not in str(inside)
            assert "sk-secret" not in str(inside)

        outside = get_contextvars()
        assert outside == {"request_id": "request-1", "scope_keys": ["tenant"]}
    finally:
        clear_observability_context()


@pytest.mark.asyncio
async def test_http_metrics_use_route_template_and_status_class(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    count = _Metric()
    duration = _Metric()
    monkeypatch.setattr("server.app.api.middleware.REQUEST_COUNT", count)
    monkeypatch.setattr("server.app.api.middleware.REQUEST_DURATION", duration)

    async def call_next(request: Request) -> Response:
        return Response(status_code=201)

    middleware = ObservabilityMiddleware(app=_asgi_app)
    request = _request("/sessions/session-123/messages", "/sessions/{session_id}/messages")

    response = await middleware.dispatch(request, call_next)

    assert response.status_code == 201
    assert response.headers["X-Request-ID"]
    assert duration.labels_seen == [
        {"method": "GET", "endpoint": "/sessions/{session_id}/messages"}
    ]
    assert count.labels_seen == [
        {"method": "GET", "endpoint": "/sessions/{session_id}/messages", "status": "2xx"}
    ]
    assert "session-123" not in str(count.labels_seen)


@pytest.mark.asyncio
async def test_http_middleware_binds_redacted_context_and_clears_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bound: list[dict[str, Any]] = []
    cleared = False

    def bind_context(**fields: Any) -> None:
        bound.append(fields)

    def clear_context() -> None:
        nonlocal cleared
        cleared = True

    monkeypatch.setattr("server.app.api.middleware.bind_observability_context", bind_context)
    monkeypatch.setattr("server.app.api.middleware.clear_observability_context", clear_context)
    monkeypatch.setattr("server.app.api.middleware.REQUEST_COUNT", _Metric())
    monkeypatch.setattr("server.app.api.middleware.REQUEST_DURATION", _Metric())

    async def call_next(request: Request) -> Response:
        return Response(status_code=200)

    middleware = ObservabilityMiddleware(app=_asgi_app)
    request = _request("/sessions/session-123/messages", "/sessions/{session_id}/messages")
    request.scope["headers"] = [
        (b"x-request-id", b"builder-request-1"),
        (b"x-cognition-scope-tenant", b"acme"),
        (b"x-cognition-scope-project", b"secret-project"),
    ]

    response = await middleware.dispatch(request, call_next)

    assert response.headers["X-Request-ID"] == "builder-request-1"
    assert bound == [{"request_id": "builder-request-1", "scope_keys": ["project", "tenant"]}]
    assert "acme" not in str(bound)
    assert "secret-project" not in str(bound)
    assert cleared is True


@pytest.mark.asyncio
async def test_agent_metrics_omit_provider_model_and_tool_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    llm_duration = _Metric()
    tool_calls = _Metric()
    monkeypatch.setattr("server.app.agent.middleware.LLM_CALL_DURATION", llm_duration)
    monkeypatch.setattr("server.app.agent.middleware.TOOL_CALL_COUNT", tool_calls)

    middleware = CognitionObservabilityMiddleware()

    async def model_handler(request: Any) -> str:
        return "ok"

    model_request = SimpleNamespace(
        model=SimpleNamespace(provider="tenant-provider", model_name="tenant-model")
    )
    assert await middleware.awrap_model_call(model_request, model_handler) == "ok"

    async def tool_handler(request: Any) -> str:
        return "done"

    tool_request = SimpleNamespace(tool_call={"name": "tenant-specific-tool"})
    assert await middleware.awrap_tool_call(tool_request, tool_handler) == "done"

    assert llm_duration.labels_seen == []
    assert len(llm_duration.observed) == 1
    assert tool_calls.labels_seen == [{"status": "success"}]
    assert "tenant-provider" not in str(llm_duration.labels_seen)
    assert "tenant-model" not in str(llm_duration.labels_seen)
    assert "tenant-specific-tool" not in str(tool_calls.labels_seen)


@pytest.mark.asyncio
async def test_runtime_projection_binds_safe_run_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[dict[str, Any]] = []

    @contextmanager
    def capture_context(**fields: Any) -> Iterator[None]:
        captured.append(fields)
        yield

    class Store:
        async def append_event(self, **kwargs: Any) -> SessionEvent:
            return SessionEvent(
                id=kwargs["event_id"],
                session_id=kwargs["session_id"],
                run_id=kwargs["run_id"],
                sequence=1,
                event_type=kwargs["event_type"],
                visibility=kwargs["visibility"],
                payload=kwargs["payload"] or {},
                effective_scope=kwargs["effective_scope"],
                created_at="2026-07-25T00:00:00Z",
                trace_id=kwargs.get("trace_id"),
                span_id=kwargs.get("span_id"),
                task_id=kwargs.get("task_id"),
            )

    monkeypatch.setattr("server.app.runtime_projection.observability_context", capture_context)

    run = SessionRun(
        id="run-1",
        session_id="session-1",
        thread_id="thread-1",
        status=RunStatus.ACTIVE,
        effective_scope={"tenant": "acme", "project": "secret-project"},
        attempt=1,
        created_at="2026-07-25T00:00:00Z",
        updated_at="2026-07-25T00:00:00Z",
        agent_revision=7,
        manifest_digest="sha256:manifest",
        task_id="task-1",
    )

    event = await RuntimeProjectionService(cast(Any, Store())).append_event(run, "run.progress")

    assert event.event_type == "run.progress"
    assert captured == [
        {
            "session_id": "session-1",
            "run_id": "run-1",
            "thread_id": "thread-1",
            "task_id": "task-1",
            "scope_keys": ["project", "tenant"],
            "agent_revision": 7,
            "manifest_digest": "sha256:manifest",
        }
    ]
    assert "acme" not in str(captured)
    assert "secret-project" not in str(captured)
