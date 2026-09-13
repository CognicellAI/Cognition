"""Immutable binary snapshots backed by existing scoped artifact manifests."""

from __future__ import annotations

import hashlib
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel

from server.app.storage.artifact_store import S3ArtifactStore
from server.app.storage.config_models import ArtifactDefinition
from server.app.telemetry import operation

REFERENCE_PREFIX = "cognition-published:"


class PublishedFile(BaseModel):
    """Durable descriptor; contains no credentials or expiring URLs."""

    id: str
    object_key: str
    checksum: str
    size: int
    filename: str
    media_type: str
    kind: Literal["raw", "url"]


async def publish_file(
    store: S3ArtifactStore,
    scope: dict[str, str],
    body: bytes,
    filename: str,
    media_type: str,
    inline_limit: int,
    *,
    run_id: str | None = None,
) -> PublishedFile:
    """Verify a binary upload before activating its scoped manifest."""
    identity = str(uuid4())
    with operation("cognition.publication.hash"):
        checksum = hashlib.sha256(body).hexdigest()
    key = await store._run_io(
        lambda objects: objects.scoped_key(scope, f"published/{identity}/{checksum}")
    )
    with operation("cognition.publication.upload"):
        await store._run_io(
            lambda objects: objects.put(
                key, body, tags={"cognition:content-class": "published-file"}
            )
        )
    with operation("cognition.publication.verify"):
        uploaded = await store._run_io(lambda objects: objects.get(key))
        if hashlib.sha256(uploaded).hexdigest() != checksum:
            raise RuntimeError("Published file checksum verification failed")
    result = PublishedFile(
        id=identity,
        object_key=key,
        checksum=checksum,
        size=len(body),
        filename=filename,
        media_type=media_type,
        kind="raw" if len(body) <= inline_limit else "url",
    )
    with operation("cognition.publication.manifest"):
        await store.upsert_artifact(
            ArtifactDefinition(
                id="published-" + identity,
                name="published-" + identity,
                artifact_type="file",
                path=f"published/{identity}",
                content=result.model_dump_json(),
                content_type="application/vnd.cognition.published-file+json",
                scope=scope,
                source="api",
                run_id=run_id,
            )
        )
    return result


async def resolve_download(store: S3ArtifactStore, scope: dict[str, str], reference: str) -> str:
    """Mint a download URL only after exact-scope manifest lookup."""
    identity = reference.removeprefix(REFERENCE_PREFIX)
    with operation("cognition.artifact.resolve"):
        manifest = await store.get_artifact("published-" + identity, scope)
    if manifest is None or manifest.content_type != "application/vnd.cognition.published-file+json":
        raise ValueError("Published artifact not found")
    result = PublishedFile.model_validate_json(manifest.content)
    expected_key = await store._run_io(
        lambda objects: objects.scoped_key(scope, f"published/{identity}/{result.checksum}")
    )
    if result.id != identity or result.object_key != expected_key:
        raise ValueError("Published artifact scope or identity mismatch")
    with operation("cognition.artifact.availability"):
        await store._run_io(lambda objects: objects.require_object(result.object_key))
    with operation("cognition.artifact.sign"):
        return await store._run_io(
            lambda objects: objects.download_url(
                result.object_key, result.filename, result.media_type
            )
        )
