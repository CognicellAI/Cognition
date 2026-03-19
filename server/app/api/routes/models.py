"""Model management API routes.

Provides endpoints for browsing the model catalog, managing provider
configurations, and testing provider connectivity.

Model catalog data comes from models.dev (configurable via
``COGNITION_MODEL_CATALOG_URL``). The catalog is enrichment only —
if it is unreachable, endpoints degrade gracefully.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from server.app.api.models import (
    ModelInfo,
    ModelList,
    ProviderConfigList,
    ProviderCreate,
    ProviderResponse,
    ProviderTestResponse,
    ProviderUpdate,
)
from server.app.settings import Settings, get_settings

if TYPE_CHECKING:
    from server.app.llm.model_catalog import CatalogModel

router = APIRouter(prefix="/models", tags=["models"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _catalog_model_to_info(
    m: CatalogModel,
    provider_label: str | None = None,
) -> ModelInfo:
    """Convert a CatalogModel to a ModelInfo API response."""
    capabilities: list[str] = []
    if m.tool_call:
        capabilities.append("tool_call")
    if m.reasoning:
        capabilities.append("reasoning")
    if m.structured_output:
        capabilities.append("structured_output")
    if m.modalities:
        input_modes = m.modalities.get("input", [])
        if "image" in input_modes:
            capabilities.append("vision")
        if "audio" in input_modes:
            capabilities.append("audio_input")
        if "pdf" in input_modes:
            capabilities.append("pdf_input")
        output_modes = m.modalities.get("output", [])
        if "image" in output_modes:
            capabilities.append("image_output")
        if "audio" in output_modes:
            capabilities.append("audio_output")

    return ModelInfo(
        id=m.id,
        provider=provider_label or m.provider_slug,
        display_name=m.name,
        context_window=m.context_window or None,
        output_limit=m.output_limit or None,
        capabilities=capabilities,
        input_cost=m.input_cost if m.input_cost > 0 else None,
        output_cost=m.output_cost if m.output_cost > 0 else None,
        modalities=m.modalities or None,
        family=m.family or None,
        status=m.status,
    )


# ---------------------------------------------------------------------------
# Model catalog endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=ModelList)
async def list_models(
    provider: str | None = Query(
        None,
        description="Filter by Cognition provider type (e.g., 'openai', 'anthropic')",
    ),
    tool_call: bool | None = Query(None, description="Filter by tool call support"),
    q: str | None = Query(None, description="Search by model name or ID"),
) -> ModelList:
    """List models from the catalog, optionally filtered.

    Returns models from the models.dev catalog for all known provider types.
    Use query parameters to filter by provider, tool call support, or search.

    If the catalog is unreachable, returns an empty list.
    """
    from server.app.llm.model_catalog import get_model_catalog

    catalog = get_model_catalog()

    if provider:
        # Filter by specific Cognition provider type
        catalog_models = await catalog.get_models_for_cognition_provider(provider)
        if q or tool_call is not None:
            catalog_models = [
                m
                for m in catalog_models
                if (q is None or q.lower() in m.id.lower() or q.lower() in m.name.lower())
                and (tool_call is None or m.tool_call == tool_call)
            ]
    elif q or tool_call is not None:
        catalog_models = await catalog.search(query=q, tool_call=tool_call)
    else:
        # Return models from all mapped Cognition provider types
        from server.app.llm.model_catalog import PROVIDER_TYPE_TO_CATALOG_SLUGS

        catalog_models = []
        for ptype in PROVIDER_TYPE_TO_CATALOG_SLUGS:
            catalog_models.extend(await catalog.get_models_for_cognition_provider(ptype))

    models = [_catalog_model_to_info(m) for m in catalog_models]
    return ModelList(models=models)


# ---------------------------------------------------------------------------
# Provider config CRUD endpoints
# ---------------------------------------------------------------------------


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
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"ConfigRegistry unavailable: {e}",
        ) from e


@router.get("/providers/{provider_id}/models", response_model=ModelList)
async def list_models_for_provider(
    provider_id: str,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> ModelList:
    """List catalog models available for a specific provider config.

    Looks up the ProviderConfig by ``provider_id``, maps its provider type
    to the models.dev catalog, and returns enriched model metadata.

    For ``openai_compatible`` providers, returns an empty model list since
    the available models depend on the upstream service (OpenRouter, vLLM, etc.).
    """
    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"ConfigRegistry unavailable: {e}",
        ) from e

    provider_config = await reg.get_provider(provider_id, scope=scope)
    if provider_config is None:
        raise HTTPException(
            status_code=404,
            detail=f"Provider '{provider_id}' not found",
        )

    from server.app.llm.model_catalog import get_model_catalog

    catalog = get_model_catalog()
    catalog_models = await catalog.get_models_for_cognition_provider(provider_config.provider)

    models = [
        _catalog_model_to_info(m, provider_label=provider_config.provider) for m in catalog_models
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


@router.post("/providers/{provider_id}/test", response_model=ProviderTestResponse)
async def test_provider(
    provider_id: str,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
    settings: Settings = Depends(get_settings),  # noqa: B008
) -> ProviderTestResponse:
    """Test provider connectivity and credentials.

    Resolves the provider config from the registry, builds a LangChain model,
    and makes a lightweight call to verify credentials and reachability.
    Returns the actual provider error message on failure so GUI settings pages
    can surface actionable diagnostics.

    Example success response:
        {"success": true, "provider": "openai_compatible", "model": "gpt-4o-mini",
         "message": "Connection successful", "response_preview": "Hello!"}

    Example failure response:
        {"success": false, "provider": "openai_compatible", "model": "gpt-4o-mini",
         "message": "Provider 'openai_compatible' configuration error: The api_key client
                     option must be set ...", "response_preview": null}
    """
    import os

    from langchain_core.messages import HumanMessage

    from server.app.exceptions import LLMProviderConfigError
    from server.app.llm.deep_agent_service import _build_model

    # 1. Fetch the provider config
    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
    except RuntimeError as e:
        raise HTTPException(
            status_code=503,
            detail=f"ConfigRegistry unavailable: {e}",
        ) from e

    provider_config = await reg.get_provider(provider_id, scope=scope)
    if provider_config is None:
        raise HTTPException(status_code=404, detail=f"Provider '{provider_id}' not found")

    # 2. Resolve API key from environment
    api_key = os.environ.get(provider_config.api_key_env) if provider_config.api_key_env else None

    # 3. Build the model — surfaces credential / config errors immediately
    try:
        model = _build_model(
            provider=provider_config.provider,
            model_id=provider_config.model,
            api_key=api_key,
            base_url=provider_config.base_url,
            region=provider_config.region,
            role_arn=provider_config.role_arn,
            settings=settings,
        )
    except LLMProviderConfigError as e:
        return ProviderTestResponse(
            success=False,
            provider=provider_config.provider,
            model=provider_config.model,
            message=str(e),
        )

    # 4. Make a minimal call to verify reachability and credentials
    try:
        response = await model.ainvoke([HumanMessage(content="Reply with one word: hello")])
        preview = str(response.content)[:120] if response.content else ""
        return ProviderTestResponse(
            success=True,
            provider=provider_config.provider,
            model=provider_config.model,
            message="Connection successful",
            response_preview=preview,
        )
    except Exception as e:
        return ProviderTestResponse(
            success=False,
            provider=provider_config.provider,
            model=provider_config.model,
            message=str(e),
        )
