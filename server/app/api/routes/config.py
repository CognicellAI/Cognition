"""Config API routes.

REST endpoints for server configuration.

Note: Authentication is the responsibility of the application layer.
Apps should protect these endpoints via middleware, proxy, or dependencies.
Cognition does not enforce auth - it validates inputs and updates config files only.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException

from server.app.api.models import (
    ConfigResponse,
    ConfigRollbackResponse,
    ConfigUpdateRequest,
    ConfigUpdateResponse,
)
from server.app.config_loader import load_config
from server.app.llm.discovery import DiscoveryEngine
from server.app.settings import Settings, get_settings

router = APIRouter(prefix="/config", tags=["config"])

logger = structlog.get_logger(__name__)

# Allowed config fields for PATCH
# Agent/LLM config has moved to ConfigRegistry — only infrastructure fields remain here.
ALLOWED_CONFIG_PATHS = {
    # Rate limiting
    "rate_limit.per_minute",
    "rate_limit.burst",
    # Observability
    "observability.otel_enabled",
    "observability.metrics_port",
    "observability.otel_endpoint",
}


def validate_and_extract_changes(
    updates: ConfigUpdateRequest,
) -> dict[str, Any]:
    """Validate request and extract allowed changes.

    Only rate_limit and observability fields are accepted here.
    Agent/LLM configuration has moved to the ConfigRegistry API
    (PATCH /agents, POST /models/providers, etc.).
    """
    changes = {}

    if updates.rate_limit:
        for key, value in updates.rate_limit.items():
            path = f"rate_limit.{key}"
            if path not in ALLOWED_CONFIG_PATHS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Field '{path}' is not allowed to be updated",
                )
            changes[path] = value

    if updates.observability:
        for key, value in updates.observability.items():
            path = f"observability.{key}"
            if path not in ALLOWED_CONFIG_PATHS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Field '{path}' is not allowed to be updated",
                )
            changes[path] = value

    return changes


def merge_configs(current: dict, changes: dict[str, Any]) -> dict:
    """Merge changes into current config."""
    result = current.copy()

    for path, value in changes.items():
        parts = path.split(".")
        target = result
        for part in parts[:-1]:
            if part not in target:
                target[part] = {}
            target = target[part]
        target[parts[-1]] = value

    return result


def save_config(config: dict, config_path: Path) -> None:
    """Save configuration to YAML file."""
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


@router.get("", response_model=ConfigResponse)
async def get_config(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ConfigResponse:
    """Get server configuration."""
    yaml_config = load_config()
    discovery = DiscoveryEngine(settings)
    discovered = await discovery.discover_models()

    # LLM/provider defaults are now in the ConfigRegistry.
    # GET /config returns infrastructure settings only.
    return ConfigResponse(
        server={
            "host": yaml_config.get("server", {}).get("host", settings.host),
            "port": yaml_config.get("server", {}).get("port", settings.port),
            "log_level": yaml_config.get("server", {}).get("log_level", settings.log_level),
            "scoping_enabled": settings.scoping_enabled,
        },
        llm={
            "available_providers": [
                {
                    "id": p_id,
                    "name": p_id.replace("_", " ").title(),
                    "models": [m.id for m in discovered if m.provider_id == p_id],
                }
                for p_id in {m.provider_id for m in discovered}
            ],
        },
        rate_limit={
            "per_minute": yaml_config.get("rate_limit", {}).get(
                "per_minute", settings.rate_limit_per_minute
            ),
            "burst": yaml_config.get("rate_limit", {}).get("burst", settings.rate_limit_burst),
        },
    )


@router.patch("", response_model=ConfigUpdateResponse)
async def patch_config(
    updates: ConfigUpdateRequest,
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ConfigUpdateResponse:
    """Update server configuration. Apps handle auth via middleware."""
    changes = validate_and_extract_changes(updates)

    if not changes:
        raise HTTPException(status_code=422, detail="No valid changes provided")

    current_config = load_config()
    config_path = Path(".cognition/config.yaml")
    backup_path = Path(".cognition/config.yaml.backup")

    # Ensure .cognition directory exists before writing
    config_path.parent.mkdir(parents=True, exist_ok=True)

    if config_path.exists():
        save_config(current_config, backup_path)

    new_config = merge_configs(current_config, changes)
    save_config(new_config, config_path)

    logger.info("config_updated", changes=changes)

    return ConfigUpdateResponse(
        updated=True,
        changes=changes,
        backup_created=config_path.exists(),
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.post("/rollback", response_model=ConfigRollbackResponse)
async def rollback_config(
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ConfigRollbackResponse:
    """Rollback configuration to last backup. Apps handle auth via middleware."""
    backup_path = Path(".cognition/config.yaml.backup")
    if not backup_path.exists():
        raise HTTPException(status_code=404, detail="No backup found to rollback to")

    with open(backup_path) as f:
        backup_config = yaml.safe_load(f)

    config_path = Path(".cognition/config.yaml")
    # Ensure .cognition directory exists before writing
    config_path.parent.mkdir(parents=True, exist_ok=True)
    save_config(backup_config, config_path)

    logger.info("config_rolled_back")

    return ConfigRollbackResponse(
        rolled_back=True,
        timestamp=datetime.now(UTC).isoformat(),
    )
