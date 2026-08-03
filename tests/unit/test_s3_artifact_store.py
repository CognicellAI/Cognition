"""Tests for database-manifest / S3-body artifact persistence."""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest
from deepagents.backends.protocol import (
    DeleteResult,
    FileDownloadResponse,
    ReadResult,
    WriteResult,
)
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import create_async_engine

from server.app.agent.artifacts_backend import ArtifactBackend
from server.app.storage.artifact_store import (
    MemoryArtifactStore,
    S3ArtifactStore,
    SqliteArtifactStore,
)
from server.app.storage.config_models import ArtifactDefinition
from server.app.storage.factory import create_artifact_store
from server.app.storage.schema import create_all_tables


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
        return ReadResult(error="not used by artifact publication")

    def object_key(self, path: str) -> str:
        return f"opaque-scope{path}"

    def verify_connection(self) -> None:
        return None

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        return [
            FileDownloadResponse(
                path=path,
                content=self.objects[path].encode("utf-8") if path in self.objects else None,
                error=None if path in self.objects else "file_not_found",
            )
            for path in paths
        ]

    def delete(self, path: str) -> DeleteResult:
        self.objects.pop(path, None)
        return DeleteResult(path=path)


def test_artifact_store_factory_honors_builder_selected_s3_backend(tmp_path: Path) -> None:
    settings = SimpleNamespace(
        persistence_backend="memory",
        persistence_uri="unused",
        workspace_path=tmp_path,
        s3_enabled=True,
        s3_bucket="durable",
        s3_prefix="cognition",
        s3_scope_hmac_key=SecretStr("test-key"),
        s3_endpoint_url="http://object-store.internal",
        s3_region="test",
        s3_force_path_style=True,
    )

    selected = create_artifact_store(settings)  # type: ignore[arg-type]

    assert isinstance(selected, S3ArtifactStore)


@pytest.mark.asyncio
async def test_s3_artifact_store_initialization_verifies_selected_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = S3ArtifactStore(
        MemoryArtifactStore(),
        bucket="test",
        base_prefix="cognition",
        hmac_key="test-key",
    )
    backend = _FakeObjectBackend()
    monkeypatch.setattr(store, "_backend", lambda scope: backend)
    await store.initialize()

    def unavailable() -> None:
        raise RuntimeError("selected S3 unavailable")

    backend.verify_connection = unavailable  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="selected S3 unavailable"):
        await store.health_check()


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
        path="reports/final.md",
        content="tenant-safe content",
        scope={"tenant": "acme"},
    )

    await store.upsert_artifact(artifact)

    manifest = await manifests.get_artifact("report", {"tenant": "acme"})
    assert manifest is not None
    assert manifest.content == ""
    assert manifest.path == "reports/final.md"
    checksum = hashlib.sha256(b"tenant-safe content").hexdigest()
    path = f"/artifacts/artifact/report/1/{checksum}"
    assert manifest.object_key == f"opaque-scope{path}"
    assert manifest.content_checksum == checksum
    assert manifest.content_size == len(b"tenant-safe content")
    assert backend.objects == {path: "tenant-safe content"}

    hydrated = await store.get_artifact("report", {"tenant": "acme"})
    assert hydrated is not None
    assert hydrated.content == "tenant-safe content"


def test_artifact_manifest_rejects_non_normalized_paths() -> None:
    with pytest.raises(ValueError, match="normalized relative POSIX"):
        ArtifactDefinition(id="report", name="report", path="../other-scope/report")


@pytest.mark.asyncio
async def test_sqlite_manifest_round_trips_durable_object_metadata(tmp_path: Path) -> None:
    database = tmp_path / "artifacts.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    await create_all_tables(engine)
    await engine.dispose()
    store = SqliteArtifactStore(str(database))
    await store.initialize()
    artifact = ArtifactDefinition(
        id="report",
        name="report",
        artifact_type="artifact",
        path="reports/final.md",
        object_key="cognition/scopes/opaque/artifacts/report",
        content_checksum="a" * 64,
        content_size=42,
    )
    try:
        await store.upsert_artifact(artifact)
        persisted = await store.get_artifact("report")
    finally:
        await store.close()

    assert persisted is not None
    assert persisted.path == artifact.path
    assert persisted.object_key == artifact.object_key
    assert persisted.content_checksum == artifact.content_checksum
    assert persisted.content_size == artifact.content_size


@pytest.mark.asyncio
async def test_general_files_route_uses_durable_artifact_store() -> None:
    manifests = MemoryArtifactStore()
    backend = ArtifactBackend(manifests, scope={"tenant": "acme"})

    write = await backend.awrite("/files/report.md", "durable file")

    assert write.error is None
    persisted = await manifests.get_artifact("report.md", {"tenant": "acme"})
    assert persisted is not None
    assert persisted.artifact_type == "file"
    read = await backend.aread("/files/report.md")
    assert read.file_data == {"content": "durable file", "encoding": "utf-8"}


@pytest.mark.asyncio
async def test_s3_artifact_store_does_not_activate_corrupt_upload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifests = MemoryArtifactStore()
    store = S3ArtifactStore(
        manifests,
        bucket="test",
        base_prefix="cognition",
        hmac_key="test-key",
    )
    backend = _FakeObjectBackend()
    original_download = backend.download_files

    def corrupt_download(paths: list[str]) -> list[FileDownloadResponse]:
        responses = original_download(paths)
        responses[0].content = b"corrupt"
        return responses

    backend.download_files = corrupt_download  # type: ignore[method-assign]
    monkeypatch.setattr(store, "_backend", lambda scope: backend)

    with pytest.raises(RuntimeError, match="post-upload integrity"):
        await store.upsert_artifact(
            ArtifactDefinition(id="report", name="report", content="expected")
        )

    assert await manifests.get_artifact("report") is None


@pytest.mark.asyncio
async def test_s3_artifact_store_detects_corrupt_body_on_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifests = MemoryArtifactStore()
    store = S3ArtifactStore(
        manifests,
        bucket="test",
        base_prefix="cognition",
        hmac_key="test-key",
    )
    backend = _FakeObjectBackend()
    monkeypatch.setattr(store, "_backend", lambda scope: backend)
    artifact = ArtifactDefinition(id="report", name="report", content="expected")
    await store.upsert_artifact(artifact)
    path = next(iter(backend.objects))
    backend.objects[path] = "tampered"

    with pytest.raises(RuntimeError, match="integrity verification"):
        await store.get_artifact("report")


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
