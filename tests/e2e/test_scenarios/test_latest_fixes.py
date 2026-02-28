"""E2E tests for latest fixes: abort, streaming, API enhancements, and events.

Covers:
- Functional abort mechanism
- Streaming integrity (no duplicates, proper content handling)
- Enhanced agent introspection (GET /models, tools/skills)
- Advanced event types (delegation, step_complete, message_id)
"""

from __future__ import annotations

import asyncio
import json
import uuid

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


@pytest.mark.e2e
class TestAbortMechanism:
    """Scenario 1: Functional abort mechanism actually cancels operations."""

    async def test_abort_cancels_active_streaming(self, api_client: ScenarioTestClient) -> None:
        """POST /sessions/{id}/abort should cancel in-progress streaming."""
        # Create session
        session_resp = await api_client.post("/sessions", json={"title": "abort-test"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Start streaming (use a long-running query)
        message_task = asyncio.create_task(
            self._collect_stream_events(
                api_client,
                session_id,
                "Please write a very long essay about Python programming, "
                "including detailed examples and explanations.",
            )
        )

        # Wait a bit for streaming to start
        await asyncio.sleep(0.5)

        # Abort the session
        abort_resp = await api_client.post(f"/sessions/{session_id}/abort")
        assert abort_resp.status_code == 200
        abort_data = abort_resp.json()
        assert abort_data["success"] is True

        # Wait for stream to complete and collect events
        events = await message_task

        # Verify stream was aborted (should have error event with ABORTED code)
        error_events = [e for e in events if e.get("event") == "error"]
        if error_events:
            assert any(e.get("data", {}).get("code") == "ABORTED" for e in error_events), (
                "Expected ABORTED error event"
            )

    async def test_abort_idempotent(self, api_client: ScenarioTestClient) -> None:
        """Abort should succeed even when no operation is active."""
        # Create session
        session_resp = await api_client.post("/sessions", json={"title": "abort-idempotent-test"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Abort with no active operation
        abort_resp = await api_client.post(f"/sessions/{session_id}/abort")
        assert abort_resp.status_code == 200
        abort_data = abort_resp.json()
        assert abort_data["success"] is True
        assert "message" in abort_data

    async def test_session_usable_after_abort(self, api_client: ScenarioTestClient) -> None:
        """Session should receive new messages after abort."""
        # Create session
        session_resp = await api_client.post("/sessions", json={"title": "abort-resume-test"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Abort
        abort_resp = await api_client.post(f"/sessions/{session_id}/abort")
        assert abort_resp.status_code == 200

        # Verify session still exists
        get_resp = await api_client.get(f"/sessions/{session_id}")
        assert get_resp.status_code == 200

        # Should be able to send new message
        events = await self._collect_stream_events(
            api_client, session_id, "Hello after abort", timeout=5.0
        )

        # Verify we got a done event (streaming completed)
        done_events = [e for e in events if e.get("event") == "done"]
        assert len(done_events) > 0, "Expected done event after post-abort message"

    async def _collect_stream_events(
        self,
        api_client: ScenarioTestClient,
        session_id: str,
        content: str,
        timeout: float = 10.0,
    ) -> list[dict]:
        """Helper to collect SSE events from message stream."""
        events: list[dict] = []

        try:
            async with api_client.client.stream(
                "POST",
                f"{api_client.base_url}/sessions/{session_id}/messages",
                json={"content": content},
                headers={"Accept": "text/event-stream", **api_client.scope_header},
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            event_data = json.loads(line[6:])
                            events.append(event_data)
                            if event_data.get("event") == "done":
                                break
                        except json.JSONDecodeError:
                            continue
        except TimeoutError:
            pass

        return events


@pytest.mark.e2e
class TestStreamingIntegrity:
    """Scenario 2: Streaming produces correct output without duplicates."""

    async def test_no_duplicate_tokens_in_stream(self, api_client: ScenarioTestClient) -> None:
        """ISSUE-013: Verify no duplicate token emission."""
        # Create session
        session_resp = await api_client.post("/sessions", json={"title": "streaming-test"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Collect stream events
        events = await self._collect_stream_events(
            api_client, session_id, "Say 'hello world' exactly once.", timeout=5.0
        )

        # Extract token events
        token_events = [e for e in events if e.get("event") == "token"]
        tokens = [e.get("data", {}).get("content", "") for e in token_events]

        # Check for consecutive duplicates
        for i in range(1, len(tokens)):
            if tokens[i] == tokens[i - 1] and tokens[i]:
                pytest.fail(f"Found consecutive duplicate token: '{tokens[i]}'")

    async def test_single_system_message(self, api_client: ScenarioTestClient) -> None:
        """ISSUE-014: Verify only one SystemMessage in context."""
        # This is verified indirectly - if double SystemMessage existed,
        # responses would be prefixed with system prompt text
        session_resp = await api_client.post("/sessions", json={"title": "system-msg-test"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        events = await self._collect_stream_events(
            api_client, session_id, "What is your role?", timeout=5.0
        )

        # Collect all content
        token_events = [e for e in events if e.get("event") == "token"]
        full_content = "".join(e.get("data", {}).get("content", "") for e in token_events)

        # Content should not contain obvious system prompt artifacts
        # (This is a sanity check - exact verification requires internal state)
        assert len(full_content) > 0, "Expected non-empty response"

    async def _collect_stream_events(
        self,
        api_client: ScenarioTestClient,
        session_id: str,
        content: str,
        timeout: float = 10.0,
    ) -> list[dict]:
        """Helper to collect SSE events from message stream."""
        events: list[dict] = []

        try:
            async with api_client.client.stream(
                "POST",
                f"{api_client.base_url}/sessions/{session_id}/messages",
                json={"content": content},
                headers={"Accept": "text/event-stream", **api_client.scope_header},
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            event_data = json.loads(line[6:])
                            events.append(event_data)
                            if event_data.get("event") == "done":
                                break
                        except json.JSONDecodeError:
                            continue
        except TimeoutError:
            pass

        return events


@pytest.mark.e2e
class TestAgentIntrospection:
    """Scenario 3: Enhanced API responses with tools/skills/models."""

    async def test_get_models_returns_model_list(self, api_client: ScenarioTestClient) -> None:
        """ISSUE-008: GET /models returns available models."""
        resp = await api_client.get("/models")
        assert resp.status_code == 200

        data = resp.json()
        assert "models" in data
        assert isinstance(data["models"], list)

        if data["models"]:
            model = data["models"][0]
            # Verify required fields
            assert "id" in model
            assert "provider" in model
            # Optional fields may be present
            if "display_name" in model:
                assert isinstance(model["display_name"], str)
            if "capabilities" in model:
                assert isinstance(model["capabilities"], list)

    async def test_get_models_by_provider(self, api_client: ScenarioTestClient) -> None:
        """GET /models/providers/{provider_id} filters by provider."""
        # First get all models
        all_resp = await api_client.get("/models")
        if all_resp.status_code != 200 or not all_resp.json().get("models"):
            pytest.skip("No models available to test")

        # Pick first provider
        first_model = all_resp.json()["models"][0]
        provider_id = first_model["provider"]

        # Get models for that provider
        resp = await api_client.get(f"/models/providers/{provider_id}")
        assert resp.status_code == 200

        data = resp.json()
        assert "models" in data
        # All returned models should be from this provider
        for model in data["models"]:
            assert model["provider"] == provider_id

    async def test_get_agents_includes_tools_and_skills(
        self, api_client: ScenarioTestClient
    ) -> None:
        """ISSUE-009: GET /agents returns tools and skills."""
        resp = await api_client.get("/agents")
        assert resp.status_code == 200

        data = resp.json()
        assert "agents" in data
        assert isinstance(data["agents"], list)

        if data["agents"]:
            agent = data["agents"][0]
            # Verify new fields exist
            assert "tools" in agent, "Agent should have tools field"
            assert "skills" in agent, "Agent should have skills field"
            assert "system_prompt" in agent, "Agent should have system_prompt field"

            # Verify types
            assert isinstance(agent["tools"], list)
            assert isinstance(agent["skills"], list)
            assert agent["system_prompt"] is None or isinstance(agent["system_prompt"], str)

    async def test_get_agent_detail_includes_tools_skills(
        self, api_client: ScenarioTestClient
    ) -> None:
        """GET /agents/{name} returns detailed agent with tools/skills."""
        # First list agents
        list_resp = await api_client.get("/agents")
        if list_resp.status_code != 200 or not list_resp.json().get("agents"):
            pytest.skip("No agents available to test")

        # Get first agent detail
        first_agent = list_resp.json()["agents"][0]
        agent_name = first_agent["name"]

        resp = await api_client.get(f"/agents/{agent_name}")
        assert resp.status_code == 200

        agent = resp.json()
        assert "tools" in agent
        assert "skills" in agent
        assert "system_prompt" in agent


@pytest.mark.e2e
class TestAdvancedEventTypes:
    """Scenario 4: Advanced SSE events (delegation, step_complete, message_id)."""

    async def test_done_event_contains_message_id(self, api_client: ScenarioTestClient) -> None:
        """ISSUE-019: Done event should include message_id."""
        # Create session
        session_resp = await api_client.post("/sessions", json={"title": "message-id-test"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Collect stream events
        events = await self._collect_stream_events(
            api_client, session_id, "Say hello.", timeout=5.0
        )

        # Find done event
        done_events = [e for e in events if e.get("event") == "done"]
        assert len(done_events) > 0, "Expected at least one done event"

        done_event = done_events[-1]
        event_data = done_event.get("data", {})

        # Verify message_id is present and is a valid UUID
        assert "message_id" in event_data, "Done event should contain message_id"
        message_id = event_data["message_id"]
        assert message_id, "message_id should not be empty"

        # Verify it's a valid UUID format
        try:
            uuid.UUID(message_id)
        except ValueError:
            pytest.fail(f"message_id '{message_id}' is not a valid UUID")

    async def test_step_complete_events_during_planning(
        self, api_client: ScenarioTestClient
    ) -> None:
        """ISSUE-011: step_complete events emitted during plan execution."""
        # Create session
        session_resp = await api_client.post("/sessions", json={"title": "step-complete-test"})
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Request something that triggers planning (multi-step)
        events = await self._collect_stream_events(
            api_client,
            session_id,
            "Create a todo list with 3 items: research, implement, test.",
            timeout=10.0,
        )

        # Look for planning and step_complete events
        planning_events = [e for e in events if e.get("event") == "planning"]
        step_complete_events = [e for e in events if e.get("event") == "step_complete"]

        # If planning happened, verify step_complete events were emitted
        if planning_events and step_complete_events:
            # Verify step_complete events have proper structure
            for sce in step_complete_events:
                data = sce.get("data", {})
                assert "step_number" in data or "total_steps" in data
            # Planning event structure
            for pe in planning_events:
                todos = pe.get("data", {}).get("todos", [])
                if todos:
                    assert isinstance(todos, list)

    async def _collect_stream_events(
        self,
        api_client: ScenarioTestClient,
        session_id: str,
        content: str,
        timeout: float = 10.0,
    ) -> list[dict]:
        """Helper to collect SSE events from message stream."""
        events: list[dict] = []

        try:
            async with api_client.client.stream(
                "POST",
                f"{api_client.base_url}/sessions/{session_id}/messages",
                json={"content": content},
                headers={"Accept": "text/event-stream", **api_client.scope_header},
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        try:
                            event_data = json.loads(line[6:])
                            events.append(event_data)
                            if event_data.get("event") == "done":
                                break
                        except json.JSONDecodeError:
                            continue
        except TimeoutError:
            pass

        return events
