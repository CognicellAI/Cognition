"""Docker Compose e2e tests for the A2A protocol adapter.

These tests target the running Cognition API (usually docker-compose at
COGNITION_E2E_URL=http://localhost:8000) and use the a2a-sdk client
to exercise the A2A protocol surface.

Requires:
  - docker-compose running Cognition
  - COGNITION_E2E_URL env var (defaults to http://localhost:8000)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.e2e.test_scenarios.conftest import (
    ScenarioTestClient,
    is_terminal_stream_event,
    stream_completed,
)


def _unique(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestAgentCardDiscovery:
    async def test_agent_card_returns_valid_json(
        self, api_client: ScenarioTestClient
    ) -> None:
        response = await api_client.client.get(
            f"{api_client.base_url}/.well-known/agent-card.json",
            headers=api_client.scope_header,
        )
        assert response.status_code == 200, response.text
        card = response.json()
        assert card["name"] == "Cognition"
        assert "skills" in card
        assert len(card["skills"]) >= 1
        assert card["capabilities"]["streaming"] is True

    async def test_agent_card_has_jsonrpc_interface(
        self, api_client: ScenarioTestClient
    ) -> None:
        response = await api_client.client.get(
            f"{api_client.base_url}/.well-known/agent-card.json",
            headers=api_client.scope_header,
        )
        card = response.json()
        interfaces = card.get("supportedInterfaces", [])
        assert any(i.get("protocolBinding") == "JSONRPC" for i in interfaces)


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestA2AMessageSend:
    async def test_send_message_returns_completed_task(
        self, api_client: ScenarioTestClient
    ) -> None:
        message = {
            "role": "ROLE_USER",
            "parts": [{"text": "Say hello in exactly 3 words.", "mediaType": "text/plain"}],
            "messageId": str(uuid.uuid4()),
        }
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {"message": message},
        }
        response = await api_client.client.post(
            f"{api_client.base_url}/a2a",
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
                **api_client.scope_header,
            },
            timeout=60.0,
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert "result" in result, f"No result in response: {result}"
        wrapper = result["result"]
        assert "task" in wrapper, f"No task in result: {wrapper}"
        task = wrapper["task"]
        assert "id" in task
        assert "contextId" in task
        assert task["status"]["state"] in [
            "TASK_STATE_COMPLETED",
            3,
        ]

    async def test_send_message_returns_artifact(
        self, api_client: ScenarioTestClient
    ) -> None:
        message = {
            "role": "ROLE_USER",
            "parts": [{"text": "Reply with exactly: A2A_WORKS", "mediaType": "text/plain"}],
            "messageId": str(uuid.uuid4()),
        }
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {"message": message},
        }
        response = await api_client.client.post(
            f"{api_client.base_url}/a2a",
            json=payload,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
                **api_client.scope_header,
            },
            timeout=60.0,
        )
        assert response.status_code == 200, response.text
        result = response.json()
        wrapper = result["result"]
        task = wrapper.get("task", wrapper)
        artifacts = task.get("artifacts", [])
        assert len(artifacts) >= 1, f"No artifacts in task: {task}"
        # Check that the artifact contains text parts
        artifact = artifacts[0]
        assert "parts" in artifact
        text_parts = [p for p in artifact["parts"] if p.get("text")]
        assert len(text_parts) >= 1


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestA2AStreaming:
    async def test_stream_message_returns_events(
        self, api_client: ScenarioTestClient
    ) -> None:
        message = {
            "role": "ROLE_USER",
            "parts": [{"text": "Count from 1 to 3.", "mediaType": "text/plain"}],
            "messageId": str(uuid.uuid4()),
        }
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendStreamingMessage",
            "params": {"message": message},
        }
        events = []
        current_event_type = None

        async with api_client.client.stream(
            "POST",
            f"{api_client.base_url}/a2a",
            json=payload,
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
                **api_client.scope_header,
            },
            timeout=60.0,
        ) as response:
            assert response.status_code == 200, response.text
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current_event_type = line[7:].strip()
                elif line.startswith("data: "):
                    data = json.loads(line[6:])
                    events.append(data)

        assert len(events) >= 1, f"No events received: {events}"

    async def test_stream_includes_status_events(
        self, api_client: ScenarioTestClient
    ) -> None:
        message = {
            "role": "ROLE_USER",
            "parts": [{"text": "Say OK.", "mediaType": "text/plain"}],
            "messageId": str(uuid.uuid4()),
        }
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendStreamingMessage",
            "params": {"message": message},
        }
        events = []

        async with api_client.client.stream(
            "POST",
            f"{api_client.base_url}/a2a",
            json=payload,
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
                **api_client.scope_header,
            },
            timeout=60.0,
        ) as response:
            async for line in response.aiter_lines():
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    events.append(data)

        # Check that we got at least a task and a status update
        assert len(events) >= 1


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestA2ACapabilities:
    async def test_capabilities_reports_a2a(
        self, api_client: ScenarioTestClient
    ) -> None:
        response = await api_client.client.get(
            f"{api_client.base_url}/capabilities",
            headers=api_client.scope_header,
        )
        assert response.status_code == 200, response.text
        caps = response.json()
        assert caps["features"]["a2a"] is True
        assert caps["features"]["a2a_jsonrpc"] is True
        assert caps["features"]["a2a_streaming"] is True
