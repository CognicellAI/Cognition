"""Config API routes.

REST endpoints for server configuration.
"""

from fastapi import APIRouter, Depends

from server.app.api.models import ConfigResponse
from server.app.config_loader import ConfigLoader, load_config
from server.app.settings import Settings, get_settings
from server.app.llm.discovery import DiscoveryEngine

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
            "available_providers": [
                {
                    "id": "openai",
                    "name": "OpenAI",
                    "models": ["gpt-4o", "gpt-4o-mini", "o1-preview"],
                },
                {
                    "id": "bedrock",
                    "name": "AWS Bedrock",
                    "models": [
                        "anthropic.claude-3-sonnet-20240229-v1:0",
                        "anthropic.claude-3-opus-20240229-v1:0",
                    ],
                },
                {
                    "id": "openai_compatible",
                    "name": "OpenRouter / Local",
                    "models": ["google/gemini-3-flash-preview", "meta-llama/llama-3-70b"],
                },
                {"id": "mock", "name": "Mock Provider", "models": ["mock-model"]},
            ],
        },
        rate_limit={
            "per_minute": yaml_config.get("rate_limit", {}).get(
                "per_minute", settings.rate_limit_per_minute
            ),
            "burst": yaml_config.get("rate_limit", {}).get("burst", settings.rate_limit_burst),
        },
    )
