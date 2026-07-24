"""Docker Compose e2e tests for the A2A protocol adapter.

These tests target the running Cognition API (usually docker-compose at
COGNITION_E2E_URL=http://localhost:8000) and use the a2a-sdk client
to exercise the A2A protocol surface.

Requires:
  - docker-compose running Cognition
  - COGNITION_E2E_URL env var (defaults to http://localhost:8000)

The tests create a temporary A2A-exposed agent before running and
clean it up afterward. No server restart needed — A2A routes use
catch-all dynamic dispatch.
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient

_A2A_AGENT_NAME = f"a2a-test-{uuid.uuid4().hex[:8]}"


@pytest.fixture(autouse=True, scope="session")
async def _ensure_a2a_agent():
    """Create an A2A-exposed agent for testing, clean up after.

    Uses catch-all dynamic dispatch — the agent is immediately available
    via A2A after creation. No server restart needed.
    """
    import os

    import httpx

    base_url = os.environ.get("COGNITION_E2E_URL", "http://localhost:8000").rstrip("/")
    async with httpx.AsyncClient(base_url=base_url) as client:
        try:
            resp = await client.post(
                "/agents",
                json={
                    "name": _A2A_AGENT_NAME,
                    "system_prompt": "You are a helpful assistant. Reply concisely.",
                    "description": "A2A test agent",
                    "mode": "primary",
                    "a2a": {"exposed": True},
                },
                headers={"X-Cognition-Scope-User": "test-user"},
            )
        except httpx.HTTPError:
            pytest.skip(
                "A2A scenario server is not reachable; set COGNITION_E2E_URL "
                "or start the docker-compose E2E environment."
            )
        assert resp.status_code in (200, 201), f"Failed to create agent: {resp.text}"

        yield

        await client.delete(
            f"/agents/{_A2A_AGENT_NAME}",
            headers={"X-Cognition-Scope-User": "test-user"},
        )


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestAgentCardDiscovery:
    async def test_agent_card_returns_exposed_agents(self, api_client: ScenarioTestClient) -> None:
        response = await api_client.client.get(
            f"{api_client.base_url}/.well-known/agent-card.json",
            params={"assistant_id": _A2A_AGENT_NAME},
            headers=api_client.scope_header,
        )
        assert response.status_code == 200, response.text
        assert response.json()["name"] == f"Cognition ({_A2A_AGENT_NAME})"

    async def test_agent_card_for_specific_agent(self, api_client: ScenarioTestClient) -> None:
        response = await api_client.client.get(
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}/.well-known/agent-card.json",
            headers=api_client.scope_header,
        )
        assert response.status_code == 200, response.text
        card = response.json()
        assert card["name"] == f"Cognition ({_A2A_AGENT_NAME})"
        assert len(card["skills"]) == 1
        assert card["skills"][0]["id"] == _A2A_AGENT_NAME
        assert f"/a2a/{_A2A_AGENT_NAME}" in card["supportedInterfaces"][0]["url"]

    async def test_agent_card_404_for_unknown_agent(self, api_client: ScenarioTestClient) -> None:
        response = await api_client.client.get(
            f"{api_client.base_url}/a2a/nonexistent-agent-xyz/.well-known/agent-card.json",
            headers=api_client.scope_header,
        )
        assert response.status_code == 404

    async def test_agent_card_has_jsonrpc_interface(self, api_client: ScenarioTestClient) -> None:
        response = await api_client.client.get(
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}/.well-known/agent-card.json",
            headers=api_client.scope_header,
        )
        card = response.json()
        interfaces = card.get("supportedInterfaces", [])
        assert len(interfaces) == 1
        assert interfaces[0]["protocolBinding"] == "JSONRPC"
        assert interfaces[0]["protocolVersion"] == "1.0"


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestA2AMessageSend:
    async def test_send_message_returns_completed_task(
        self, api_client: ScenarioTestClient
    ) -> None:
        message = {
            "role": "ROLE_USER",
            "parts": [{"text": "Say hello in exactly 3 words."}],
            "messageId": str(uuid.uuid4()),
        }
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {"message": message},
        }
        response = await api_client.client.post(
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}",
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
        task = result["result"]["task"]
        assert "id" in task
        assert "contextId" in task
        assert task["status"]["state"] == "TASK_STATE_COMPLETED"

    async def test_send_message_returns_artifact(self, api_client: ScenarioTestClient) -> None:
        message = {
            "role": "ROLE_USER",
            "parts": [{"text": "Reply with exactly: A2A_WORKS"}],
            "messageId": str(uuid.uuid4()),
        }
        payload = {
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "SendMessage",
            "params": {"message": message},
        }
        response = await api_client.client.post(
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}",
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
        task = result["result"]["task"]
        artifacts = task.get("artifacts", [])
        assert len(artifacts) >= 1, f"No artifacts in task: {task}"
        artifact = artifacts[0]
        assert "parts" in artifact
        text_parts = [p for p in artifact["parts"] if p.get("text")]
        assert len(text_parts) >= 1


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestA2AStreaming:
    async def test_stream_message_returns_events(self, api_client: ScenarioTestClient) -> None:
        message = {
            "role": "ROLE_USER",
            "parts": [{"text": "Count from 1 to 3."}],
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
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}",
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
                if line.startswith("data: "):
                    data = json.loads(line[6:])
                    events.append(data)

        assert len(events) >= 1, f"No events received: {events}"
        assert all(event.get("jsonrpc") == "2.0" for event in events)
        assert any("statusUpdate" in event.get("result", {}) for event in events)

    async def test_stream_includes_status_events(self, api_client: ScenarioTestClient) -> None:
        message = {
            "role": "ROLE_USER",
            "parts": [{"text": "Say OK."}],
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
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}",
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

        assert any(
            event.get("result", {}).get("statusUpdate", {}).get("status", {}).get("state")
            == "TASK_STATE_COMPLETED"
            for event in events
        )


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestA2ATaskOperations:
    async def test_get_and_list_return_durable_task(self, api_client: ScenarioTestClient) -> None:
        headers = {
            "Content-Type": "application/json",
            "A2A-Version": "1.0",
            **api_client.scope_header,
        }
        sent = await api_client.client.post(
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}",
            json={
                "jsonrpc": "2.0",
                "id": "send-for-task-ops",
                "method": "SendMessage",
                "params": {
                    "message": {
                        "messageId": str(uuid.uuid4()),
                        "role": "ROLE_USER",
                        "parts": [{"text": "Reply with OK."}],
                    }
                },
            },
            headers=headers,
            timeout=60.0,
        )
        task_id = sent.json()["result"]["task"]["id"]

        fetched = await api_client.client.post(
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}",
            json={
                "jsonrpc": "2.0",
                "id": "get-task",
                "method": "GetTask",
                "params": {"id": task_id},
            },
            headers=headers,
        )
        listed = await api_client.client.post(
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}",
            json={
                "jsonrpc": "2.0",
                "id": "list-tasks",
                "method": "ListTasks",
                "params": {"pageSize": 100, "includeArtifacts": True},
            },
            headers=headers,
        )

        assert fetched.json()["result"]["id"] == task_id
        assert task_id in {task["id"] for task in listed.json()["result"]["tasks"]}

    @pytest.mark.parametrize("method", ["CancelTask", "SubscribeToTask"])
    async def test_task_operation_conceals_unknown_ids(
        self,
        api_client: ScenarioTestClient,
        method: str,
    ) -> None:
        response = await api_client.client.post(
            f"{api_client.base_url}/a2a/{_A2A_AGENT_NAME}",
            json={
                "jsonrpc": "2.0",
                "id": f"{method}-missing",
                "method": method,
                "params": {"id": str(uuid.uuid4())},
            },
            headers={
                "Content-Type": "application/json",
                "A2A-Version": "1.0",
                **api_client.scope_header,
            },
        )

        assert response.json()["error"]["code"] == -32001


@pytest.mark.e2e
@pytest.mark.integration
@pytest.mark.asyncio
class TestA2ACapabilities:
    async def test_capabilities_reports_a2a(self, api_client: ScenarioTestClient) -> None:
        response = await api_client.client.get(
            f"{api_client.base_url}/capabilities",
            headers=api_client.scope_header,
        )
        assert response.status_code == 200, response.text
        caps = response.json()
        assert caps["features"]["a2a"] is True
        assert caps["features"]["a2a_jsonrpc"] is True
        assert caps["features"]["a2a_streaming"] is True
        assert caps["features"]["a2a_per_agent_cards"] is True
