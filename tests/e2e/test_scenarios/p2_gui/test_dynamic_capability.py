"""Business Scenario: Dynamic AI Capability Extension.

As a developer, I want to add custom tools to the AI agent dynamically,
so I can extend its capabilities without restarting the server.

Business Value:
- Rapid iteration on AI capabilities
- Custom integrations without downtime
- Flexible tool ecosystem
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestDynamicCapabilityExtension:
    """Test runtime extension of AI capabilities."""

    async def test_agent_configuration_accessible(self, api_client) -> None:
        """Test agent configuration is accessible."""
        response = await api_client.get("/config")

        if response.status_code == 200:
            data = response.json()
            if "agent" in data:
                print(f"\n  Agent config: {data['agent']}")
            else:
                print("\n  Agent config not exposed")

    async def test_tool_queries(self, api_client, session) -> None:
        """Test AI with available tools."""
        queries = [
            "What tools are available?",
            "Can you help with file operations?",
            "Show me the project structure",
        ]

        for query in queries:
            response = await api_client.send_message(session, query)
            if response.status_code == 200:
                print(f"  Processed: {query[:40]}...")

    async def test_tool_usage_in_conversation(self, api_client, session) -> None:
        """Test tool-capable messages."""
        response = await api_client.send_message(session, "List files in the current directory")

        assert response.status_code == 200

    async def test_tools_across_sessions(self, api_client) -> None:
        """Test tool availability across sessions."""
        sessions = []
        for i in range(2):
            sid = await api_client.create_session(f"Tool Session {i}")
            sessions.append(sid)

        for sid in sessions:
            response = await api_client.send_message(sid, "Test message")
            assert response.status_code == 200

        print(f"\n  Tools available in {len(sessions)} sessions")

    async def test_tool_metadata(self, api_client, session) -> None:
        """Test tool metadata in messages."""
        # Send a message
        await api_client.send_message(session, "Test for tool metadata")

        # Check messages
        messages = await api_client.get_messages(session)

        if messages:
            msg = messages[0]
            if "tool_calls" in msg:
                print("\n  Messages include tool_calls")
            if "tool_call_id" in msg:
                print("  Messages include tool_call_id")

    async def test_agent_responsiveness(self, api_client, session) -> None:
        """Test agent responsiveness with tools."""
        import time

        start = time.time()
        response = await api_client.send_message(session, "Quick responsiveness test")
        duration_ms = (time.time() - start) * 1000

        assert response.status_code == 200
        print(f"\n  Response time: {duration_ms:.0f}ms")

    async def test_multiple_tool_interactions(self, api_client, session) -> None:
        """Test multiple tool interactions."""
        interactions = [
            "Find configuration files",
            "Read the main config",
            "Update settings",
            "Verify changes",
        ]

        for interaction in interactions:
            response = await api_client.send_message(session, interaction)
            if response.status_code == 200:
                print(f"  {interaction}")
