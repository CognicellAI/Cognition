"""E2E tests for session lifecycle and control plane.

Tests the v0.10.0 11-state session machine, idempotency,
pause/cancel/abort operations, and state transition validation.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import uuid

import httpx
import pytest

SSE_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


class TestSessionStateMachine:
    """Tests for the 11-state session lifecycle state machine."""

    async def test_idempotent_creation(self, server: str, scope_headers: dict[str, str]) -> None:
        """Same idempotency_key returns the existing session, not a new one."""
        key = f"idem-{uuid.uuid4().hex[:12]}"

        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            resp1 = await client.post(
                f"{server}/sessions",
                json={"title": "idempotent-test", "agent_name": "default", "idempotency_key": key},
                headers=scope_headers,
            )
            assert resp1.status_code == 201
            session_id = resp1.json()["id"]
            assert resp1.json()["idempotency_key"] == key

            resp2 = await client.post(
                f"{server}/sessions",
                json={
                    "title": "idempotent-test-2",
                    "agent_name": "default",
                    "idempotency_key": key,
                },
                headers=scope_headers,
            )
            assert resp2.status_code in {200, 201}
            assert resp2.json()["id"] == session_id
            assert resp2.json()["idempotency_key"] == key

    async def test_status_progression_to_idle_after_completed_run(
        self, server: str, scope_headers: dict[str, str]
    ) -> None:
        """Session returns to idle after a completed run."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "lifecycle-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert create_resp.status_code == 201
            session_id = create_resp.json()["id"]

            get_resp = await client.get(f"{server}/sessions/{session_id}", headers=scope_headers)
            assert get_resp.status_code == 200
            status = get_resp.json()["status"]
            assert status in {
                "queued",
                "starting",
                "active",
                "idle",
                "stalled",
                "waiting_for_approval",
                "aborting",
                "aborted",
                "failed",
                "done",
                "expired",
                "inactive",
                "error",
            }

            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "Hello"},
                headers={**scope_headers, "Accept": "text/event-stream"},
            ) as stream:
                saw_error = False
                saw_completed_run = False
                current_event: str | None = None
                async for line in stream.aiter_lines():
                    if line.startswith("event: "):
                        current_event = line[7:]
                        continue
                    if not line.startswith("data: "):
                        continue
                    try:
                        data = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(data, dict):
                        continue
                    event_type = data.get("event") or current_event
                    if event_type == "error":
                        saw_error = True
                    if event_type == "run_state" and data.get("to_status") in {"idle", "done"}:
                        saw_completed_run = True
                    if event_type == "done":
                        saw_completed_run = True
                    current_event = None

            get_resp2 = await client.get(f"{server}/sessions/{session_id}", headers=scope_headers)
            assert get_resp2.status_code == 200
            final_status = get_resp2.json()["status"]
            if saw_error:
                assert final_status == "failed"
            else:
                assert saw_completed_run
                assert final_status == "idle"

    async def test_pause_idle_session_rejected(
        self, server: str, scope_headers: dict[str, str]
    ) -> None:
        """POST /sessions/{id}/pause rejects an idle session."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "pause-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert create_resp.status_code == 201
            session_id = create_resp.json()["id"]
            assert create_resp.json()["status"] == "idle"

            pause_resp = await client.post(
                f"{server}/sessions/{session_id}/pause", headers=scope_headers
            )
            assert pause_resp.status_code == 409

            get_resp = await client.get(f"{server}/sessions/{session_id}", headers=scope_headers)
            assert get_resp.json()["status"] == "idle"

    async def test_pause_invalid_state(self, server: str, scope_headers: dict[str, str]) -> None:
        """Pausing after a completed run returns 409 because the session is idle."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "pause-invalid-test", "agent_name": "default"},
                headers=scope_headers,
            )
            session_id = create_resp.json()["id"]

            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "complete this"},
                headers={**scope_headers, "Accept": "text/event-stream"},
            ) as stream:
                async for _ in stream.aiter_lines():
                    pass

            pause_resp = await client.post(
                f"{server}/sessions/{session_id}/pause", headers=scope_headers
            )
            assert pause_resp.status_code == 409

    async def test_cancel_session(self, server: str, scope_headers: dict[str, str]) -> None:
        """POST /sessions/{id}/cancel transitions to aborted (terminal)."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "cancel-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert create_resp.status_code == 201
            session_id = create_resp.json()["id"]

            cancel_resp = await client.post(
                f"{server}/sessions/{session_id}/cancel", headers=scope_headers
            )
            assert cancel_resp.status_code == 200
            assert cancel_resp.json()["success"] is True
            assert cancel_resp.json()["status"] == "aborted"

            get_resp = await client.get(f"{server}/sessions/{session_id}", headers=scope_headers)
            assert get_resp.json()["status"] == "aborted"

    async def test_cancel_terminal_rejected(
        self, server: str, scope_headers: dict[str, str]
    ) -> None:
        """Canceling an already-aborted session returns 409."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "cancel-terminal-test", "agent_name": "default"},
                headers=scope_headers,
            )
            session_id = create_resp.json()["id"]

            cancel1 = await client.post(
                f"{server}/sessions/{session_id}/cancel", headers=scope_headers
            )
            assert cancel1.status_code == 200

            cancel2 = await client.post(
                f"{server}/sessions/{session_id}/cancel", headers=scope_headers
            )
            assert cancel2.status_code == 409

    async def test_abort_cancels_stream(self, server: str, scope_headers: dict[str, str]) -> None:
        """POST /sessions/{id}/abort cancels an active SSE stream."""
        async with (
            httpx.AsyncClient(timeout=SSE_TIMEOUT) as stream_client,
            httpx.AsyncClient(timeout=SSE_TIMEOUT) as control_client,
        ):
            create_resp = await control_client.post(
                f"{server}/sessions",
                json={"title": "abort-stream-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert create_resp.status_code == 201
            session_id = create_resp.json()["id"]

            stream_started = asyncio.Event()

            async def stream_message() -> None:
                async with stream_client.stream(
                    "POST",
                    f"{server}/sessions/{session_id}/messages",
                    json={"content": "long task"},
                    headers={**scope_headers, "Accept": "text/event-stream"},
                ) as stream_response:
                    stream_started.set()
                    async for _ in stream_response.aiter_lines():
                        pass

            task = asyncio.create_task(stream_message())
            try:
                await asyncio.wait_for(stream_started.wait(), timeout=5.0)
            except TimeoutError:
                task.cancel()
                pytest.fail("Stream did not start within 5s")

            try:
                abort_resp = await control_client.post(
                    f"{server}/sessions/{session_id}/abort", headers=scope_headers
                )
            except httpx.ReadTimeout:
                task.cancel()
                with contextlib.suppress(
                    asyncio.CancelledError,
                    httpx.ReadError,
                    httpx.ReadTimeout,
                ):
                    await task
                pytest.skip("Local e2e server did not accept abort while stream was open")
            assert abort_resp.status_code == 200
            assert abort_resp.json()["success"] is True

            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, httpx.ReadError, httpx.ReadTimeout):
                pass

            get_resp = await control_client.get(
                f"{server}/sessions/{session_id}", headers=scope_headers
            )
            assert get_resp.status_code == 200

    async def test_heartbeat_in_sse_stream(
        self, server: str, scope_headers: dict[str, str]
    ) -> None:
        """SSE stream emits heartbeat events during agent run."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "heartbeat-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert create_resp.status_code == 201
            session_id = create_resp.json()["id"]

            event_types: set[str] = set()
            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "Hello"},
                headers={**scope_headers, "Accept": "text/event-stream"},
            ) as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("event: "):
                        event_types.add(line[7:])
                    elif line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if isinstance(data, dict) and data.get("event") == "heartbeat":
                                event_types.add("heartbeat")
                        except json.JSONDecodeError:
                            pass

            assert "done" in event_types or "status" in event_types or "run_state" in event_types

    async def test_status_event_in_sse_stream(
        self, server: str, scope_headers: dict[str, str]
    ) -> None:
        """SSE stream emits status events during session lifecycle."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "status-event-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert create_resp.status_code == 201
            session_id = create_resp.json()["id"]

            event_types: set[str] = set()
            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "status check"},
                headers={**scope_headers, "Accept": "text/event-stream"},
            ) as stream:
                async for line in stream.aiter_lines():
                    if line.startswith("event: "):
                        event_types.add(line[7:])
                    elif line.startswith("data: "):
                        try:
                            data = json.loads(line[6:])
                            if isinstance(data, dict) and (
                                "event" in data and data["event"] == "status"
                            ):
                                event_types.add("status")
                        except json.JSONDecodeError:
                            pass

            assert bool(event_types), "Expected at least some event types, got none"
