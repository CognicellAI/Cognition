"""Business Scenario: Experimental Async Subagent Configuration

Builders can expose remote Agent Protocol graphs as Deep Agents async subagents
through agent definitions without adding a credential surface in Cognition.
This is remote worker interoperability, not the simple in-process
supervisor/subagent pattern.
"""

from __future__ import annotations

import uuid

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


@pytest.mark.asyncio
class TestAsyncSubagentConfig:
    """Async subagent specs round-trip through builder-facing APIs."""

    async def test_agent_async_subagents_round_trip(self, api_client: ScenarioTestClient) -> None:
        agent_name = f"async-agent-{uuid.uuid4().hex[:8]}"
        spec = {
            "name": "researcher",
            "description": "Runs long-running research tasks on a remote graph",
            "graph_id": "research_graph",
            "url": "https://agents.example.com",
        }

        create_response = await api_client.post(
            "/agents",
            json={
                "name": agent_name,
                "system_prompt": "You coordinate background research tasks.",
                "async_subagents": [spec],
            },
        )
        assert create_response.status_code == 201, create_response.text

        try:
            created = create_response.json()
            assert created["async_subagents"] == [spec]
            assert "headers" not in created["async_subagents"][0]

            get_response = await api_client.get(f"/agents/{agent_name}")
            assert get_response.status_code == 200, get_response.text
            agent = get_response.json()
            assert agent["async_subagents"] == [spec]
            assert "headers" not in agent["async_subagents"][0]

            update_response = await api_client.patch(
                f"/agents/{agent_name}",
                json={
                    "async_subagents": [
                        {
                            "name": "builder",
                            "description": "Runs background build tasks",
                            "graph_id": "build_graph",
                        }
                    ]
                },
            )
            assert update_response.status_code == 200, update_response.text
            updated = update_response.json()
            assert updated["async_subagents"] == [
                {
                    "name": "builder",
                    "description": "Runs background build tasks",
                    "graph_id": "build_graph",
                    "url": None,
                }
            ]
        finally:
            await api_client.delete(f"/agents/{agent_name}")
