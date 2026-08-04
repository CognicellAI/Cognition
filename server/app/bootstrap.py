"""Config bootstrap from config.yaml and workspace sources.

Seeds ``ProviderConfig`` entries into the ConfigStore on startup from the
``llm:`` section of ``.cognition/config.yaml``. Uses ``seed_if_absent``
semantics: YAML provides defaults, API rows always win.

Architecture: Layer 1 (Foundation) — startup-only, runs once during
the ``main.py`` lifespan before the server begins accepting requests.
"""

from __future__ import annotations

from typing import Any

import structlog

from server.app.storage.config_models import SandboxProfile

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Static mapping: provider type → default api_key_env
# ---------------------------------------------------------------------------

_PROVIDER_TYPE_TO_DEFAULT_API_KEY_ENV: dict[str, str | None] = {
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "openai_compatible": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
    "google_genai": "GOOGLE_API_KEY",
    "google_vertexai": None,  # uses ADC, no key
    "bedrock": None,  # uses IAM, no key
    "mock": None,
}


def _infer_api_key_env(provider_type: str) -> str | None:
    """Return the conventional api_key_env for a provider type."""
    return _PROVIDER_TYPE_TO_DEFAULT_API_KEY_ENV.get(provider_type)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def seed_providers_from_config(
    config: dict[str, Any],
    config_store: Any,
) -> bool:
    """Seed a ProviderConfig from the ``llm:`` section of config.yaml.

    Reads ``config["llm"]`` and constructs a ``ProviderConfig`` entry
    with ``id="default"``, ``scope={}``, ``source="file"``.  Uses
    ``seed_if_absent`` so an existing API-written provider with the
    same ID is never overwritten.

    Args:
        config: The merged YAML config dict from ``load_config()``.
        config_store: Unified config persistence interface.

    Returns:
        True if a provider was seeded, False if skipped (already exists,
        missing ``llm:`` section, or missing required fields).
    """
    llm = config.get("llm")
    if not isinstance(llm, dict):
        logger.debug("No llm section in config.yaml — skipping provider bootstrap")
        return False

    provider_type = llm.get("provider")
    model = llm.get("model")

    if not provider_type or not model:
        logger.debug(
            "llm section missing provider or model — skipping provider bootstrap",
            provider=provider_type,
            model=model,
        )
        return False

    # Skip the mock provider — it's test-only
    if provider_type == "mock":
        logger.debug("llm.provider is 'mock' — skipping provider bootstrap")
        return False

    # Build the definition dict for seed_if_absent
    api_key_env = llm.get("api_key_env") or _infer_api_key_env(provider_type)
    base_url = llm.get("base_url")
    region = llm.get("region")
    role_arn = llm.get("role_arn")

    definition: dict[str, Any] = {
        "id": "default",
        "provider": provider_type,
        "model": model,
        "display_name": f"Default ({provider_type})",
        "enabled": True,
        "priority": 0,
        "max_retries": 2,
        "scope": {},
        "source": "file",
    }
    if api_key_env:
        definition["api_key_env"] = api_key_env
    if base_url:
        definition["base_url"] = base_url
    if region:
        definition["region"] = region
    if role_arn:
        definition["role_arn"] = role_arn

    try:
        inserted = await config_store.seed_if_absent(
            entity_type="provider",
            name="default",
            scope={},
            definition=definition,
            source="file",
        )

        if inserted:
            logger.info(
                "Provider seeded from config.yaml",
                provider=provider_type,
                model=model,
                base_url=base_url,
                api_key_env=api_key_env,
            )
        else:
            logger.debug(
                "Provider 'default' already exists — config.yaml bootstrap skipped",
                provider=provider_type,
                model=model,
            )
        return bool(inserted)

    except Exception as exc:
        logger.warning(
            "Failed to seed provider from config.yaml",
            error=str(exc),
            provider=provider_type,
            model=model,
        )
        return False


async def seed_sandbox_profiles_from_config(
    config: dict[str, Any],
    config_store: Any,
) -> int:
    """Seed SandboxProfile entries from the ``sandbox_profiles`` config section."""
    raw_profiles = config.get("sandbox_profiles")
    if raw_profiles is None:
        logger.debug("No sandbox_profiles section in config.yaml — skipping bootstrap")
        return 0

    if isinstance(raw_profiles, dict):
        profile_items = []
        for name, value in raw_profiles.items():
            if isinstance(value, dict):
                profile_items.append({"name": name, **value})
    elif isinstance(raw_profiles, list):
        profile_items = [item for item in raw_profiles if isinstance(item, dict)]
    else:
        logger.warning(
            "sandbox_profiles section must be a mapping or list",
            type=type(raw_profiles).__name__,
        )
        return 0

    inserted = 0
    for item in profile_items:
        try:
            profile = SandboxProfile.model_validate({**item, "source": "file"})
        except Exception as exc:
            logger.warning(
                "Invalid sandbox profile in config.yaml",
                profile=item.get("name"),
                error=str(exc),
            )
            continue

        did_insert = await config_store.seed_if_absent(
            entity_type="sandbox_profile",
            name=profile.name,
            scope=profile.scope,
            definition=profile.model_dump(),
            source="file",
        )
        if did_insert:
            inserted += 1
            logger.info(
                "Sandbox profile seeded from config.yaml",
                profile=profile.name,
                backend=profile.backend,
            )

    return inserted
