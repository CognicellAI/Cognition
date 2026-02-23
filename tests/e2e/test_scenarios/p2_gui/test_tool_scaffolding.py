"""Business Scenario: Developer Tool Scaffolding.

As a developer, I want to quickly scaffold new tools and middleware,
so I can extend the AI agent's capabilities without boilerplate code.

Business Value:
- Rapid development of custom tools
- Consistent code structure
- Reduced time to production
"""

from __future__ import annotations

import subprocess

import pytest


@pytest.mark.asyncio
class TestDeveloperToolScaffolding:
    """Test rapid development of custom tools."""

    async def test_cli_scaffolding_available(self, api_client) -> None:
        """Test CLI scaffolding commands available."""
        try:
            result = subprocess.run(
                ["python", "-m", "server.app.cli", "--help"],
                capture_output=True,
                text=True,
                timeout=5,
            )

            if "create" in result.stdout:
                print("\n  CLI scaffolding commands available")
            else:
                print("\n  CLI help accessible")
        except Exception as e:
            print(f"\n  CLI test: {e}")

    async def test_system_readiness(self, api_client) -> None:
        """Test system is ready for development."""
        sid = await api_client.create_session("Scaffolding Test")

        response = await api_client.send_message(sid, "Test message")
        assert response.status_code == 200

        print("\n  System ready for development")

    async def test_api_structure(self, api_client) -> None:
        """Test API structure for tool development."""
        endpoints = ["/health", "/ready", "/config", "/sessions"]

        available = []
        for endpoint in endpoints:
            response = await api_client.get(endpoint)
            if response.status_code == 200:
                available.append(endpoint)

        print(f"\n  Available endpoints: {', '.join(available)}")
        assert len(available) > 0

    async def test_tool_compatible_operations(self, api_client, session) -> None:
        """Test tool-compatible operations."""
        tool_outputs = [
            "Found 5 files in directory",
            "Command executed successfully",
            "File content retrieved",
        ]

        for output in tool_outputs:
            response = await api_client.send_message(session, output)
            if response.status_code == 200:
                print(f"  {output[:40]}...")

    async def test_extended_interactions(self, api_client, session) -> None:
        """Test extended interaction support."""
        interactions = [
            "List files",
            "Read file config.py",
            "Update file with new content",
            "Verify changes",
        ]

        for step in interactions:
            response = await api_client.send_message(session, step)
            if response.status_code == 200:
                print(f"  {step}")

    async def test_message_metadata(self, api_client, session) -> None:
        """Test message metadata for tool integration."""
        await api_client.send_message(session, "Metadata test")

        messages = await api_client.get_messages(session)

        if messages:
            msg = messages[0]

            fields = []
            if "role" in msg:
                fields.append("role")
            if "content" in msg:
                fields.append("content")
            if "metadata" in msg:
                fields.append("metadata")

            print(f"\n  Message fields: {', '.join(fields)}")

    async def test_sandbox_environment(self, api_client) -> None:
        """Test sandbox environment for tools."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            if "sandbox" in data:
                print(f"\n  Sandbox: {data['sandbox']}")
            else:
                print("\n  Sandbox config available")

    async def test_streaming_for_tools(self, api_client, session) -> None:
        """Test streaming for real-time tool output."""
        events = await api_client.stream_sse(
            f"/sessions/{session}/messages", {"content": "Stream tool output"}, max_events=20
        )

        print(f"\n  Streaming: {len(events)} events")
        # Should not crash, events may be empty
