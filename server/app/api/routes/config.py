"""Config API routes.

REST endpoints for server configuration.
"""

from fastapi import APIRouter, Depends

from server.app.api.models import ConfigResponse
from server.app.config_loader import ConfigLoader, load_config
from server.app.settings import Settings, get_settings

router = APIRouter(prefix="/config", tags=["config"])


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
    """
    # Load merged config from YAML files
    yaml_config = load_config()

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
        },
        llm={
            "provider": yaml_config.get("llm", {}).get("provider", settings.llm_provider),
            "model": yaml_config.get("llm", {}).get("model", settings.llm_model),
            "temperature": yaml_config.get("llm", {}).get("temperature"),
            "max_tokens": yaml_config.get("llm", {}).get("max_tokens"),
            # Note: system_prompt not included to keep response concise
        },
        rate_limit={
            "per_minute": yaml_config.get("rate_limit", {}).get(
                "per_minute", settings.rate_limit_per_minute
            ),
            "burst": yaml_config.get("rate_limit", {}).get("burst", settings.rate_limit_burst),
        },
    )
