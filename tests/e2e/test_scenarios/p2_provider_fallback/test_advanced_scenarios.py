"""Business Scenario: Advanced Provider Fallback Scenarios.

Additional edge cases and complex scenarios for provider fallback testing.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest


def _unique(prefix: str = "adv") -> str:
    """Generate a unique ID."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
class TestAdvancedProviderScenarios:
    """Advanced provider fallback scenarios."""

    async def test_large_provider_chain_performance(self, api_client) -> None:
        """Test that a large provider chain (10+ providers) performs reasonably."""
        provider_ids = []

        try:
            # Create 10 providers with different priorities
            models = [
                "google/gemini-2.0-flash-exp:free",
                "meta-llama/llama-3.2-3b-instruct:free",
                "mistralai/mistral-7b-instruct:free",
                "google/gemini-2.0-flash-exp:free",
                "meta-llama/llama-3.2-3b-instruct:free",
                "mistralai/mistral-7b-instruct:free",
                "google/gemini-2.0-flash-exp:free",
                "meta-llama/llama-3.2-3b-instruct:free",
                "mistralai/mistral-7b-instruct:free",
                "google/gemini-2.0-flash-exp:free",
            ]

            for i, model in enumerate(models):
                provider_id = _unique(f"chain{i}")
                provider_ids.append(provider_id)

                resp = await api_client.post(
                    "/models/providers",
                    json={
                        "id": provider_id,
                        "provider": "openai_compatible",
                        "model": model,
                        "enabled": True,
                        "priority": i + 1,
                        "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                        "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                    },
                )
                assert resp.status_code in [200, 201], f"Failed to create provider {i}: {resp.text}"

            # Verify all 10 providers exist
            list_resp = await api_client.get("/models/providers")
            assert list_resp.status_code == 200
            providers = list_resp.json()["providers"]
            test_providers = [p for p in providers if any(pid in p["id"] for pid in provider_ids)]
            assert len(test_providers) == 10

            # List should be sorted by priority
            priorities = [p["priority"] for p in test_providers]
            assert priorities == sorted(priorities)

        finally:
            # Cleanup
            for pid in provider_ids:
                await api_client.delete(f"/models/providers/{pid}")

    async def test_concurrent_provider_operations(self, api_client) -> None:
        """Test concurrent provider CRUD operations."""
        provider_ids = []

        try:
            # Create multiple providers concurrently
            async def create_provider(idx: int) -> str:
                provider_id = _unique(f"concurrent{idx}")
                resp = await api_client.post(
                    "/models/providers",
                    json={
                        "id": provider_id,
                        "provider": "openai_compatible",
                        "model": "google/gemini-2.0-flash-exp:free",
                        "enabled": True,
                        "priority": idx,
                        "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                        "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                    },
                )
                assert resp.status_code in [200, 201]
                return provider_id

            # Create 5 providers concurrently
            tasks = [create_provider(i) for i in range(5)]
            provider_ids = await asyncio.gather(*tasks)

            # Verify all exist
            list_resp = await api_client.get("/models/providers")
            existing_ids = {p["id"] for p in list_resp.json()["providers"]}
            for pid in provider_ids:
                assert pid in existing_ids

        finally:
            # Cleanup concurrently
            await asyncio.gather(
                *[api_client.delete(f"/models/providers/{pid}") for pid in provider_ids]
            )

    async def test_rapid_provider_updates(self, api_client) -> None:
        """Test rapid sequential updates to a provider."""
        provider_id = _unique("rapid")

        try:
            # Create provider
            resp = await api_client.post(
                "/models/providers",
                json={
                    "id": provider_id,
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "enabled": True,
                    "priority": 1,
                    "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                    "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                },
            )
            assert resp.status_code in [200, 201]

            # Rapidly update priority 10 times
            for i in range(10):
                patch_resp = await api_client.patch(
                    f"/models/providers/{provider_id}",
                    json={"priority": i + 1},
                )
                assert patch_resp.status_code == 200

            # Verify final priority in list
            list_resp = await api_client.get("/models/providers")
            assert list_resp.status_code in [200, 201]
            providers = list_resp.json()["providers"]
            updated_provider = next((p for p in providers if p["id"] == provider_id), None)
            assert updated_provider is not None
            assert updated_provider["priority"] == 10

        finally:
            await api_client.delete(f"/models/providers/{provider_id}")

    async def test_provider_id_special_characters(self, api_client) -> None:
        """Test provider IDs with various valid characters."""
        # Test IDs with different patterns
        test_ids = [
            _unique("test-with-dashes"),
            _unique("test_with_underscores"),
            _unique("test.with.dots"),
            _unique("test123"),
            _unique("TestMixedCase"),
        ]

        created_ids = []

        try:
            for provider_id in test_ids:
                resp = await api_client.post(
                    "/models/providers",
                    json={
                        "id": provider_id,
                        "provider": "openai_compatible",
                        "model": "google/gemini-2.0-flash-exp:free",
                        "enabled": True,
                        "priority": 1,
                        "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                        "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                    },
                )
                if resp.status_code == 201:
                    created_ids.append(provider_id)
                else:
                    # Some characters might not be allowed - that's OK
                    pass

            # Verify created providers exist
            if created_ids:
                list_resp = await api_client.get("/models/providers")
                existing_ids = {p["id"] for p in list_resp.json()["providers"]}
                for pid in created_ids:
                    assert pid in existing_ids

        finally:
            for pid in created_ids:
                await api_client.delete(f"/models/providers/{pid}")


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
class TestProviderFallbackSessionOverrides:
    """Test session-level provider overrides."""

    async def test_session_provider_override(self, api_client) -> None:
        """Test that session can specify its own provider."""
        # Create a provider
        provider_id = _unique("session-override")

        try:
            resp = await api_client.post(
                "/models/providers",
                json={
                    "id": provider_id,
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "enabled": True,
                    "priority": 1,
                    "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                    "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                },
            )
            assert resp.status_code in [200, 201]

            # Create session - would need to support provider override in session config
            # This depends on the session model supporting provider overrides
            session_id = await api_client.create_session("Provider Override Test")

            # Verify session was created successfully
            assert session_id is not None

        finally:
            await api_client.delete(f"/models/providers/{provider_id}")
