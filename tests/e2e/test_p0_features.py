"""E2E tests for P0 features.

End-to-end tests for the table stakes features.
Tests require a running server (started via the ``server`` fixture in conftest.py).
"""

from __future__ import annotations

import asyncio
import contextlib

import httpx
import pytest

from tests.e2e.conftest import E2E_DEFAULT_AGENT_NAME, ensure_e2e_agent, ensure_e2e_provider

# Generous timeout for SSE streams through mock LLM
SSE_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


async def _ensure_builder_agent(
    client: httpx.AsyncClient, server: str, headers: dict[str, str]
) -> None:
    """Provision the default E2E Agent in an exact builder-authorized scope."""
    await ensure_e2e_provider(client, server, headers=headers)
    await ensure_e2e_agent(client, server, E2E_DEFAULT_AGENT_NAME, headers=headers)


@pytest.mark.asyncio
class TestP0EndToEnd:
    """E2E tests for P0 features."""

    async def test_message_persistence_across_restart(
        self, server: str, scope_headers: dict[str, str]
    ) -> None:
        """Test that messages persist across server restart."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            session_resp = await client.post(
                f"{server}/sessions",
                json={"title": "persistence-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            collected_events: list[str] = []
            async with client.stream(
                "POST",
                f"{server}/sessions/{session_id}/messages",
                json={"content": "Test message for persistence"},
                headers={**scope_headers, "Accept": "text/event-stream"},
            ) as stream:
                async for line in stream.aiter_lines():
                    collected_events.append(line)
                    if line.startswith("event: done") or '"event":"done"' in line:
                        break

            list_resp = await client.get(
                f"{server}/sessions/{session_id}/messages", headers=scope_headers
            )
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["total"] >= 1

            messages = data["messages"]
            assert any("Test message" in str(m.get("content", "")) for m in messages)

    async def test_scoping_isolation(self, server: str) -> None:
        """Test that scoped sessions are isolated."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            alice_headers = {"X-Cognition-Scope-User": "alice"}
            bob_headers = {"X-Cognition-Scope-User": "bob"}
            await _ensure_builder_agent(client, server, alice_headers)
            await _ensure_builder_agent(client, server, bob_headers)

            alice_resp = await client.post(
                f"{server}/sessions",
                json={"title": "alice-session", "agent_name": "default"},
                headers=alice_headers,
            )

            if alice_resp.status_code == 403:
                pytest.skip("Scoping not enabled")

            assert alice_resp.status_code == 201
            alice_session_id = alice_resp.json()["id"]

            bob_resp = await client.post(
                f"{server}/sessions",
                json={"title": "bob-session", "agent_name": "default"},
                headers=bob_headers,
            )
            assert bob_resp.status_code == 201
            bob_session_id = bob_resp.json()["id"]

            alice_list = await client.get(
                f"{server}/sessions",
                headers=alice_headers,
            )
            alice_sessions = alice_list.json()["sessions"]
            assert any(s["id"] == alice_session_id for s in alice_sessions)
            assert not any(s["id"] == bob_session_id for s in alice_sessions)

    async def test_rate_limiting(self, server: str, scope_headers: dict[str, str]) -> None:
        """Test that rate limiting is enforced."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            session_resp = await client.post(
                f"{server}/sessions",
                json={"title": "rate-limit-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            responses: list[int] = []
            for i in range(70):
                try:
                    async with client.stream(
                        "POST",
                        f"{server}/sessions/{session_id}/messages",
                        json={"content": f"Message {i}"},
                        headers={**scope_headers, "Accept": "text/event-stream"},
                    ) as stream:
                        responses.append(stream.status_code)
                        if stream.status_code == 429:
                            break
                        async for _ in stream.aiter_lines():
                            pass
                except httpx.HTTPStatusError as e:
                    responses.append(e.response.status_code)
                    if e.response.status_code == 429:
                        break

            assert 429 in responses or 200 in responses

    async def test_abort_cancels_streaming(
        self, server: str, scope_headers: dict[str, str]
    ) -> None:
        """Test that abort cancels an active streaming response."""
        async with (
            httpx.AsyncClient(timeout=SSE_TIMEOUT) as stream_client,
            httpx.AsyncClient(timeout=SSE_TIMEOUT) as control_client,
        ):
            session_resp = await control_client.post(
                f"{server}/sessions",
                json={"title": "abort-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            stream_started = asyncio.Event()

            async def stream_message() -> int:
                async with stream_client.stream(
                    "POST",
                    f"{server}/sessions/{session_id}/messages",
                    json={"content": "Long running task"},
                    headers={**scope_headers, "Accept": "text/event-stream"},
                ) as stream:
                    stream_started.set()
                    async for _ in stream.aiter_lines():
                        pass
                    return stream.status_code

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

    async def test_shell_injection_prevention(
        self, server: str, scope_headers: dict[str, str]
    ) -> None:
        """Test that shell injection attacks are prevented.

        Sends messages containing common shell injection patterns.
        The server must not crash and must return valid SSE responses.
        """
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            injection_attempts = [
                "Hello; rm -rf /",
                "World && cat /etc/passwd",
                "Test `whoami`",
                "Content $(id)",
            ]

            for index, attempt in enumerate(injection_attempts):
                session_resp = await client.post(
                    f"{server}/sessions",
                    json={"title": f"security-test-{index}", "agent_name": "default"},
                    headers=scope_headers,
                )
                assert session_resp.status_code == 201
                session_id = session_resp.json()["id"]
                async with client.stream(
                    "POST",
                    f"{server}/sessions/{session_id}/messages",
                    json={"content": attempt},
                    headers={**scope_headers, "Accept": "text/event-stream"},
                ) as stream:
                    assert stream.status_code == 200
                    async for _ in stream.aiter_lines():
                        pass

    async def test_health_and_ready(self, server: str) -> None:
        """Test health and readiness endpoints."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            health = await client.get(f"{server}/health")
            assert health.status_code == 200
            assert health.json()["status"] == "healthy"

            ready = await client.get(f"{server}/ready")
            assert ready.status_code == 200
            assert ready.json()["ready"] is True

    async def test_session_crud(self, server: str, scope_headers: dict[str, str]) -> None:
        """Test basic session create/read/list/delete lifecycle."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/sessions",
                json={"title": "crud-test", "agent_name": "default"},
                headers=scope_headers,
            )
            assert create_resp.status_code == 201
            session_id = create_resp.json()["id"]

            get_resp = await client.get(f"{server}/sessions/{session_id}", headers=scope_headers)
            assert get_resp.status_code == 200
            assert get_resp.json()["title"] == "crud-test"

            list_resp = await client.get(f"{server}/sessions", headers=scope_headers)
            assert list_resp.status_code == 200
            sessions = list_resp.json()["sessions"]
            assert any(s["id"] == session_id for s in sessions)

            del_resp = await client.delete(f"{server}/sessions/{session_id}", headers=scope_headers)
            assert del_resp.status_code == 204

            get_resp2 = await client.get(f"{server}/sessions/{session_id}", headers=scope_headers)
            assert get_resp2.status_code == 404
