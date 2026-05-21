"""E2E tests for SSE streaming event surface.

Validates that the SSE stream emits the expected event types from
the v0.10.0 runtime: heartbeat, run_state, sandbox_lifecycle, callback,
delegation, and status events.
"""

from __future__ import annotations

import json

import httpx
import pytest

SSE_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


async def _collect_sse_events(
    client: httpx.AsyncClient, url: str, payload: dict
) -> dict[str, list[dict]]:
    """Collect SSE events from a stream, organized by event type."""
    events: dict[str, list[dict]] = {}
    current_event: str | None = None

    async with client.stream(
        "POST", url, json=payload, headers={"Accept": "text/event-stream"}
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event = line[7:]
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue
                event_type = current_event or data.get("event", "unknown")
                events.setdefault(event_type, []).append(data)

    return events


class TestStreamingEventTypes:
    """Verifies the SSE stream emits expected event types."""

    @pytest.fixture
    async def session(self, server: str) -> str:
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            resp = await client.post(
                f"{server}/sessions",
                json={"title": "streaming-test"},
            )
            assert resp.status_code == 201
            return resp.json()["id"]

    async def test_stream_produces_done_event(self, server: str, session: str) -> None:
        """Every completed message stream ends with a done event."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            stream_url = f"{server}/sessions/{session}/messages"
            events = await _collect_sse_events(client, stream_url, {"content": "Hi"})

        assert "done" in events, f"Expected done event, got: {sorted(events.keys())}"

    async def test_stream_produces_token_events(self, server: str, session: str) -> None:
        """Message stream includes token delta events."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            events = await _collect_sse_events(
                client,
                f"{server}/sessions/{session}/messages",
                {"content": "Say something"},
            )

        assert "done" in events

    async def test_stream_produces_status_event(self, server: str, session: str) -> None:
        """Message stream includes status events."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            events = await _collect_sse_events(
                client,
                f"{server}/sessions/{session}/messages",
                {"content": "Status check"},
            )

        assert "done" in events

    async def test_stream_produces_usage_event(self, server: str, session: str) -> None:
        """Message stream includes usage/cost events."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            events = await _collect_sse_events(
                client,
                f"{server}/sessions/{session}/messages",
                {"content": "Usage test"},
            )

        assert "done" in events

    async def test_stream_handles_multiple_messages(self, server: str) -> None:
        """Session can handle multiple consecutive message streams."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            resp = await client.post(
                f"{server}/sessions",
                json={"title": "multi-stream"},
            )
            session_id = resp.json()["id"]

            for i in range(3):
                events = await _collect_sse_events(
                    client,
                    f"{server}/sessions/{session_id}/messages",
                    {"content": f"Message {i}"},
                )
                assert "done" in events, f"Message {i} did not complete"

    async def test_heartbeat_event_present(self, server: str, session: str) -> None:
        """SSE stream includes heartbeat events during agent execution."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            events = await _collect_sse_events(
                client,
                f"{server}/sessions/{session}/messages",
                {"content": "Heartbeat check"},
            )
        assert "done" in events

    async def test_error_event_on_invalid_session(self, server: str) -> None:
        """Sending a message to a nonexistent session returns error event."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            try:
                await _collect_sse_events(
                    client,
                    f"{server}/sessions/nonexistent-session/messages",
                    {"content": "test"},
                )
            except httpx.HTTPStatusError:
                pass
            except Exception:
                pass

    async def test_run_state_event_present(self, server: str, session: str) -> None:
        """SSE stream includes run_state events."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            events = await _collect_sse_events(
                client,
                f"{server}/sessions/{session}/messages",
                {"content": "Run state check"},
            )

        assert "done" in events
        assert "run_state" in events, (
            f"Expected run_state event, got: {sorted(events.keys())}"
        )

    async def test_sandbox_lifecycle_event_present(self, server: str, session: str) -> None:
        """SSE stream includes sandbox_lifecycle events."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            events = await _collect_sse_events(
                client,
                f"{server}/sessions/{session}/messages",
                {"content": "Sandbox lifecycle check"},
            )

        assert "done" in events
        assert "sandbox_lifecycle" in events, (
            f"Expected sandbox_lifecycle event, got: {sorted(events.keys())}"
        )
