"""Model management API routes."""

from fastapi import APIRouter, Depends, Header, HTTPException

from server.app.api.models import (
    ModelInfo,
    ModelList,
    ProviderConfigList,
    ProviderCreate,
    ProviderResponse,
    ProviderUpdate,
)
from server.app.llm.discovery import DiscoveryEngine
from server.app.settings import Settings, get_settings

router = APIRouter(prefix="/models", tags=["models"])


def _scope_from_headers(
    user: str | None = Header(None, alias="x-cognition-scope-user"),
    project: str | None = Header(None, alias="x-cognition-scope-project"),
) -> dict[str, str] | None:
    """Extract optional scope dict from request headers."""
    scope: dict[str, str] = {}
    if user:
        scope["user"] = user
    if project:
        scope["project"] = project
    return scope if scope else None


@router.get("", response_model=ModelList)
async def list_models(settings: Settings = Depends(get_settings)) -> ModelList:  # noqa: B008
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


@router.get("/providers", response_model=ProviderConfigList)
async def list_providers(
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> ProviderConfigList:
    """List all provider configs from the ConfigRegistry visible in the given scope."""
    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        providers = await reg.list_providers(scope=scope)
        return ProviderConfigList(
            providers=[
                ProviderResponse(
                    id=p.id,
                    provider=p.provider,
                    model=p.model,
                    display_name=p.display_name,
                    enabled=p.enabled,
                    priority=p.priority,
                    max_retries=p.max_retries,
                    api_key_env=p.api_key_env,
                    base_url=p.base_url,
                    region=p.region,
                    role_arn=p.role_arn,
                    extra=p.extra,
                    scope=p.scope,
                    source=p.source,
                )
                for p in providers
            ],
            count=len(providers),
        )
    except RuntimeError:
        return ProviderConfigList(providers=[], count=0)


@router.get("/providers/{provider_id}", response_model=ModelList)
async def list_models_by_provider(
    provider_id: str,
    settings: Settings = Depends(get_settings),  # noqa: B008
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


@router.post("/providers", response_model=ProviderResponse, status_code=201)
async def create_provider(
    body: ProviderCreate,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> ProviderResponse:
    """Create or replace a provider config in the ConfigRegistry."""
    try:
        from server.app.storage.config_models import ProviderConfig
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        # Header scope overrides body scope if provided
        effective_scope = scope if scope is not None else (body.scope or {})
        provider = ProviderConfig(
            id=body.id,
            provider=body.provider,
            model=body.model,
            display_name=body.display_name,
            enabled=body.enabled,
            priority=body.priority,
            max_retries=body.max_retries,
            api_key_env=body.api_key_env,
            base_url=body.base_url,
            region=body.region,
            role_arn=body.role_arn,
            extra=body.extra,
            scope=effective_scope,
            source="api",
        )
        await reg.upsert_provider(provider)
        return ProviderResponse(
            id=provider.id,
            provider=provider.provider,
            model=provider.model,
            display_name=provider.display_name,
            enabled=provider.enabled,
            priority=provider.priority,
            max_retries=provider.max_retries,
            api_key_env=provider.api_key_env,
            base_url=provider.base_url,
            region=provider.region,
            role_arn=provider.role_arn,
            extra=provider.extra,
            scope=provider.scope,
            source=provider.source,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.patch("/providers/{provider_id}", response_model=ProviderResponse)
async def update_provider(
    provider_id: str,
    body: ProviderUpdate,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> ProviderResponse:
    """Partially update a provider config."""
    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        provider = await reg.get_provider(provider_id, scope=scope)
        if provider is None:
            raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

        updates = body.model_dump(exclude_none=True)
        updated = provider.model_copy(update=updates)
        await reg.upsert_provider(updated)

        return ProviderResponse(
            id=updated.id,
            provider=updated.provider,
            model=updated.model,
            display_name=updated.display_name,
            enabled=updated.enabled,
            priority=updated.priority,
            max_retries=updated.max_retries,
            api_key_env=updated.api_key_env,
            base_url=updated.base_url,
            region=updated.region,
            role_arn=updated.role_arn,
            extra=updated.extra,
            scope=updated.scope,
            source=updated.source,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/providers/{provider_id}", status_code=204)
async def delete_provider(
    provider_id: str,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> None:
    """Delete a provider config from the ConfigRegistry."""
    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        deleted = await reg.delete_provider(provider_id, scope=scope)
        if not deleted:
            raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
