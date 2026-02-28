"""Model management API routes."""

from fastapi import APIRouter, HTTPException, Depends

from server.app.api.models import ModelInfo, ModelList
from server.app.llm.discovery import DiscoveryEngine
from server.app.settings import Settings, get_settings

router = APIRouter(prefix="/models", tags=["models"])


@router.get("", response_model=ModelList)
async def list_models(settings: Settings = Depends(get_settings)) -> ModelList:
    """List all available LLM models.

    Returns a flat list of models from all providers, including:
    - id: The model ID to use in PATCH /sessions/{id}
    - provider: The provider name
    - display_name: Human-readable name
    - context_window: Token context window size
    - capabilities: Feature flags (vision, tools, etc.)
    """
    discovery = DiscoveryEngine(settings)
    discovered = await discovery.discover_models()

    models = [
        ModelInfo(
            id=m.id,
            provider=m.provider_id,
            display_name=m.name or m.id,
            context_window=None,
            capabilities=[],
        )
        for m in discovered
    ]

    return ModelList(models=models)


@router.get("/providers/{provider_id}", response_model=ModelList)
async def list_models_by_provider(
    provider_id: str,
    settings: Settings = Depends(get_settings),
) -> ModelList:
    """List models for a specific provider."""
    discovery = DiscoveryEngine(settings)
    discovered = await discovery.discover_models()

    provider_models = [m for m in discovered if m.provider_id == provider_id]

    if not provider_models:
        raise HTTPException(status_code=404, detail=f"No models found for provider '{provider_id}'")

    models = [
        ModelInfo(
            id=m.id,
            provider=m.provider_id,
            display_name=m.name or m.id,
            context_window=None,
            capabilities=[],
        )
        for m in provider_models
    ]

    return ModelList(models=models)
