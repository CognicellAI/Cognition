"""Model discovery and heartbeating for the Cognition engine.

Handles probing configured providers to find available 'Brains'.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, List, Optional

import httpx
import structlog

from server.app.settings import Settings

logger = structlog.get_logger(__name__)


@dataclass
class DiscoveredModel:
    """A model discovered from a provider."""

    id: str
    name: str
    provider_id: str


class DiscoveryEngine:
    """Probes configured providers to find active models."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def discover_models(self) -> List[DiscoveredModel]:
        """Discover models from all configured providers."""
        tasks = [
            self._probe_openai_compatible(),
            self._probe_openai(),
            self._probe_bedrock(),
            self._probe_mock(),
        ]
        results = await asyncio.gather(*tasks)

        # Flatten results
        discovered = []
        for r in results:
            discovered.extend(r)

        return discovered

    async def _probe_openai_compatible(self) -> List[DiscoveredModel]:
        """Probe OpenRouter/Local providers."""
        base_url = self.settings.openai_compatible_base_url
        if not base_url:
            return []

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                # Standard OpenAI-compatible models endpoint
                resp = await client.get(f"{base_url}/models")
                if resp.status_code == 200:
                    data = resp.json()
                    models = data.get("data", [])
                    return [
                        DiscoveredModel(
                            id=m["id"],
                            name=m.get("name", m["id"]),
                            provider_id="openai_compatible",
                        )
                        for m in models
                    ]
        except Exception as e:
            logger.warning("Failed to probe openai_compatible endpoint", error=str(e))

        # Fallback to the configured model if discovery fails but URL is present
        return [
            DiscoveredModel(
                id=self.settings.llm_model,
                name=self.settings.llm_model,
                provider_id="openai_compatible",
            )
        ]

    async def _probe_openai(self) -> List[DiscoveredModel]:
        """Probe OpenAI if configured."""
        if not self.settings.openai_api_key:
            return []

        # We could probe /v1/models here, but usually these are static for devs
        return [
            DiscoveredModel(id="gpt-4o", name="GPT-4o", provider_id="openai"),
            DiscoveredModel(id="gpt-4o-mini", name="GPT-4o Mini", provider_id="openai"),
            DiscoveredModel(id="o1-preview", name="o1 Preview", provider_id="openai"),
        ]

    async def _probe_bedrock(self) -> List[DiscoveredModel]:
        """Probe AWS Bedrock if configured."""
        if not self.settings.aws_access_key_id:
            return []

        # Usually static list for the substrate
        return [
            DiscoveredModel(
                id="anthropic.claude-3-sonnet-20240229-v1:0",
                name="Claude 3 Sonnet (Bedrock)",
                provider_id="bedrock",
            ),
            DiscoveredModel(
                id="anthropic.claude-3-opus-20240229-v1:0",
                name="Claude 3 Opus (Bedrock)",
                provider_id="bedrock",
            ),
        ]

    async def _probe_mock(self) -> List[DiscoveredModel]:
        """Return the mock model."""
        return [DiscoveredModel(id="mock-model", name="Mock Model", provider_id="mock")]

    async def get_provider_for_model(self, model_id: str) -> Optional[str]:
        """Find the provider for a given model ID."""
        models = await self.discover_models()
        for m in models:
            if m.id == model_id:
                return m.provider_id
        return None
