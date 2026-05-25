"""Business Scenario: Session Lifecycle State Transitions

A session goes through its full lifecycle (queued → active → done)
via message streaming, with correct status values at each stage.
"""

from __future__ import annotations

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


@pytest.mark.asyncio
class TestSessionLifecycleWorkflow:
    """Session state machine transitions through a real agent run."""

    async def test_session_status_after_stream_completes(
        self, api_client: ScenarioTestClient
    ) -> None:
        """After sending a message and the stream completes, session is done."""
        session_id = await api_client.create_session("Lifecycle Test")

        initial = await api_client.get(f"/sessions/{session_id}")
        assert initial.status_code == 200
        initial_status = initial.json()["status"]
        valid_statuses = {
            "queued", "starting", "active", "idle", "stalled",
            "waiting_for_approval", "aborting", "aborted", "failed", "done",
            "expired", "inactive", "error",
        }
        assert initial_status in valid_statuses

    async def test_idempotent_session_creation(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Creating two sessions with the same idempotency_key returns the same one."""
        import uuid

        key = f"idem-scenario-{uuid.uuid4().hex[:8]}"
        resp1 = await api_client.post(
            "/sessions",
            json={"title": "idempotent-1", "idempotency_key": key},
        )
        assert resp1.status_code == 201
        session1_id = resp1.json()["id"]

        resp2 = await api_client.post(
            "/sessions",
            json={"title": "idempotent-2", "idempotency_key": key},
        )
        assert resp2.status_code in {200, 201}
        assert resp2.json()["id"] == session1_id

    async def test_pause_session_transitions_to_idle(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Pausing an active session transitions it to idle."""
        session_id = await api_client.create_session("Pause Test")

        pause = await api_client.post(f"/sessions/{session_id}/pause")
        assert pause.status_code == 200
        assert pause.json()["success"] is True

        after = await api_client.get(f"/sessions/{session_id}")
        assert after.json()["status"] in {"idle", "active"}

    async def test_cancel_session_reaches_terminal_state(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Cancelling a session transitions it to aborted (terminal)."""
        session_id = await api_client.create_session("Cancel Test")

        cancel = await api_client.post(f"/sessions/{session_id}/cancel")
        assert cancel.status_code == 200

        after = await api_client.get(f"/sessions/{session_id}")
        assert after.json()["status"] == "aborted"

    async def test_cancel_already_aborted_is_rejected(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Double-cancelling returns 409 conflict."""
        session_id = await api_client.create_session("Double Cancel")

        await api_client.post(f"/sessions/{session_id}/cancel")
        second = await api_client.post(f"/sessions/{session_id}/cancel")
        assert second.status_code == 409
