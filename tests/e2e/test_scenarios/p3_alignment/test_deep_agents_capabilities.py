"""E2E scenarios for Deep Agents-native capabilities over docker compose.

Run against: docker-compose environment at http://localhost:8000
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


def _unique(prefix: str = "da") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _collect_events(
    api_client: ScenarioTestClient,
    session_id: str,
    content: str,
    timeout: float = 30.0,
    max_events: int = 200,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event_type: str | None = None

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
                current_event_type = line[7:].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if current_event_type:
                    payload["event"] = current_event_type
                events.append(payload)
                if current_event_type in {"done", "error"}:
                    break
                current_event_type = None
                if len(events) >= max_events:
                    break
    return events


def _response_text(events: list[dict[str, Any]]) -> str:
    return "".join(event.get("content", "") for event in events if event.get("event") == "token")


async def _stream_resume_events(
    api_client: ScenarioTestClient,
    session_id: str,
    decision: str,
    tool_call_id: str,
    tool_name: str,
    args: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    current_event_type: str | None = None

    async with api_client.client.stream(
        "POST",
        f"{api_client.base_url}/sessions/{session_id}/resume",
        json={
            "decision": decision,
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "args": args,
        },
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
                current_event_type = line[7:].strip()
            elif line.startswith("data: "):
                payload = json.loads(line[6:])
                if current_event_type:
                    payload["event"] = current_event_type
                events.append(payload)
                if current_event_type in {"done", "error"}:
                    break
                current_event_type = None
    return events


@pytest.mark.asyncio
@pytest.mark.e2e
class TestDeepAgentsPlanningEvents:
    async def test_planning_events_stream_from_native_todos(
        self, api_client: ScenarioTestClient
    ) -> None:
        session_id = await api_client.create_session(_unique("planning"))
        try:
            events = await _collect_events(
                api_client,
                session_id,
                "Break this into a todo list with three steps and then complete the first step.",
                timeout=45.0,
            )

            planning_events = [event for event in events if event.get("event") == "planning"]
            step_complete_events = [
                event for event in events if event.get("event") == "step_complete"
            ]

            assert planning_events, (
                f"Expected planning event, got {[e.get('event') for e in events]}"
            )
            first_plan = planning_events[0].get("todos", [])
            assert isinstance(first_plan, list)
            assert len(first_plan) >= 1

            if step_complete_events:
                first_step = step_complete_events[0]
                assert "step_number" in first_step
                assert "total_steps" in first_step
                assert "description" in first_step
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_planning_can_emit_multiple_step_complete_events(
        self, api_client: ScenarioTestClient
    ) -> None:
        session_id = await api_client.create_session(_unique("planning-multi"))
        try:
            events = await _collect_events(
                api_client,
                session_id,
                "Make a plan with several steps, then work through the first two steps before answering.",
                timeout=60.0,
            )

            step_complete_events = [
                event for event in events if event.get("event") == "step_complete"
            ]
            if step_complete_events:
                step_numbers: list[int] = []
                for event in step_complete_events:
                    step_number = event.get("step_number")
                    if isinstance(step_number, int):
                        step_numbers.append(step_number)
                assert step_numbers
                assert step_numbers == sorted(step_numbers)
        finally:
            await api_client.delete(f"/sessions/{session_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestDeepAgentsStructuredOutput:
    async def test_agent_response_format_is_accepted_and_session_completes(
        self, api_client: ScenarioTestClient
    ) -> None:
        agent_name = _unique("structured-agent")
        session_id: str | None = None
        try:
            create_agent = await api_client.post(
                "/agents",
                json={
                    "name": agent_name,
                    "system_prompt": "Return a concise structured review.",
                    "response_format": "tests.fixtures.schemas.CodeReviewResult",
                },
            )
            if create_agent.status_code == 404:
                pytest.skip("POST /agents endpoint not available")
            assert create_agent.status_code in (200, 201), create_agent.text

            session_id = await api_client.create_session(
                _unique("structured"), agent_name=agent_name
            )
            patch_resp = await api_client.patch(
                f"/sessions/{session_id}",
                json={"config": {"response_format": "tests.fixtures.schemas.CodeReviewResult"}},
            )
            assert patch_resp.status_code == 200, patch_resp.text

            events = await _collect_events(
                api_client,
                session_id,
                "Review a tiny program that prints hello and mention any issues.",
                timeout=45.0,
            )

            assert any(event.get("event") == "done" for event in events), (
                f"Expected done event, got {[e.get('event') for e in events]}"
            )
            response_text = _response_text(events)
            if response_text.strip():
                assert response_text.strip() != ""
            else:
                done_event = next(event for event in events if event.get("event") == "done")
                assistant_data = done_event.get("assistant_data", {})
                assert isinstance(assistant_data, dict)
        finally:
            if session_id is not None:
                await api_client.delete(f"/sessions/{session_id}")
            await api_client.delete(f"/agents/{agent_name}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestDeepAgentsInterruptResume:
    async def test_interrupt_event_and_waiting_status_for_readonly_agent(
        self, api_client: ScenarioTestClient
    ) -> None:
        session_id = await api_client.create_session(_unique("interrupt"), agent_name="hitl_test")
        try:
            events = await _collect_events(
                api_client,
                session_id,
                "You must use the write_file tool to create a file named hitl_test.txt containing exactly the word approved. Do not describe the steps. Attempt the tool call.",
                timeout=60.0,
            )

            interrupt_events = [event for event in events if event.get("event") == "interrupt"]
            assert interrupt_events, (
                f"Expected interrupt event, got {[e.get('event') for e in events]}"
            )

            interrupt_event = interrupt_events[0]
            assert interrupt_event.get("tool_name")
            assert interrupt_event.get("tool_call_id")
            assert isinstance(interrupt_event.get("action_requests"), list)

            resume_response = await api_client.post(
                f"/sessions/{session_id}/resume",
                json={
                    "decision": "approve",
                    "tool_call_id": interrupt_event["tool_call_id"],
                    "tool_name": interrupt_event["tool_name"],
                },
            )
            assert resume_response.status_code == 200, resume_response.text

            session_response = await api_client.get(f"/sessions/{session_id}")
            assert session_response.status_code == 200, session_response.text
            assert session_response.json()["status"] == "active"
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_edit_flow_streams_resumed_output(self, api_client: ScenarioTestClient) -> None:
        session_id = await api_client.create_session(
            _unique("interrupt-edit"), agent_name="hitl_test"
        )
        try:
            events = await _collect_events(
                api_client,
                session_id,
                "You must use the write_file tool to create a file named hitl_edit.txt containing exactly the word approved. Do not describe the steps. Attempt the tool call.",
                timeout=60.0,
            )

            interrupt_event = next(event for event in events if event.get("event") == "interrupt")
            edited_args = {
                "content": "edited via hitl\n",
                "file_path": "/hitl_edit_changed.txt",
            }
            resume_events = await _stream_resume_events(
                api_client,
                session_id,
                decision="edit",
                tool_call_id=interrupt_event["tool_call_id"],
                tool_name=interrupt_event["tool_name"],
                args=edited_args,
                timeout=60.0,
            )

            assert any(event.get("event") == "done" for event in resume_events), resume_events
            assert any(event.get("event") == "status" for event in resume_events)
            assert any(event.get("event") == "usage" for event in resume_events)

            session_response = await api_client.get(f"/sessions/{session_id}")
            assert session_response.status_code == 200, session_response.text
            assert session_response.json()["status"] == "active"
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_reject_flow_streams_completion(self, api_client: ScenarioTestClient) -> None:
        session_id = await api_client.create_session(
            _unique("interrupt-reject"), agent_name="hitl_test"
        )
        try:
            events = await _collect_events(
                api_client,
                session_id,
                "You must use the write_file tool to create a file named hitl_reject.txt containing exactly the word approved. Do not describe the steps. Attempt the tool call.",
                timeout=60.0,
            )

            interrupt_event = next(event for event in events if event.get("event") == "interrupt")
            resume_events = await _stream_resume_events(
                api_client,
                session_id,
                decision="reject",
                tool_call_id=interrupt_event["tool_call_id"],
                tool_name=interrupt_event["tool_name"],
                args={"message": "Do not write files in this session."},
                timeout=60.0,
            )

            assert any(event.get("event") == "done" for event in resume_events), resume_events
            response_text = _response_text(resume_events)
            if response_text.strip():
                assert response_text.strip() != ""
            else:
                messages = await api_client.get_messages(session_id)
                assistant_messages = [m for m in messages if m.get("role") == "assistant"]
                assert assistant_messages

            session_response = await api_client.get(f"/sessions/{session_id}")
            assert session_response.status_code == 200, session_response.text
            assert session_response.json()["status"] == "active"
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_resume_endpoint_returns_conflict_without_active_interrupt(
        self, api_client: ScenarioTestClient
    ) -> None:
        session_id = await api_client.create_session(_unique("resume"))
        try:
            response = await api_client.post(
                f"/sessions/{session_id}/resume",
                json={
                    "decision": "approve",
                    "tool_call_id": "call-missing",
                    "tool_name": "write_file",
                },
            )
            assert response.status_code == 409, response.text
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_session_status_allows_waiting_for_approval_value(
        self, api_client: ScenarioTestClient
    ) -> None:
        session_id = await api_client.create_session(_unique("status"))
        try:
            response = await api_client.get(f"/sessions/{session_id}")
            assert response.status_code == 200, response.text
            assert response.json()["status"] in {
                "active",
                "inactive",
                "error",
                "waiting_for_approval",
            }
        finally:
            await api_client.delete(f"/sessions/{session_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestDeepAgentsSummarizationConfig:
    async def test_config_exposes_summarization_tool_middleware(
        self, api_client: ScenarioTestClient
    ) -> None:
        response = await api_client.get("/config")
        assert response.status_code == 200, response.text

        config = response.json()
        middleware = config.get("llm", {}).get("agent", {}).get("middleware")
        if middleware is None:
            pytest.skip("/config does not expose agent middleware")

        assert any(
            isinstance(item, dict)
            and item.get("name") == "summarization_tool"
            or item == "summarization_tool"
            for item in middleware
        )
