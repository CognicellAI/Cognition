from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import pytest
from pydantic import BaseModel

from server.app.agent.cognition_agent import CognitionContext
from server.app.agent.middleware import (
    ToolArgumentValidationMiddleware,
    ToolSecurityMiddleware,
    ToolVisibilityMiddleware,
    TrustedRuntimeContextMiddleware,
    _audit_tool_safety,
    _tool_safety_attributes,
)


class _ToolInput(BaseModel):
    command: str
    session_id: str | None = None
    effective_scope: dict[str, str] | None = None


class _Tool:
    def get_input_schema(self) -> type[BaseModel]:
        return _ToolInput


@dataclass(frozen=True)
class _Runtime:
    context: CognitionContext
    config: dict[str, Any]


@dataclass(frozen=True)
class _Request:
    tool_call: dict[str, Any]
    tool: Any
    runtime: _Runtime

    def override(self, **overrides: Any) -> _Request:
        return replace(self, **overrides)


@dataclass(frozen=True)
class _ModelRequest:
    tools: list[Any]

    def override(self, **overrides: Any) -> _ModelRequest:
        return replace(self, **overrides)


@dataclass(frozen=True)
class _NamedTool:
    name: str


def _plain_tool() -> str:
    return "ok"


def test_cognition_context_carries_trusted_runtime_fields() -> None:
    context = CognitionContext.from_scope(
        {"tenant": "acme", "project": "ios"},
        session_id="session-1",
        thread_id="thread-1",
        agent_name="safe-builder",
        metadata={"assignment_id": "assign-1"},
    )

    assert context.effective_scope == {"tenant": "acme", "project": "ios"}
    assert context.session_id == "session-1"
    assert context.thread_id == "thread-1"
    assert context.agent_name == "safe-builder"
    assert context.metadata == {"assignment_id": "assign-1"}


def test_tool_safety_trace_attributes_are_redacted() -> None:
    attributes = _tool_safety_attributes(
        action="context_injected",
        tool_name="update_assignment",
        tool_call_id="call-1",
        safe_context={
            "session_id": "session-1",
            "run_id": "run-1",
            "scope_keys": ["project", "tenant"],
        },
        fields=["session_id"],
        overwritten_fields=["session_id"],
    )

    assert attributes["cognition.tool_safety.action"] == "context_injected"
    assert attributes["tool.name"] == "update_assignment"
    assert attributes["cognition.session_id"] == "session-1"
    assert attributes["cognition.run_id"] == "run-1"
    assert attributes["cognition.scope_keys"] == "project,tenant"
    assert "acme" not in str(attributes)


def test_tool_safety_audit_increments_metric(monkeypatch: pytest.MonkeyPatch) -> None:
    labels_seen: list[dict[str, str]] = []
    incremented = False

    class _Metric:
        def labels(self, **labels: str) -> _Metric:
            labels_seen.append(labels)
            return self

        def inc(self) -> None:
            nonlocal incremented
            incremented = True

    monkeypatch.setattr("server.app.agent.middleware.TOOL_SAFETY_EVENT_COUNT", _Metric())

    _audit_tool_safety(
        action="blocked",
        tool_name="execute",
        tool_call_id="call-1",
        safe_context={"session_id": "session-1", "run_id": "run-1", "scope_keys": ["tenant"]},
        message="blocked",
    )

    assert labels_seen == [{"action": "blocked"}]
    assert incremented is True


@pytest.mark.asyncio
async def test_trusted_context_overwrites_model_supplied_reserved_args() -> None:
    middleware = TrustedRuntimeContextMiddleware()
    request = _Request(
        tool_call={
            "name": "run_assignment",
            "id": "call-1",
            "args": {
                "command": "pytest",
                "session_id": "model-spoofed-session",
            },
        },
        tool=_Tool(),
        runtime=_Runtime(
            context=CognitionContext.from_scope(
                {"tenant": "acme"},
                session_id="trusted-session",
                thread_id="trusted-thread",
            ),
            config={"run_id": "run-1", "configurable": {"thread_id": "fallback-thread"}},
        ),
    )

    seen_request: _Request | None = None

    async def handler(updated: _Request) -> str:
        nonlocal seen_request
        seen_request = updated
        return "ok"

    result = await middleware.awrap_tool_call(request, handler)

    assert result == "ok"
    assert seen_request is not None
    assert seen_request.tool_call["args"]["session_id"] == "trusted-session"
    assert seen_request.tool_call["args"]["effective_scope"] == {"tenant": "acme"}
    assert seen_request.tool_call["args"]["command"] == "pytest"


@pytest.mark.asyncio
async def test_trusted_context_event_uses_redacted_scope_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_dispatch(event_name: str, data: dict[str, Any]) -> None:
        events.append((event_name, data))

    monkeypatch.setattr(
        "server.app.agent.middleware.adispatch_custom_event",
        fake_dispatch,
    )
    middleware = TrustedRuntimeContextMiddleware()
    request = _Request(
        tool_call={
            "name": "run_assignment",
            "id": "call-1",
            "args": {"command": "pytest", "session_id": "spoofed"},
        },
        tool=_Tool(),
        runtime=_Runtime(
            context=CognitionContext.from_scope(
                {"tenant": "acme", "project": "ios"},
                session_id="trusted-session",
                thread_id="trusted-thread",
            ),
            config={"run_id": "run-1"},
        ),
    )

    async def handler(updated: _Request) -> str:
        return "ok"

    await middleware.awrap_tool_call(request, handler)

    assert events
    _, data = events[0]
    assert data["session_id"] == "trusted-session"
    assert data["run_id"] == "run-1"
    assert data["scope_keys"] == ["project", "tenant"]
    assert "acme" not in str(data)


@pytest.mark.asyncio
async def test_trusted_context_leaves_tools_without_reserved_fields_unchanged() -> None:
    middleware = TrustedRuntimeContextMiddleware()
    request = _Request(
        tool_call={
            "name": "plain_tool",
            "id": "call-2",
            "args": {"query": "hello"},
        },
        tool=None,
        runtime=_Runtime(
            context=CognitionContext.from_scope({"tenant": "acme"}, session_id="session-1"),
            config={},
        ),
    )

    seen_request: _Request | None = None

    async def handler(updated: _Request) -> str:
        nonlocal seen_request
        seen_request = updated
        return "ok"

    result = await middleware.awrap_tool_call(request, handler)

    assert result == "ok"
    assert seen_request is request


@pytest.mark.asyncio
async def test_tool_argument_validation_returns_repair_feedback() -> None:
    middleware = ToolArgumentValidationMiddleware()
    request = _Request(
        tool_call={
            "name": "run_assignment",
            "id": "call-3",
            "args": {"session_id": "session-1"},
        },
        tool=_Tool(),
        runtime=_Runtime(
            context=CognitionContext.from_scope({"tenant": "acme"}, session_id="session-1"),
            config={},
        ),
    )

    async def handler(updated: _Request) -> str:
        raise AssertionError("invalid tool calls should not reach the handler")

    result = await middleware.awrap_tool_call(request, handler)

    assert result.status == "error"
    assert result.tool_call_id == "call-3"
    assert "Tool argument validation failed" in result.content
    assert "command" in result.content


@pytest.mark.asyncio
async def test_blocked_tool_emits_redacted_tool_safety_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[tuple[str, dict[str, Any]]] = []

    async def fake_dispatch(event_name: str, data: dict[str, Any]) -> None:
        events.append((event_name, data))

    monkeypatch.setattr(
        "server.app.agent.middleware.adispatch_custom_event",
        fake_dispatch,
    )
    middleware = ToolSecurityMiddleware(blocked_tools=["execute"])
    request = _Request(
        tool_call={
            "name": "execute",
            "id": "call-blocked",
            "args": {"command": "rm -rf /"},
        },
        tool=None,
        runtime=_Runtime(
            context=CognitionContext.from_scope(
                {"tenant": "acme"},
                session_id="session-1",
                thread_id="thread-1",
            ),
            config={"run_id": "run-1"},
        ),
    )

    async def handler(updated: _Request) -> str:
        raise AssertionError("blocked tool calls should not reach the handler")

    result = await middleware.awrap_tool_call(request, handler)

    assert result.status == "error"
    assert result.tool_call_id == "call-blocked"
    assert events
    event_name, data = events[0]
    assert event_name == "tool_blocked"
    assert data["event"] == "tool_blocked"
    assert data["tool_name"] == "execute"
    assert data["tool_call_id"] == "call-blocked"
    assert data["session_id"] == "session-1"
    assert data["run_id"] == "run-1"
    assert data["scope_keys"] == ["tenant"]
    assert "acme" not in str(data)


@pytest.mark.asyncio
async def test_tool_visibility_filters_excluded_model_tools() -> None:
    middleware = ToolVisibilityMiddleware(
        excluded_tools=["execute", "glob", "grep", "ls", "read_file"]
    )
    request = _ModelRequest(
        tools=[
            {"type": "function", "function": {"name": "grep"}},
            {"name": "execute"},
            _NamedTool("ls"),
            _plain_tool,
        ]
    )

    seen_request: _ModelRequest | None = None

    async def handler(updated: _ModelRequest) -> list[Any]:
        nonlocal seen_request
        seen_request = updated
        return updated.tools

    result = await middleware.awrap_model_call(request, handler)

    assert result == [_plain_tool]
    assert seen_request is not None
    assert seen_request.tools == [_plain_tool]
    assert request.tools != seen_request.tools


def test_tool_visibility_leaves_allowed_model_tools() -> None:
    middleware = ToolVisibilityMiddleware(excluded_tools=["grep"])
    request = _ModelRequest(tools=[_plain_tool])

    def handler(updated: _ModelRequest) -> list[Any]:
        return updated.tools

    assert middleware.wrap_model_call(request, handler) == [_plain_tool]
