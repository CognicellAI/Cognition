"""Sandbox profile management API routes."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import ValidationError

from server.app.api.dependencies import get_config_store, get_scope_dep
from server.app.api.models import (
    SandboxProfileCreate,
    SandboxProfileList,
    SandboxProfileResponse,
    SandboxProfileUpdate,
)
from server.app.api.scoping import SessionScope
from server.app.storage.config_models import SandboxProfile
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/sandbox/profiles", tags=["sandbox"])


def _to_response(profile: SandboxProfile) -> SandboxProfileResponse:
    return SandboxProfileResponse(
        name=profile.name,
        backend=profile.backend,
        image_arn=profile.image_arn,
        image_version=profile.image_version,
        region=profile.region,
        ingress_network_connector_arns=list(profile.ingress_network_connector_arns),
        egress_mode=profile.egress_mode,
        egress_network_connector_arns=list(profile.egress_network_connector_arns),
        idle_policy=profile.idle_policy,
        maximum_duration_seconds=profile.maximum_duration_seconds,
        port=profile.port,
        token_expiration_minutes=profile.token_expiration_minutes,
        default_execution_role_arn=profile.default_execution_role_arn,
        scope=dict(profile.scope),
        source=profile.source,
        extra=dict(profile.extra),
    )


@router.get("", response_model=SandboxProfileList)
async def list_sandbox_profiles(
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> SandboxProfileList:
    """List sandbox profiles visible in the current scope."""
    profiles = await config_store.list_sandbox_profiles(scope=scope.get_all() or None)
    responses = [_to_response(profile) for profile in profiles]
    return SandboxProfileList(profiles=responses, count=len(responses))


@router.post("", response_model=SandboxProfileResponse, status_code=201)
async def register_sandbox_profile(
    body: SandboxProfileCreate,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> SandboxProfileResponse:
    """Register or replace a sandbox profile in the current scope."""
    effective_scope = scope.get_all() or body.scope or {}
    existing = await config_store.get_sandbox_profile(body.name, effective_scope or None)
    if existing is not None and existing.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"Sandbox profile '{body.name}' is file-managed and cannot be modified via API",
        )

    try:
        profile = SandboxProfile(
            name=body.name,
            backend=body.backend,
            image_arn=body.image_arn,
            image_version=body.image_version,
            region=body.region,
            ingress_network_connector_arns=body.ingress_network_connector_arns,
            egress_mode=body.egress_mode,
            egress_network_connector_arns=body.egress_network_connector_arns,
            idle_policy=body.idle_policy,
            maximum_duration_seconds=body.maximum_duration_seconds,
            port=body.port,
            token_expiration_minutes=body.token_expiration_minutes,
            default_execution_role_arn=body.default_execution_role_arn,
            scope=effective_scope,
            source="api",
            extra=body.extra,
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await config_store.upsert_sandbox_profile(profile)
    return _to_response(profile)


@router.get("/{name}", response_model=SandboxProfileResponse)
async def get_sandbox_profile(
    name: str,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> SandboxProfileResponse:
    """Get a sandbox profile visible in the current scope."""
    profile = await config_store.get_sandbox_profile(name, scope.get_all() or None)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"Sandbox profile '{name}' not found")
    return _to_response(profile)


@router.patch("/{name}", response_model=SandboxProfileResponse)
async def update_sandbox_profile(
    name: str,
    body: SandboxProfileUpdate,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> SandboxProfileResponse:
    """Partially update an API-registered sandbox profile."""
    effective_scope = scope.get_all() or None
    existing = await config_store.get_sandbox_profile(name, effective_scope)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Sandbox profile '{name}' not found")
    if existing.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"Sandbox profile '{name}' is file-managed and cannot be modified via API",
        )

    updates = body.model_dump(exclude_unset=True)
    try:
        profile = SandboxProfile(
            name=existing.name,
            backend=existing.backend,
            image_arn=updates.get("image_arn", existing.image_arn),
            image_version=updates.get("image_version", existing.image_version),
            region=updates.get("region", existing.region),
            ingress_network_connector_arns=updates.get(
                "ingress_network_connector_arns",
                existing.ingress_network_connector_arns,
            ),
            egress_mode=updates.get("egress_mode", existing.egress_mode),
            egress_network_connector_arns=updates.get(
                "egress_network_connector_arns",
                existing.egress_network_connector_arns,
            ),
            idle_policy=updates.get("idle_policy", existing.idle_policy),
            maximum_duration_seconds=updates.get(
                "maximum_duration_seconds",
                existing.maximum_duration_seconds,
            ),
            port=updates.get("port", existing.port),
            token_expiration_minutes=updates.get(
                "token_expiration_minutes",
                existing.token_expiration_minutes,
            ),
            default_execution_role_arn=updates.get(
                "default_execution_role_arn",
                existing.default_execution_role_arn,
            ),
            scope=existing.scope,
            source=existing.source,
            extra=updates.get("extra", existing.extra),
        )
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    await config_store.upsert_sandbox_profile(profile)
    return _to_response(profile)


@router.delete("/{name}", status_code=204)
async def delete_sandbox_profile(
    name: str,
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
) -> None:
    """Delete an API-registered sandbox profile from the current scope."""
    effective_scope = scope.get_all() or None
    existing = await config_store.get_sandbox_profile(name, effective_scope)
    if existing is not None and existing.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"Sandbox profile '{name}' is file-managed and cannot be modified via API",
        )
    deleted = await config_store.delete_sandbox_profile(name, scope=effective_scope)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Sandbox profile '{name}' not found")
