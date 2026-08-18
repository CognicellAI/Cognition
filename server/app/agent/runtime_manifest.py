"""Pinned, redacted runtime identity for one Agent execution."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from server.app.agent.resolver import RuntimeResolver
from server.app.exceptions import LLMProviderConfigError
from server.app.observability import (
    RUNTIME_MANIFEST_RESOLUTION_DURATION,
    RUNTIME_MANIFEST_RESOLUTIONS_TOTAL,
)
from server.app.settings import Settings
from server.app.storage.common import canonical_json_digest, effective_scope_key
from server.app.storage.config_store import ConfigStore


@dataclass(frozen=True)
class ResolvedRuntimeManifest:
    """A safe dependency manifest resolved exactly once before a run."""

    agent_revision: int
    manifest: dict[str, Any]
    digest: str


async def resolve_runtime_manifest(
    *,
    config_store: ConfigStore,
    settings: Settings,
    agent_name: str,
    effective_scope: dict[str, str],
    session: Any | None = None,
) -> ResolvedRuntimeManifest:
    """Resolve safe runtime identities without persisting credentials or URLs."""
    started_at = time.monotonic()
    record = await config_store.get_agent_record(agent_name, effective_scope)
    definition = await config_store.get_agent_definition(agent_name, effective_scope)
    if definition is None:
        RUNTIME_MANIFEST_RESOLUTIONS_TOTAL.labels(outcome="not_found").inc()
        RUNTIME_MANIFEST_RESOLUTION_DURATION.labels(outcome="not_found").observe(
            time.monotonic() - started_at
        )
        raise ValueError(f"Agent '{agent_name}' is not provisioned at this scope")

    validated_definition = definition.model_dump(mode="json")
    agent_revision = record.revision if record is not None else 1
    agent_digest = (
        record.definition_digest
        if record is not None
        else canonical_json_digest(validated_definition)
    )

    resolver = RuntimeResolver(config_store, settings)
    try:
        target = await resolver.select_model_target_for_session(
            session=session,
            scope=effective_scope,
            agent_def=definition,
        )
        resolved_model = resolver.resolve_model_config_for_target(
            target,
            session=session,
            agent_def=definition,
        )
        provider_identity: dict[str, Any] | None = {
            "target": target.to_safe_manifest(),
            "resolved": {
                "provider": resolved_model.provider,
                "model_id": resolved_model.model_id,
                "base_url": resolved_model.base_url,
                "region": resolved_model.region,
                "role_arn": resolved_model.role_arn,
                "max_retries": resolved_model.max_retries,
                "timeout": resolved_model.timeout,
            },
            "recursion_limit": resolved_model.recursion_limit,
            "temperature": resolved_model.temperature,
            "max_tokens": resolved_model.max_tokens,
        }
        if target.provider_id:
            assert provider_identity is not None
            provider = await config_store.get_provider(
                target.provider_id,
                effective_scope,
            )
            if provider is not None:
                provider_identity["id"] = provider.id
                provider_identity["digest"] = canonical_json_digest(
                    provider.model_dump(mode="json")
                )
    except LLMProviderConfigError:
        provider_identity = None

    sandbox_profile_identity: dict[str, Any] | None = None
    selected_sandbox_profile = (
        definition.config.sandbox_profile or settings.aws_lambda_microvm_default_profile
    )
    if selected_sandbox_profile:
        profile = await config_store.get_sandbox_profile(
            selected_sandbox_profile,
            effective_scope,
        )
        if profile is not None:
            profile_definition = profile.model_dump(mode="json")
            sandbox_profile_identity = {
                "name": profile.name,
                "digest": canonical_json_digest(profile_definition),
                "definition": profile_definition,
            }

    manifest: dict[str, Any] = {
        "scope_key": effective_scope_key(effective_scope),
        "agent": {
            "name": agent_name,
            "revision": agent_revision,
            "definition_digest": agent_digest,
            "source": record.source if record is not None else "file",
            # Agent definitions do not contain resolved credentials. Persisting
            # the validated snapshot makes the revision executable after a
            # concurrent replacement without consulting mutable current state.
            "definition": validated_definition,
        },
        "dependencies": {
            "provider": provider_identity,
            "sandbox_profile": sandbox_profile_identity,
        },
        "backend_identity": {
            "sandbox_backend": settings.sandbox_backend,
        },
    }
    if definition.a2a.a2ui is not None:
        from server.app.protocols.a2a.a2ui.core import pinned_asset_manifest

        manifest["dependencies"]["a2ui"] = pinned_asset_manifest()
    result = ResolvedRuntimeManifest(
        agent_revision=agent_revision,
        manifest=manifest,
        digest=canonical_json_digest(manifest),
    )
    RUNTIME_MANIFEST_RESOLUTIONS_TOTAL.labels(outcome="success").inc()
    RUNTIME_MANIFEST_RESOLUTION_DURATION.labels(outcome="success").observe(
        time.monotonic() - started_at
    )
    return result


__all__ = ["ResolvedRuntimeManifest", "resolve_runtime_manifest"]
