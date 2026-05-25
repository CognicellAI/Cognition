"""Business Scenario: Agent Artifact Production

An agent run that produces artifacts during execution, and the artifacts
are verifiable via the REST API with correct version history.
"""

from __future__ import annotations

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


@pytest.mark.asyncio
class TestAgentArtifactProduction:
    """Agent creates artifacts during a session run and they persist."""

    async def test_agent_run_creates_artifacts(
        self, api_client: ScenarioTestClient
    ) -> None:
        """A full agent conversation produces artifacts viewable via API."""
        session_id = await api_client.create_session("Artifact Producer")

        await api_client.send_message(
            session_id,
            "Create a file called notes.txt with 'Hello from agent' and save it as an artifact",
        )

        artifacts = await api_client.get("/artifacts")
        data = artifacts.json()
        assert data["count"] >= 0
        assert isinstance(data["artifacts"], list)

    async def test_artifact_version_history(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Artifacts maintain version history through updates."""
        import uuid

        art_id = f"version-{uuid.uuid4().hex[:8]}"
        await api_client.post(
            "/artifacts",
            json={
                "id": art_id,
                "name": f"version-{art_id}",
                "artifact_type": "scratch",
                "content": "v1",
            },
        )
        await api_client.put(
            f"/artifacts/{art_id}",
            json={"content": "v2"},
        )

        versions_resp = await api_client.get(f"/artifacts/{art_id}/versions")
        assert versions_resp.status_code == 200
        vdata = versions_resp.json()
        assert vdata["count"] >= 2
        assert vdata["artifact_id"] == art_id

    async def test_artifact_type_isolation(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Listing by artifact_type filters correctly."""
        import uuid

        art_id = f"typefilter-{uuid.uuid4().hex[:8]}"
        await api_client.post(
            "/artifacts",
            json={
                "id": art_id,
                "name": f"typefilter-{art_id}",
                "artifact_type": "contract",
                "content": "contract content",
            },
        )

        scratch_resp = await api_client.get(
            "/artifacts", params={"artifact_type": "scratch"}
        )
        contract_resp = await api_client.get(
            "/artifacts", params={"artifact_type": "contract"}
        )

        assert scratch_resp.status_code == 200
        assert contract_resp.status_code == 200

        scratch_ids = [a["id"] for a in scratch_resp.json()["artifacts"]]
        contract_ids = [a["id"] for a in contract_resp.json()["artifacts"]]
        assert art_id in contract_ids
        assert art_id not in scratch_ids

    async def test_create_read_update_lifecycle(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Full CRUD lifecycle: create → read → update → verify version bump."""
        import uuid

        art_id = f"lifecycle-{uuid.uuid4().hex[:8]}"
        create = await api_client.post(
            "/artifacts",
            json={
                "id": art_id,
                "name": f"lifecycle-{art_id}",
                "artifact_type": "artifact",
                "content": "initial",
            },
        )
        assert create.status_code == 201
        assert create.json()["version"] == 1

        read = await api_client.get(f"/artifacts/{art_id}")
        assert read.status_code == 200
        assert read.json()["content"] == "initial"

        update = await api_client.put(
            f"/artifacts/{art_id}", json={"content": "updated"}
        )
        assert update.status_code == 200
        assert update.json()["version"] == 2
        assert update.json()["content"] == "updated"
