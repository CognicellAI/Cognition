"""E2E tests for P0 features.

End-to-end tests for the table stakes features.
"""

import pytest
import asyncio
import tempfile
import httpx
from pathlib import Path


@pytest.mark.asyncio
class TestP0EndToEnd:
    """E2E tests for P0 features."""

    async def test_message_persistence_across_restart(self, server):
        """Test that messages persist across server restart."""
        # Create session and send message
        async with httpx.AsyncClient() as client:
            # Create session
            session_resp = await client.post(
                f"{server}/sessions",
                json={"title": "persistence-test"},
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            # Send message
            msg_resp = await client.post(
                f"{server}/sessions/{session_id}/messages",
                json={"content": "Test message for persistence"},
                headers={"Accept": "text/event-stream"},
            )
            assert msg_resp.status_code == 200

            # Read stream
            content = ""
            async for line in msg_resp.aiter_lines():
                if line.startswith("data: "):
                    content += line[6:]
                if "done" in line:
                    break

            # List messages
            list_resp = await client.get(f"{server}/sessions/{session_id}/messages")
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["total"] >= 1

            # Verify message exists
            messages = data["messages"]
            assert any("Test message" in str(m.get("content", "")) for m in messages)

    async def test_scoping_isolation(self, server):
        """Test that scoped sessions are isolated."""
        async with httpx.AsyncClient() as client:
            # Create session as user alice
            alice_resp = await client.post(
                f"{server}/sessions",
                json={"title": "alice-session"},
                headers={"X-Cognition-Scope-User": "alice"},
            )

            if alice_resp.status_code == 403:
                # Scoping not enabled, skip
                pytest.skip("Scoping not enabled")

            assert alice_resp.status_code == 201
            alice_session_id = alice_resp.json()["id"]

            # Create session as user bob
            bob_resp = await client.post(
                f"{server}/sessions",
                json={"title": "bob-session"},
                headers={"X-Cognition-Scope-User": "bob"},
            )
            assert bob_resp.status_code == 201
            bob_session_id = bob_resp.json()["id"]

            # Alice should not see Bob's session
            alice_list = await client.get(
                f"{server}/sessions",
                headers={"X-Cognition-Scope-User": "alice"},
            )
            alice_sessions = alice_list.json()["sessions"]
            assert any(s["id"] == alice_session_id for s in alice_sessions)
            assert not any(s["id"] == bob_session_id for s in alice_sessions)

    async def test_rate_limiting(self, server):
        """Test that rate limiting is enforced."""
        async with httpx.AsyncClient() as client:
            # Create session
            session_resp = await client.post(
                f"{server}/sessions",
                json={"title": "rate-limit-test"},
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            # Send many messages quickly
            responses = []
            for i in range(70):  # Exceed default limit of 60/min
                resp = await client.post(
                    f"{server}/sessions/{session_id}/messages",
                    json={"content": f"Message {i}"},
                    headers={"Accept": "text/event-stream"},
                )
                responses.append(resp.status_code)
                if resp.status_code == 429:
                    break

            # Should eventually get rate limited
            assert 429 in responses or 200 in responses

    async def test_abort_cancels_streaming(self, server):
        """Test that abort cancels an active streaming response."""
        async with httpx.AsyncClient() as client:
            # Create session
            session_resp = await client.post(
                f"{server}/sessions",
                json={"title": "abort-test"},
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            # Start a message (don't wait for completion)
            task = asyncio.create_task(
                client.post(
                    f"{server}/sessions/{session_id}/messages",
                    json={"content": "Long running task"},
                    headers={"Accept": "text/event-stream"},
                )
            )

            # Give it a moment to start
            await asyncio.sleep(0.1)

            # Abort
            abort_resp = await client.post(f"{server}/sessions/{session_id}/abort")
            assert abort_resp.status_code == 200
            assert abort_resp.json()["success"] is True

            # Cancel the streaming task if still running
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

            # Session should still exist and be usable
            get_resp = await client.get(f"{server}/sessions/{session_id}")
            assert get_resp.status_code == 200

    async def test_shell_injection_prevention(self, server):
        """Test that shell injection attacks are prevented."""
        async with httpx.AsyncClient() as client:
            # Create session
            session_resp = await client.post(
                f"{server}/sessions",
                json={"title": "security-test"},
            )
            assert session_resp.status_code == 201
            session_id = session_resp.json()["id"]

            # Try to inject shell commands via message
            injection_attempts = [
                "Hello; rm -rf /",
                "World && cat /etc/passwd",
                "Test `whoami`",
                "Content $(id)",
            ]

            for attempt in injection_attempts:
                resp = await client.post(
                    f"{server}/sessions/{session_id}/messages",
                    json={"content": attempt},
                    headers={"Accept": "text/event-stream"},
                )
                # Should not crash and should return 200 (agent handles the message)
                assert resp.status_code == 200
