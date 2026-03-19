"""Provider bootstrap from config.yaml.

Seeds ``ProviderConfig`` entries into the ConfigRegistry on startup
from the ``llm:`` section of ``.cognition/config.yaml``.  Uses
``seed_if_absent`` semantics: YAML provides defaults, API rows always
win.

Architecture: Layer 1 (Foundation) — startup-only, runs once during
the ``main.py`` lifespan before the server begins accepting requests.
"""

from __future__ import annotations

from typing import Any

import structlog

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
) -> bool:
    """Seed a ProviderConfig from the ``llm:`` section of config.yaml.

    Reads ``config["llm"]`` and constructs a ``ProviderConfig`` entry
    with ``id="default"``, ``scope={}``, ``source="file"``.  Uses
    ``seed_if_absent`` so an existing API-written provider with the
    same ID is never overwritten.

    Args:
        config: The merged YAML config dict from ``load_config()``.

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
        from server.app.storage.config_registry import get_config_registry

        registry = get_config_registry()
        inserted = await registry.seed_if_absent(
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
        return inserted

    except Exception as exc:
        logger.warning(
            "Failed to seed provider from config.yaml",
            error=str(exc),
            provider=provider_type,
            model=model,
        )
        return False
