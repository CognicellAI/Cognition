"""Config API routes.

REST endpoints for server configuration.
"""

from fastapi import APIRouter, Depends

from server.app.api.models import ConfigResponse
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

    Returns the current server configuration (sanitized, no secrets).
    """
    return ConfigResponse(
        server={
            "host": settings.host,
            "port": settings.port,
            "log_level": settings.log_level,
            "max_sessions": settings.max_sessions,
            "session_timeout_seconds": settings.session_timeout_seconds,
        },
        llm={
            "provider": settings.llm_provider,
            "model": settings.llm_model,
        },
        rate_limit={
            "per_minute": settings.rate_limit_per_minute,
            "burst": settings.rate_limit_burst,
        },
    )
