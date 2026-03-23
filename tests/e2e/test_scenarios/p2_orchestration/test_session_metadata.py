from __future__ import annotations

import uuid

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


def _unique(prefix: str = "meta") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.e2e
class TestSessionMetadata:
    async def test_create_and_get_session_metadata(self, api_client: ScenarioTestClient) -> None:
        metadata = {"workflow_id": _unique("wf"), "repository": "myorg/myrepo", "pr_number": "42"}
        session_id = await api_client.create_session(
            _unique("session"),
            metadata=metadata,
        )

        try:
            response = await api_client.get(f"/sessions/{session_id}")
            assert response.status_code == 200, response.text
            assert response.json()["metadata"] == metadata
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_filter_sessions_by_metadata_key(self, api_client: ScenarioTestClient) -> None:
        target_metadata = {
            "workflow_id": _unique("wf"),
            "repository": "myorg/myrepo",
            "pr_number": "99",
        }
        other_metadata = {
            "workflow_id": _unique("wf"),
            "repository": "other/repo",
            "pr_number": "100",
        }

        target_session = await api_client.create_session(
            _unique("target"), metadata=target_metadata
        )
        other_session = await api_client.create_session(_unique("other"), metadata=other_metadata)

        try:
            response = await api_client.get(
                "/sessions",
                params={
                    "metadata.repository": target_metadata["repository"],
                    "metadata.pr_number": target_metadata["pr_number"],
                },
            )
            assert response.status_code == 200, response.text
            data = response.json()

            session_ids = {session["id"] for session in data["sessions"]}
            assert target_session in session_ids
            assert other_session not in session_ids
        finally:
            await api_client.delete(f"/sessions/{target_session}")
            await api_client.delete(f"/sessions/{other_session}")
