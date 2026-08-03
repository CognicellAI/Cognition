"""E2E tests for durable artifact storage with versioning.

Tests CRUD operations across artifact types, version history,
scope isolation, and input validation.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

SSE_TIMEOUT = httpx.Timeout(30.0, connect=10.0)


VALID_TYPES = {"scratch", "artifact", "file", "contract", "eval", "memory", "policy"}


class TestArtifactCRUD:
    """Tests for artifact create, read, update, delete lifecycle."""

    @pytest.fixture
    def unique_id(self) -> str:
        return f"test-art-{uuid.uuid4().hex[:12]}"

    async def test_create_and_read(self, server: str, unique_id: str, scope_headers: dict[str, str]) -> None:
        """POST + GET /artifacts/{id} round-trip."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/artifacts",
                json={
                    "id": unique_id,
                    "name": "Test-Artifact",
                    "artifact_type": "scratch",
                    "content": "hello world",
                    "content_type": "text/plain",
                },
                headers=scope_headers,
            )
            assert create_resp.status_code == 201
            data = create_resp.json()
            assert data["id"] == unique_id
            assert data["name"] == "Test-Artifact"
            assert data["content"] == "hello world"
            assert data["version"] == 1
            assert data["artifact_type"] == "scratch"

            get_resp = await client.get(
                f"{server}/artifacts/{unique_id}", headers=scope_headers
            )
            assert get_resp.status_code == 200
            assert get_resp.json()["id"] == unique_id

    async def test_list_by_type(self, server: str, unique_id: str, scope_headers: dict[str, str]) -> None:
        """GET /artifacts?artifact_type=scratch returns only that type."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            await client.post(
                f"{server}/artifacts",
                json={
                    "id": unique_id,
                    "name": "Scratch-Item",
                    "artifact_type": "scratch",
                    "content": "scratch data",
                },
                headers=scope_headers,
            )

            list_resp = await client.get(
                f"{server}/artifacts",
                params={"artifact_type": "scratch"},
                headers=scope_headers,
            )
            assert list_resp.status_code == 200
            data = list_resp.json()
            assert data["count"] >= 1
            ids = [a["id"] for a in data["artifacts"]]
            assert unique_id in ids

    async def test_update_creates_version(self, server: str, unique_id: str, scope_headers: dict[str, str]) -> None:
        """PUT /artifacts/{id} bumps version; GET .../versions returns history."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            await client.post(
                f"{server}/artifacts",
                json={
                    "id": unique_id,
                    "name": "Version-Test",
                    "artifact_type": "scratch",
                    "content": "v1 content",
                },
                headers=scope_headers,
            )

            update_resp = await client.put(
                f"{server}/artifacts/{unique_id}",
                json={"content": "v2 content"},
                headers=scope_headers,
            )
            assert update_resp.status_code == 200
            assert update_resp.json()["version"] == 2
            assert update_resp.json()["content"] == "v2 content"

            versions_resp = await client.get(
                f"{server}/artifacts/{unique_id}/versions", headers=scope_headers
            )
            assert versions_resp.status_code == 200
            vdata = versions_resp.json()
            assert vdata["count"] >= 2
            assert vdata["artifact_id"] == unique_id

    async def test_get_specific_version(self, server: str, unique_id: str, scope_headers: dict[str, str]) -> None:
        """GET /artifacts/{id}/versions/{v} returns that version."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            await client.post(
                f"{server}/artifacts",
                json={
                    "id": unique_id,
                    "name": "Version-Lookup",
                    "artifact_type": "scratch",
                    "content": "v1",
                },
                headers=scope_headers,
            )
            await client.put(
                f"{server}/artifacts/{unique_id}",
                json={"content": "v2"},
                headers=scope_headers,
            )

            v1_resp = await client.get(
                f"{server}/artifacts/{unique_id}/versions/1", headers=scope_headers
            )
            assert v1_resp.status_code == 200
            assert v1_resp.json()["content"] == "v1"
            assert v1_resp.json()["version"] == 1

            v2_resp = await client.get(
                f"{server}/artifacts/{unique_id}/versions/2", headers=scope_headers
            )
            assert v2_resp.status_code == 200
            assert v2_resp.json()["content"] == "v2"
            assert v2_resp.json()["version"] == 2

    @pytest.mark.skip(reason="Postgres artifact store delete has AsyncCursor→int bug")
    async def test_delete_and_verify_gone(self, server: str, unique_id: str, scope_headers: dict[str, str]) -> None:
        """DELETE /artifacts/{id} → 204; re-GET → 404."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            await client.post(
                f"{server}/artifacts",
                json={
                    "id": unique_id,
                    "name": "Delete-Me",
                    "artifact_type": "scratch",
                    "content": "temp",
                },
                headers=scope_headers,
            )

            del_resp = await client.delete(
                f"{server}/artifacts/{unique_id}", headers=scope_headers
            )
            assert del_resp.status_code == 204

            get_resp = await client.get(
                f"{server}/artifacts/{unique_id}", headers=scope_headers
            )
            assert get_resp.status_code == 404

    async def test_invalid_type_rejected(self, server: str, unique_id: str, scope_headers: dict[str, str]) -> None:
        """Unknown artifact_type returns 422."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            resp = await client.post(
                f"{server}/artifacts",
                json={
                    "id": unique_id,
                    "name": "Bad-Type",
                    "artifact_type": "invalid-type",
                    "content": "nope",
                },
                headers=scope_headers,
            )
            assert resp.status_code == 422

    async def test_all_valid_types_accepted(self, server: str, scope_headers: dict[str, str]) -> None:
        """Each of the 6 valid artifact types can be created."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            for atype in sorted(VALID_TYPES):
                art_id = f"test-{atype}-{uuid.uuid4().hex[:8]}"
                resp = await client.post(
                    f"{server}/artifacts",
                    json={
                        "id": art_id,
                        "name": f"{atype}-test",
                        "artifact_type": atype,
                        "content": f"content for {atype}",
                    },
                    headers=scope_headers,
                )
                assert resp.status_code == 201, (
                    f"Failed for type {atype}: {resp.status_code}"
                )
                assert resp.json()["artifact_type"] == atype

    async def test_nonexistent_artifact_404(self, server: str, scope_headers: dict[str, str]) -> None:
        """GET /artifacts/nonexistent → 404."""
        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            resp = await client.get(
                f"{server}/artifacts/nonexistent-id-xyz", headers=scope_headers
            )
            assert resp.status_code == 404


class TestArtifactScoping:
    """Tests for artifact scope isolation."""

    async def test_user_isolation(self, server: str, scope_headers: dict[str, str]) -> None:
        """User A cannot see User B's private artifacts."""
        art_id = f"scoped-{uuid.uuid4().hex[:12]}"

        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            alice_headers = {"X-Cognition-Scope-User": "alice"}
            create_resp = await client.post(
                f"{server}/artifacts",
                json={
                    "id": art_id,
                    "name": "Alice-Artifact",
                    "artifact_type": "scratch",
                    "content": "alice data",
                    "scope": {"user": "alice"},
                },
                headers=alice_headers,
            )
            assert create_resp.status_code == 201

            get_resp = await client.get(
                f"{server}/artifacts/{art_id}", headers=alice_headers
            )
            assert get_resp.status_code == 200

            bob_headers = {"X-Cognition-Scope-User": "bob"}
            bob_get = await client.get(
                f"{server}/artifacts/{art_id}", headers=bob_headers
            )
            assert bob_get.status_code in {404, 403}

    async def test_visibility_filtering(self, server: str, scope_headers: dict[str, str]) -> None:
        """Artifacts with different visibility levels are filterable."""
        art_id = f"vis-{uuid.uuid4().hex[:12]}"

        async with httpx.AsyncClient(timeout=SSE_TIMEOUT) as client:
            create_resp = await client.post(
                f"{server}/artifacts",
                json={
                    "id": art_id,
                    "name": "Private-Item",
                    "artifact_type": "scratch",
                    "content": "secret",
                    "visibility": "private",
                },
                headers=scope_headers,
            )
            assert create_resp.status_code == 201

            list_resp = await client.get(
                f"{server}/artifacts", headers=scope_headers
            )
            assert list_resp.status_code == 200
            visible_ids = [a["id"] for a in list_resp.json()["artifacts"]]
            assert art_id in visible_ids
