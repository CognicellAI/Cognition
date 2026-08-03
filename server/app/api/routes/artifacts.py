"""Artifact management API routes.

Artifacts are durable, scope-aware files that agents and builders can
read, write, list, and diff. They provide explicit state outside the
model context window for long-running agent handoffs.

Route structure maps to the typed artifact categories:
- /artifacts/scratch/    — thread-scoped scratch state
- /artifacts/artifacts/  — user-visible outputs
- /artifacts/files/      — general durable files
- /artifacts/contracts/  — negotiated done criteria
- /artifacts/evals/      — evaluator results
- /artifacts/memories/   — scoped memory
- /artifacts/policies/   — read-only org/project policy
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from server.app.api.dependencies import get_artifact_store, get_scope_dep
from server.app.api.models import (
    ArtifactCreate,
    ArtifactList,
    ArtifactResponse,
    ArtifactUpdate,
    ArtifactVersion,
    ArtifactVersionList,
)
from server.app.api.scoping import SessionScope
from server.app.storage.artifact_store import ArtifactStore

router = APIRouter(prefix="/artifacts", tags=["artifacts"])

logger = structlog.get_logger(__name__)

VALID_ARTIFACT_TYPES = {
    "scratch",
    "artifact",
    "file",
    "contract",
    "eval",
    "memory",
    "policy",
}
VALID_VISIBILITIES = {"private", "run", "public"}


def _to_response(artifact: Any) -> ArtifactResponse:
    return ArtifactResponse(
        id=artifact.id,
        name=artifact.name,
        artifact_type=artifact.artifact_type,
        path=artifact.path,
        content=artifact.content,
        content_type=artifact.content_type,
        version=artifact.version,
        parent_version=artifact.parent_version,
        run_id=artifact.run_id,
        checkpoint_id=artifact.checkpoint_id,
        visibility=artifact.visibility,
        scope=artifact.scope,
        source=artifact.source,
        created_at=artifact.created_at.isoformat() if artifact.created_at else None,
        updated_at=artifact.updated_at.isoformat() if artifact.updated_at else None,
    )


def _to_version(artifact: Any) -> ArtifactVersion:
    return ArtifactVersion(
        version=artifact.version,
        parent_version=artifact.parent_version,
        content=artifact.content,
        content_type=artifact.content_type,
        created_at=artifact.created_at.isoformat() if artifact.created_at else None,
    )


@router.get("", response_model=ArtifactList)
async def list_artifacts(
    artifact_type: str | None = Query(default=None, description="Filter by type"),
    run_id: str | None = Query(default=None, description="Filter by run ID"),
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    artifact_store: ArtifactStore = Depends(get_artifact_store),  # noqa: B008
) -> ArtifactList:
    """List all artifacts visible in the given scope."""
    artifacts = await artifact_store.list_artifacts(
        scope=scope.get_all() or None,
        artifact_type=artifact_type,
        run_id=run_id,
    )
    return ArtifactList(
        artifacts=[_to_response(a) for a in artifacts],
        count=len(artifacts),
    )


@router.get("/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(
    artifact_id: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    artifact_store: ArtifactStore = Depends(get_artifact_store),  # noqa: B008
) -> ArtifactResponse:
    """Get the latest version of an artifact by ID."""
    artifact = await artifact_store.get_artifact(artifact_id, scope=scope.get_all() or None)
    if artifact is None:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found")
    return _to_response(artifact)


@router.post("", response_model=ArtifactResponse, status_code=201)
async def create_artifact(
    body: ArtifactCreate,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    artifact_store: ArtifactStore = Depends(get_artifact_store),  # noqa: B008
) -> ArtifactResponse:
    """Create a new artifact."""
    if body.artifact_type not in VALID_ARTIFACT_TYPES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid artifact_type '{body.artifact_type}'. Must be one of: {sorted(VALID_ARTIFACT_TYPES)}",
        )
    if body.visibility not in VALID_VISIBILITIES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid visibility '{body.visibility}'. Must be one of: {sorted(VALID_VISIBILITIES)}",
        )

    effective_scope = scope.get_all() or body.scope
    now = datetime.now(UTC)

    from server.app.storage.config_models import ArtifactDefinition

    artifact = ArtifactDefinition(
        id=body.id,
        name=body.name,
        artifact_type=body.artifact_type,  # type: ignore[arg-type]
        path=body.path,
        content=body.content,
        content_type=body.content_type,
        version=1,
        parent_version=None,
        run_id=body.run_id,
        checkpoint_id=body.checkpoint_id,
        visibility=body.visibility,  # type: ignore[arg-type]
        scope=effective_scope,
        source="api",
        created_at=now,
        updated_at=now,
    )
    await artifact_store.upsert_artifact(artifact)
    return _to_response(artifact)


@router.put("/{artifact_id}", response_model=ArtifactResponse)
async def update_artifact(
    artifact_id: str,
    body: ArtifactUpdate,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    artifact_store: ArtifactStore = Depends(get_artifact_store),  # noqa: B008
) -> ArtifactResponse:
    """Update an artifact. Content changes create a new version automatically."""
    effective_scope = scope.get_all() or None
    existing = await artifact_store.get_artifact(artifact_id, scope=effective_scope)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found")

    if existing.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"Artifact '{artifact_id}' is file-managed and cannot be modified via API",
        )

    now = datetime.now(UTC)

    from server.app.storage.config_models import ArtifactDefinition

    content_changed = body.content is not None and body.content != existing.content

    updated = ArtifactDefinition(
        id=existing.id,
        name=body.name if body.name is not None else existing.name,
        artifact_type=existing.artifact_type,
        path=existing.path,
        content=body.content if body.content is not None else existing.content,
        content_type=body.content_type if body.content_type is not None else existing.content_type,
        version=existing.version + 1 if content_changed else existing.version,
        parent_version=existing.version if content_changed else existing.parent_version,
        run_id=body.run_id if body.run_id is not None else existing.run_id,
        checkpoint_id=body.checkpoint_id if body.checkpoint_id is not None else existing.checkpoint_id,
        visibility=body.visibility if body.visibility is not None else existing.visibility,  # type: ignore[arg-type]
        scope=existing.scope,
        source=existing.source,
        created_at=existing.created_at,
        updated_at=now,
    )
    await artifact_store.upsert_artifact(updated)
    return _to_response(updated)


@router.delete("/{artifact_id}", status_code=204)
async def delete_artifact(
    artifact_id: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    artifact_store: ArtifactStore = Depends(get_artifact_store),  # noqa: B008
) -> None:
    """Delete an artifact and all its versions."""
    deleted = await artifact_store.delete_artifact(artifact_id, scope=scope.get_all() or None)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Artifact '{artifact_id}' not found")


@router.get("/{artifact_id}/versions", response_model=ArtifactVersionList)
async def list_versions(
    artifact_id: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    artifact_store: ArtifactStore = Depends(get_artifact_store),  # noqa: B008
) -> ArtifactVersionList:
    """List all versions of an artifact, ordered by version descending."""
    versions = await artifact_store.list_artifact_versions(
        artifact_id, scope=scope.get_all() or None
    )
    return ArtifactVersionList(
        artifact_id=artifact_id,
        versions=[_to_version(v) for v in versions],
        count=len(versions),
    )


@router.get("/{artifact_id}/versions/{version}", response_model=ArtifactResponse)
async def get_version(
    artifact_id: str,
    version: int,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    artifact_store: ArtifactStore = Depends(get_artifact_store),  # noqa: B008
) -> ArtifactResponse:
    """Get a specific version of an artifact."""
    artifact = await artifact_store.get_artifact_version(
        artifact_id, version, scope=scope.get_all() or None
    )
    if artifact is None:
        raise HTTPException(
            status_code=404,
            detail=f"Version {version} of artifact '{artifact_id}' not found",
        )
    return _to_response(artifact)
