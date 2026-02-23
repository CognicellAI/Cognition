"""Config API routes.

REST endpoints for server configuration.
"""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import structlog
import yaml
from fastapi import APIRouter, Depends, HTTPException, Request

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

# Allowed config fields for PATCH (security whitelist)
ALLOWED_CONFIG_PATHS = {
    "llm.temperature",
    "llm.max_tokens",
    "agent.memory",
    "agent.skills",
    "rate_limit.per_minute",
    "rate_limit.burst",
    "observability.otel_enabled",
    "observability.metrics_port",
}


def check_config_auth(request: Request, settings: Settings) -> None:
    """Check if request has authorization to modify config.

    Uses scope-based authorization. Requires scoping to be enabled
    and the request to have the configured scope key with the
    configured value.

    Args:
        request: The incoming request
        settings: Application settings

    Raises:
        HTTPException: If scoping is disabled or authorization fails
    """
    if not settings.scoping_enabled:
        raise HTTPException(
            status_code=403,
            detail="Config changes require scoping to be enabled",
        )

    header_name = f"X-Cognition-Scope-{settings.config_auth_scope_key}"
    scope_value = request.headers.get(header_name)

    if scope_value != settings.config_auth_scope_value:
        raise HTTPException(
            status_code=403,
            detail=f"Requires {settings.config_auth_scope_key}={settings.config_auth_scope_value}",
        )


def validate_and_extract_changes(
    updates: ConfigUpdateRequest,
) -> dict[str, Any]:
    """Validate request and extract allowed changes.

    Args:
        updates: The update request

    Returns:
        Dictionary of validated changes

    Raises:
        HTTPException: If validation fails
    """
    changes = {}

    if updates.llm:
        for key, value in updates.llm.items():
            path = f"llm.{key}"
            if path not in ALLOWED_CONFIG_PATHS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Field '{path}' is not allowed to be updated",
                )
            changes[path] = value

            # Validate ranges
            if key == "temperature" and not (0 <= value <= 2):
                raise HTTPException(
                    status_code=422,
                    detail="temperature must be between 0 and 2",
                )
            if key == "max_tokens" and not (1 <= value <= 100000):
                raise HTTPException(
                    status_code=422,
                    detail="max_tokens must be between 1 and 100000",
                )

    if updates.agent:
        for key, value in updates.agent.items():
            path = f"agent.{key}"
            if path not in ALLOWED_CONFIG_PATHS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Field '{path}' is not allowed to be updated",
                )
            changes[path] = value

    if updates.rate_limit:
        for key, value in updates.rate_limit.items():
            path = f"rate_limit.{key}"
            if path not in ALLOWED_CONFIG_PATHS:
                raise HTTPException(
                    status_code=422,
                    detail=f"Field '{path}' is not allowed to be updated",
                )
            changes[path] = value

            # Validate positive values
            if value < 0:
                raise HTTPException(
                    status_code=422,
                    detail=f"{key} must be non-negative",
                )

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
    """Merge changes into current config.

    Args:
        current: Current configuration
        changes: Changes to apply

    Returns:
        Updated configuration
    """
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
    """Save configuration to YAML file.

    Args:
        config: Configuration dictionary
        config_path: Path to save to
    """
    with open(config_path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=False)


@router.get(
    "",
    response_model=ConfigResponse,
)
async def get_config(
    settings: Settings = Depends(get_settings),
) -> ConfigResponse:
    """Get server configuration.

    Returns the merged configuration from YAML files and environment variables.
    Sanitized - no secrets included.
    Includes dynamic model discovery.
    """
    # Load merged config from YAML files
    yaml_config = load_config()

    # Probe for live models
    discovery = DiscoveryEngine(settings)
    discovered = await discovery.discover_models()

    # Merge with Settings (env vars take precedence)
    return ConfigResponse(
        server={
            "host": yaml_config.get("server", {}).get("host", settings.host),
            "port": yaml_config.get("server", {}).get("port", settings.port),
            "log_level": yaml_config.get("server", {}).get("log_level", settings.log_level),
            "max_sessions": yaml_config.get("server", {}).get(
                "max_sessions", settings.max_sessions
            ),
            "session_timeout_seconds": yaml_config.get("server", {}).get(
                "session_timeout_seconds", settings.session_timeout_seconds
            ),
            "scoping_enabled": settings.scoping_enabled,
        },
        llm={
            "provider": yaml_config.get("llm", {}).get("provider", settings.llm_provider),
            "model": yaml_config.get("llm", {}).get("model", settings.llm_model),
            "temperature": yaml_config.get("llm", {}).get("temperature"),
            "max_tokens": yaml_config.get("llm", {}).get("max_tokens"),
            "available_providers": [
                {
                    "id": p_id,
                    "name": p_id.replace("_", " ").title(),
                    "models": [m.id for m in discovered if m.provider_id == p_id],
                }
                for p_id in set(m.provider_id for m in discovered)
            ],
        },
        rate_limit={
            "per_minute": yaml_config.get("rate_limit", {}).get(
                "per_minute", settings.rate_limit_per_minute
            ),
            "burst": yaml_config.get("rate_limit", {}).get("burst", settings.rate_limit_burst),
        },
    )


@router.patch(
    "",
    response_model=ConfigUpdateResponse,
)
async def patch_config(
    request: Request,
    updates: ConfigUpdateRequest,
    settings: Settings = Depends(get_settings),
) -> ConfigUpdateResponse:
    """Update server configuration.

    Requires scoping to be enabled and the request to have the
    configured scope key with the configured value.

    Only specific safe fields can be updated. Protected fields
    (server endpoints, secrets, observability backends) are rejected.

    Creates a backup before applying changes.
    """
    # 1. Check authorization
    check_config_auth(request, settings)

    # 2. Validate and extract changes
    changes = validate_and_extract_changes(updates)

    if not changes:
        raise HTTPException(
            status_code=422,
            detail="No valid changes provided",
        )

    # 3. Load current config
    current_config = load_config()

    # 4. Create backup
    config_path = Path(".cognition/config.yaml")
    backup_path = Path(".cognition/config.yaml.backup")

    if config_path.exists():
        save_config(current_config, backup_path)

    # 5. Merge and save
    new_config = merge_configs(current_config, changes)
    save_config(new_config, config_path)

    # 6. Audit log
    scope_header = f"X-Cognition-Scope-{settings.config_auth_scope_key}"
    scope_value = request.headers.get(scope_header)
    logger.info(
        "config_updated",
        changes=changes,
        scope_key=settings.config_auth_scope_key,
        scope_value=scope_value,
    )

    return ConfigUpdateResponse(
        updated=True,
        changes=changes,
        backup_created=config_path.exists(),
        timestamp=datetime.now(UTC).isoformat(),
    )


@router.post(
    "/rollback",
    response_model=ConfigRollbackResponse,
)
async def rollback_config(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> ConfigRollbackResponse:
    """Rollback configuration to last backup.

    Requires scoping to be enabled and the request to have the
    configured scope key with the configured value.

    Restores from .cognition/config.yaml.backup to .cognition/config.yaml
    """
    # 1. Check authorization
    check_config_auth(request, settings)

    # 2. Check backup exists
    backup_path = Path(".cognition/config.yaml.backup")
    if not backup_path.exists():
        raise HTTPException(
            status_code=404,
            detail="No backup found to rollback to",
        )

    # 3. Load backup and save as current
    with open(backup_path, "r") as f:
        backup_config = yaml.safe_load(f)

    config_path = Path(".cognition/config.yaml")
    save_config(backup_config, config_path)

    # 4. Audit log
    scope_header = f"X-Cognition-Scope-{settings.config_auth_scope_key}"
    scope_value = request.headers.get(scope_header)
    logger.info(
        "config_rolled_back",
        scope_key=settings.config_auth_scope_key,
        scope_value=scope_value,
    )

    return ConfigRollbackResponse(
        rolled_back=True,
        timestamp=datetime.now(UTC).isoformat(),
    )
