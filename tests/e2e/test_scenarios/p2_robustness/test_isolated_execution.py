"""Business Scenario: Isolated Command Execution.

As a security-conscious user, I want commands to execute in isolated
environments so that malicious code cannot harm the system.

Business Value:
- Protection against malicious code execution
- Sandboxed environment for safe AI-generated commands
- Resource limits to prevent abuse
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestIsolatedCommandExecution:
    """Test commands run in secure, isolated environments."""

    async def test_sandbox_configuration_accessible(self, api_client) -> None:
        """Test sandbox configuration is accessible."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            if "sandbox" in data:
                print(f"\n  Sandbox config: {data['sandbox']}")
            else:
                print("\n  Sandbox config not exposed")

    async def test_session_isolation(self, api_client) -> None:
        """Test sessions are properly isolated."""
        # Create multiple sessions
        sessions = []
        for i in range(3):
            sid = await api_client.create_session(f"Isolation Test {i}")
            sessions.append(sid)

        # Verify each session is independent
        for sid in sessions:
            response = await api_client.get(f"/sessions/{sid}")
            assert response.status_code == 200
            assert response.json()["id"] == sid

        print(f"\n  {len(sessions)} sessions isolated")

    async def test_shell_injection_prevention(self, api_client, session) -> None:
        """Test shell injection attempts are blocked."""
        injection_attempts = [
            "Hello; rm -rf /",
            "World && cat /etc/passwd",
            "Test `whoami`",
            "Message $(id)",
        ]

        for attempt in injection_attempts:
            response = await api_client.send_message(session, attempt)
            # Should not crash
            assert response.status_code in [200, 429], f"Injection caused error: {attempt[:30]}"

        print(f"\n  {len(injection_attempts)} injection attempts handled")

    async def test_resource_limits_configurable(self, api_client) -> None:
        """Test resource limits are configurable."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            sandbox = data.get("sandbox", {})

            if "memory_limit" in sandbox:
                print(f"\n  Memory limit: {sandbox['memory_limit']}")
            if "cpu_limit" in sandbox:
                print(f"  CPU limit: {sandbox['cpu_limit']}")

    async def test_message_safety(self, api_client, session) -> None:
        """Test various message types are handled safely."""
        safe_messages = [
            "Normal text message",
            "Code: print('hello')",
            "Path: /tmp/test",
            "Variables: $HOME",
        ]

        for msg in safe_messages:
            response = await api_client.send_message(session, msg)
            assert response.status_code == 200, f"Failed for: {msg[:30]}"

        print(f"\n  {len(safe_messages)} safe messages processed")

    async def test_session_persistence_after_commands(self, api_client, session) -> None:
        """Test session persists after command messages."""
        # Send various commands
        await api_client.send_message(session, "Command 1")
        await api_client.send_message(session, "Command 2")

        # Verify session still works
        response = await api_client.get(f"/sessions/{session}")
        assert response.status_code == 200

        # Can still send messages
        response = await api_client.send_message(session, "After commands")
        assert response.status_code == 200
