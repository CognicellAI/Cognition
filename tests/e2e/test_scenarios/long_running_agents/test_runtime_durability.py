"""Business Scenario: Runtime Durability And Traceability

A streamed agent turn must leave durable, queryable state that builders can use
to distinguish progress from stalls without scraping logs.
"""

from __future__ import annotations

import json
import os
import uuid
from typing import Any

import pytest

from tests.e2e.test_scenarios.conftest import (
    ScenarioTestClient,
    is_terminal_stream_event,
)


async def _collect_sse_events(
    api_client: ScenarioTestClient,
    session_id: str,
    content: str,
    timeout: float = 60.0,
    stop_on_terminal: bool = True,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event: str | None = None

    async with api_client.client.stream(
        "POST",
        f"{api_client.base_url}/sessions/{session_id}/messages",
        json={"content": content},
        headers={
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
            **api_client.scope_header,
        },
        timeout=timeout,
    ) as response:
        response.raise_for_status()
        async for line in response.aiter_lines():
            if line.startswith("event: "):
                current_event = line[7:].strip()
            elif line.startswith("data: ") and current_event:
                payload = json.loads(line[6:])
                payload["event"] = current_event
                events.append(payload)
                if stop_on_terminal and is_terminal_stream_event(payload):
                    break
                current_event = None

    return events


def _strict_tool_gate_enabled() -> bool:
    return os.getenv("COGNITION_STRICT_TOOL_E2E", "").lower() in {"1", "true", "yes"}


def _use_isolated_user_scope(api_client: ScenarioTestClient) -> None:
    """Avoid persisted scoped provider config from other local scenario runs."""
    api_client.scope_header = {
        "X-Cognition-Scope-User": f"runtime-durability-{uuid.uuid4().hex[:8]}"
    }


@pytest.mark.asyncio
class TestRuntimeDurability:
    """Builder-facing session state reflects runtime activity."""

    async def test_streamed_turn_is_queryable_as_run_events_and_messages(
        self,
        api_client: ScenarioTestClient,
    ) -> None:
        _use_isolated_user_scope(api_client)
        session_id = await api_client.create_session("Runtime Durability")

        try:
            events = await _collect_sse_events(
                api_client,
                session_id,
                "Reply with exactly: durable-ok. Do not call tools.",
            )
            assert events, "Expected streamed runtime events"
            assert any(is_terminal_stream_event(event) for event in events), events

            session_response = await api_client.get(f"/sessions/{session_id}")
            assert session_response.status_code == 200, session_response.text
            session = session_response.json()
            assert session["latest_run_id"] is not None
            assert session["last_activity_at"] is not None
            assert session["message_count"] >= 1

            runs_response = await api_client.get(f"/sessions/{session_id}/runs")
            assert runs_response.status_code == 200, runs_response.text
            runs = runs_response.json()["runs"]
            assert len(runs) == 1
            run = runs[0]
            assert run["id"] == session["latest_run_id"]
            assert run["status"] in {"done", "failed", "aborted", "active"}

            events_response = await api_client.get(f"/sessions/{session_id}/events")
            assert events_response.status_code == 200, events_response.text
            durable_events = events_response.json()["events"]
            durable_event_types = [event["event_type"] for event in durable_events]
            assert durable_event_types[:2] == [
                "run.started",
                "message.user.accepted",
            ]
            assert all(event["run_id"] == run["id"] for event in durable_events)
            assert [event["sequence"] for event in durable_events] == sorted(
                event["sequence"] for event in durable_events
            )
            assert "message.assistant.completed" in durable_event_types or any(
                event_type.startswith("run.") for event_type in durable_event_types
            )

            messages_response = await api_client.get(f"/sessions/{session_id}/messages")
            assert messages_response.status_code == 200, messages_response.text
            messages = messages_response.json()["messages"]
            assert any(message["role"] == "user" for message in messages)
            if run["status"] == "done":
                assert any(message["role"] == "assistant" for message in messages)
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_tool_activity_is_queryable_as_durable_events(
        self,
        api_client: ScenarioTestClient,
    ) -> None:
        _use_isolated_user_scope(api_client)
        agent_name = f"runtime-tool-durability-{uuid.uuid4().hex[:8]}"
        session_id: str | None = None

        create_agent = await api_client.post(
            "/agents",
            json={
                "name": agent_name,
                "system_prompt": (
                    "You must call the ls tool exactly once before answering. "
                    "Do not answer from memory."
                ),
                "tools": ["ls"],
                "temperature": 0,
            },
        )
        assert create_agent.status_code == 201, create_agent.text

        try:
            session_id = await api_client.create_session(
                "Tool Runtime Durability",
                agent_name=agent_name,
            )
            stream_events = await _collect_sse_events(
                api_client,
                session_id,
                "Use the ls tool to list the current directory. Call the tool now.",
                timeout=60.0,
                stop_on_terminal=False,
            )
            stream_tool_calls = [
                event for event in stream_events if event.get("event") == "tool_call"
            ]
            stream_tool_results = [
                event for event in stream_events if event.get("event") == "tool_result"
            ]

            if not stream_tool_calls:
                message = (
                    "Configured model did not emit a tool_call event. Set "
                    "COGNITION_STRICT_TOOL_E2E=1 in release validation to make "
                    "this a hard failure."
                )
                if _strict_tool_gate_enabled():
                    pytest.fail(message)
                pytest.skip(message)

            session_response = await api_client.get(f"/sessions/{session_id}")
            assert session_response.status_code == 200, session_response.text
            session = session_response.json()
            assert session["latest_run_id"] is not None
            assert session["last_activity_at"] is not None

            events_response = await api_client.get(f"/sessions/{session_id}/events")
            assert events_response.status_code == 200, events_response.text
            durable_events = events_response.json()["events"]
            durable_event_types = [event["event_type"] for event in durable_events]

            assert "tool.call.started" in durable_event_types
            assert any(
                event_type in durable_event_types
                for event_type in {"tool.call.completed", "tool.call.failed"}
            )
            assert all(
                event["run_id"] == session["latest_run_id"] for event in durable_events
            )
            assert [event["sequence"] for event in durable_events] == sorted(
                event["sequence"] for event in durable_events
            )

            durable_tool_call = next(
                event
                for event in durable_events
                if event["event_type"] == "tool.call.started"
            )
            assert durable_tool_call["payload"]["tool_name"] == "ls"
            assert durable_tool_call["payload"]["tool_call_id"] in {
                event.get("id") for event in stream_tool_calls
            }

            if stream_tool_results:
                result_ids = {event.get("tool_call_id") for event in stream_tool_results}
                assert durable_tool_call["payload"]["tool_call_id"] in result_ids
        finally:
            if session_id is not None:
                await api_client.delete(f"/sessions/{session_id}")
            await api_client.delete(f"/agents/{agent_name}")
