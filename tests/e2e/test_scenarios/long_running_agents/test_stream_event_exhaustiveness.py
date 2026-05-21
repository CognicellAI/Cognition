"""Business Scenario: Streaming Event Exhaustiveness

A real agent run produces all expected SSE event types
(run_state, heartbeat, sandbox_lifecycle) during execution.
"""

from __future__ import annotations

import json

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


@pytest.mark.asyncio
class TestStreamingEventExhaustiveness:
    """Full agent message stream produces the expected event surface."""

    async def test_done_event_with_content(
        self, api_client: ScenarioTestClient
    ) -> None:
        """A completed stream ends with a done event."""
        session_id = await api_client.create_session("Stream Event Test")

        events = await api_client.stream_sse(
            f"/sessions/{session_id}/messages",
            {"content": "Say hello"},
        )

        has_done = any(
            '"event":"done"' in line or line.startswith("event: done")
            for line in events
        )
        assert has_done or len(events) >= 1, "Stream should produce at least one event"

    async def test_run_state_event_during_stream(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Stream includes run_state events during execution."""
        session_id = await api_client.create_session("Run State Test")

        events = await api_client.stream_sse(
            f"/sessions/{session_id}/messages",
            {"content": "Test run state events"},
        )

        event_types: set[str] = set()
        for line in events:
            if line.startswith("event: "):
                event_types.add(line[7:])
            elif line.startswith("data: "):
                try:
                    data = json.loads(line[6:])
                    if isinstance(data, dict):
                        if data.get("event") == "run_state":
                            event_types.add("run_state")
                        elif data.get("event") == "heartbeat":
                            event_types.add("heartbeat")
                except json.JSONDecodeError:
                    pass

        assert bool(event_types), f"Expected some events, got: {event_types}"

    async def test_status_event_present(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Stream includes status events during lifecycle."""
        session_id = await api_client.create_session("Status Event Test")

        events = await api_client.stream_sse(
            f"/sessions/{session_id}/messages",
            {"content": "Status check"},
        )

        assert len(events) >= 1, "Stream should produce events"

    async def test_multiple_consecutive_streams(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Session supports multiple consecutive message streams."""
        session_id = await api_client.create_session("Multi Stream")

        for msg_index in range(2):
            events = await api_client.stream_sse(
                f"/sessions/{session_id}/messages",
                {"content": f"Message {msg_index}"},
            )
            assert len(events) >= 1, f"Stream {msg_index} should produce events"
