"""Business Scenario: Context Controls

Builders can configure context policy on an agent, observe context lifecycle
signals during streams, and inspect redacted context metadata for a session.
"""

from __future__ import annotations

import json
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
                if is_terminal_stream_event(payload):
                    break
                current_event = None

    return events


@pytest.mark.asyncio
class TestContextControls:
    """Context policy is visible and redacted through public API surfaces."""

    async def test_agent_context_policy_streams_and_debugs_redacted_metadata(
        self, api_client: ScenarioTestClient
    ) -> None:
        agent_name = f"context-agent-{uuid.uuid4().hex[:8]}"
        prompt = "Reply with exactly: context-ok. Do not call tools."
        policy = {
            "max_input_tokens": 32000,
            "tool_token_limit_before_evict": 4096,
            "summarization_enabled": False,
            "summarizer_model": "fast-summarizer",
            "offload_large_tool_outputs": True,
            "retention": {"logs": "summarize"},
        }

        create_response = await api_client.post(
            "/agents",
            json={
                "name": agent_name,
                "system_prompt": "You are a concise context-controls test agent.",
                "context_policy": policy,
            },
        )
        assert create_response.status_code == 201, create_response.text

        session_id: str | None = None
        try:
            get_response = await api_client.get(f"/agents/{agent_name}")
            assert get_response.status_code == 200, get_response.text
            agent = get_response.json()
            assert agent["config"]["context_policy"]["max_input_tokens"] == 32000
            assert agent["config"]["context_policy"]["summarization_enabled"] is False

            session_id = await api_client.create_session(
                "Context Controls", agent_name=agent_name
            )
            events = await _collect_sse_events(api_client, session_id, prompt)

            context_events = [event for event in events if event.get("event") == "context"]
            assert context_events, f"Expected context event, got {[e.get('event') for e in events]}"
            policy_event = next(
                event for event in context_events if event.get("action") == "policy_resolved"
            )
            assert policy_event["policy"]["max_input_tokens"] == 32000
            assert policy_event["policy"]["summarization_enabled"] is False
            assert isinstance(policy_event["scope_keys"], list)
            assert "test-user" not in json.dumps(policy_event)

            debug_response = await api_client.get(f"/sessions/{session_id}/context")
            assert debug_response.status_code == 200, debug_response.text
            debug = debug_response.json()
            assert debug["session_id"] == session_id
            assert debug["agent_name"] == agent_name
            assert debug["policy"]["max_input_tokens"] == 32000
            assert debug["message_count"] >= 1
            assert debug["estimated_tokens"] > 0
            assert "messages" in debug
            assert "context-ok" not in debug_response.text
            assert prompt not in debug_response.text
        finally:
            if session_id is not None:
                await api_client.delete(f"/sessions/{session_id}")
            await api_client.delete(f"/agents/{agent_name}")
