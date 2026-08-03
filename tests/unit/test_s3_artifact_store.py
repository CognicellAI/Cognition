"""Tests for database-manifest / S3-body artifact persistence."""

from __future__ import annotations

import pytest
from deepagents.backends.protocol import DeleteResult, FileData, ReadResult, WriteResult

from server.app.storage.artifact_store import MemoryArtifactStore, S3ArtifactStore
from server.app.storage.config_models import ArtifactDefinition


class _FakeObjectBackend:
    def __init__(self) -> None:
        self.objects: dict[str, str] = {}

    def write(self, path: str, content: str) -> WriteResult:
        self.objects[path] = content
        return WriteResult(path=path)

    def read(self, path: str, offset: int = 0, limit: int = 2000) -> ReadResult:
        del offset, limit
        content = self.objects.get(path)
        if content is None:
            return ReadResult(error="missing")
        return ReadResult(file_data=FileData(content=content, encoding="utf-8"))

    def delete(self, path: str) -> DeleteResult:
        self.objects.pop(path, None)
        return DeleteResult(path=path)


@pytest.mark.asyncio
async def test_s3_artifact_store_keeps_body_out_of_database_manifest(monkeypatch: pytest.MonkeyPatch) -> None:
    manifests = MemoryArtifactStore()
    store = S3ArtifactStore(
        manifests,
        bucket="test",
        base_prefix="cognition",
        hmac_key="test-key",
    )
    backend = _FakeObjectBackend()
    monkeypatch.setattr(store, "_backend", lambda scope: backend)
    artifact = ArtifactDefinition(
        id="report",
        name="report",
        artifact_type="artifact",
        content="tenant-safe content",
        scope={"tenant": "acme"},
    )

    await store.upsert_artifact(artifact)

    manifest = await manifests.get_artifact("report", {"tenant": "acme"})
    assert manifest is not None
    assert manifest.content == ""
    assert backend.objects == {"/artifacts/artifact/report/1": "tenant-safe content"}

    hydrated = await store.get_artifact("report", {"tenant": "acme"})
    assert hydrated is not None
    assert hydrated.content == "tenant-safe content"


@pytest.mark.asyncio
async def test_s3_artifact_store_deletes_versioned_bodies(monkeypatch: pytest.MonkeyPatch) -> None:
    manifests = MemoryArtifactStore()
    store = S3ArtifactStore(manifests, bucket="test", base_prefix="cognition", hmac_key="test-key")
    backend = _FakeObjectBackend()
    monkeypatch.setattr(store, "_backend", lambda scope: backend)
    first = ArtifactDefinition(id="report", name="report", content="one", scope={"tenant": "acme"})
    second = first.model_copy(update={"content": "two", "version": 2, "parent_version": 1})

    await store.upsert_artifact(first)
    await store.upsert_artifact(second)
    assert await store.delete_artifact("report", {"tenant": "acme"})
    assert backend.objects == {}
    assert await manifests.get_artifact("report", {"tenant": "acme"}) is None
