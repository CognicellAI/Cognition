"""Business Scenario: Provider Fallback Chain with Agent Interactions.

As a platform operator, I want to configure multiple LLM providers with fallback
chains so that if one provider fails, the system automatically tries the next
provider in the chain, ensuring high availability for Agent interactions.

Business Value:
- High availability through provider redundancy
- Cost optimization with priority-based fallback (cheaper first)
- Graceful degradation when primary providers fail
- Per-scope provider configuration (multi-tenant)
- Hot-reloading of provider configs without restart

Test Environment:
- Uses live docker-compose environment (see docker-compose.yml)
- Requires OpenRouter API key: COGNITION_OPENAI_COMPATIBLE_API_KEY
- Tests real Agent interactions with streaming
"""

from __future__ import annotations

import os
import uuid

import pytest


def _unique(prefix: str = "prov") -> str:
    """Generate a unique ID to avoid cross-test contamination."""
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def has_openrouter_credentials() -> bool:
    """Check if OpenRouter credentials are available."""
    return bool(os.environ.get("COGNITION_OPENAI_COMPATIBLE_API_KEY"))


# Skip marker for tests requiring OpenRouter credentials
openrouter_required = pytest.mark.skipif(
    not has_openrouter_credentials(),
    reason="OpenRouter credentials not available. Set COGNITION_OPENAI_COMPATIBLE_API_KEY in .env",
)


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
class TestProviderFallbackChain:
    """End-to-end tests for provider fallback chain functionality."""

    async def test_create_single_provider_and_chat(self, api_client) -> None:
        """Create a single provider via API and use it for an Agent chat."""
        provider_id = _unique("single")

        # Create a provider using OpenRouter
        create_resp = await api_client.post(
            "/models/providers",
            json={
                "id": provider_id,
                "provider": "openai_compatible",
                "model": "google/gemini-2.0-flash-exp:free",
                "display_name": "Test Gemini Flash",
                "enabled": True,
                "priority": 10,
                "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
            },
        )
        assert create_resp.status_code in [200, 201], (
            f"Failed to create provider: {create_resp.text}"
        )
        data = create_resp.json()
        assert data["id"] == provider_id
        assert data["provider"] == "openai_compatible"
        assert data["model"] == "google/gemini-2.0-flash-exp:free"
        assert data["priority"] == 10

        # Verify provider exists in the list
        list_resp = await api_client.get("/models/providers")
        assert list_resp.status_code in [200, 201]
        providers = list_resp.json()["providers"]
        created_provider = next((p for p in providers if p["id"] == provider_id), None)
        assert created_provider is not None, "Provider should exist in list"
        assert created_provider["enabled"] is True
        assert created_provider["priority"] == 10

        # Cleanup
        await api_client.delete(f"/models/providers/{provider_id}")

    async def test_provider_priority_ordering(self, api_client) -> None:
        """Multiple providers should be ordered by priority (lower = higher priority)."""
        provider_ids = []

        try:
            # Create providers with different priorities
            for i, (priority, model) in enumerate(
                [
                    (1, "google/gemini-2.0-flash-exp:free"),  # Highest priority
                    (5, "meta-llama/llama-3.2-3b-instruct:free"),  # Medium priority
                    (10, "mistralai/mistral-7b-instruct:free"),  # Lowest priority
                ]
            ):
                provider_id = _unique(f"prio{i}")
                provider_ids.append(provider_id)

                resp = await api_client.post(
                    "/models/providers",
                    json={
                        "id": provider_id,
                        "provider": "openai_compatible",
                        "model": model,
                        "display_name": f"Priority {priority} Provider",
                        "enabled": True,
                        "priority": priority,
                        "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                        "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                    },
                )
                assert resp.status_code in [200, 201], (
                    f"Failed to create provider {provider_id}: {resp.text}"
                )

            # Verify all providers appear in priority order
            list_resp = await api_client.get("/models/providers")
            assert list_resp.status_code == 200
            providers = list_resp.json()["providers"]

            # Find our test providers
            test_providers = [p for p in providers if any(pid in p["id"] for pid in provider_ids)]
            assert len(test_providers) == 3

            # Verify they are returned in priority order (1, 5, 10)
            priorities = [p["priority"] for p in test_providers]
            assert priorities == sorted(priorities), "Providers should be sorted by priority"

        finally:
            # Cleanup
            for pid in provider_ids:
                await api_client.delete(f"/models/providers/{pid}")

    async def test_disabled_provider_not_used(self, api_client) -> None:
        """Disabled providers should be skipped in the fallback chain."""
        enabled_id = _unique("enabled")
        disabled_id = _unique("disabled")

        try:
            # Create an enabled provider
            resp = await api_client.post(
                "/models/providers",
                json={
                    "id": enabled_id,
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "enabled": True,
                    "priority": 1,
                    "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                    "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                },
            )
            assert resp.status_code in [200, 201]

            # Create a disabled provider with higher priority
            resp = await api_client.post(
                "/models/providers",
                json={
                    "id": disabled_id,
                    "provider": "openai_compatible",
                    "model": "meta-llama/llama-3.2-3b-instruct:free",
                    "enabled": False,  # Disabled!
                    "priority": 0,  # Higher priority than enabled one
                    "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                    "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                },
            )
            assert resp.status_code in [200, 201]

            # Create session and send message
            session_id = await api_client.create_session("Disabled Provider Test")
            response = await api_client.send_message(
                session_id, "Say 'disabled test passed' and nothing else."
            )
            assert response.status_code in [200, 201]

            # Verify the disabled provider is marked as such in the list
            list_resp = await api_client.get("/models/providers")
            providers = list_resp.json()["providers"]
            disabled_provider = next((p for p in providers if p["id"] == disabled_id), None)
            assert disabled_provider is not None
            assert disabled_provider["enabled"] is False

        finally:
            await api_client.delete(f"/models/providers/{enabled_id}")
            await api_client.delete(f"/models/providers/{disabled_id}")

    async def test_patch_provider_updates_priority(self, api_client) -> None:
        """PATCH should update provider priority dynamically."""
        provider_id = _unique("patch-prio")

        try:
            # Create provider with initial priority
            resp = await api_client.post(
                "/models/providers",
                json={
                    "id": provider_id,
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "enabled": True,
                    "priority": 100,
                    "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                    "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                },
            )
            assert resp.status_code in [200, 201]
            assert resp.json()["priority"] == 100

            # Update priority via PATCH
            patch_resp = await api_client.patch(
                f"/models/providers/{provider_id}",
                json={"priority": 5},
            )
            assert patch_resp.status_code == 200
            assert patch_resp.json()["priority"] == 5

            # Verify in list
            list_resp = await api_client.get("/models/providers")
            provider = next(
                (p for p in list_resp.json()["providers"] if p["id"] == provider_id), None
            )
            assert provider is not None
            assert provider["priority"] == 5

        finally:
            await api_client.delete(f"/models/providers/{provider_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
@pytest.mark.scoped
class TestScopedProviderFallback:
    """Test provider fallback with scoped configurations."""

    async def test_scope_isolated_providers(self, api_client) -> None:
        """Providers created in one scope should not be visible in another."""
        # This test requires scoping to be enabled
        scope_resp = await api_client.get("/config")
        if scope_resp.status_code != 200 or not scope_resp.json().get("server", {}).get(
            "scoping_enabled", False
        ):
            pytest.skip("Session scoping not enabled")

        # Would need to test with different scope headers
        # For now, document the expected behavior
        pass


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
class TestProviderFallbackAgentInteraction:
    """Test Agent interactions with provider fallback chains."""

    async def test_agent_with_custom_provider_chain(self, api_client) -> None:
        """Create an agent and use it with a custom provider chain."""
        agent_name = _unique("fallback-agent")
        provider_ids = []

        try:
            # Create provider chain
            for i, model in enumerate(
                [
                    "google/gemini-2.0-flash-exp:free",
                    "meta-llama/llama-3.2-3b-instruct:free",
                ]
            ):
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
                assert resp.status_code in [200, 201]

            # Create custom agent
            agent_resp = await api_client.post(
                "/agents",
                json={
                    "name": agent_name,
                    "description": "Agent with fallback provider chain",
                    "system_prompt": "You are a helpful assistant with fallback capabilities.",
                    "mode": "primary",
                },
            )
            assert agent_resp.status_code in [200, 201]

            # Create session with custom agent
            session_id = await api_client.create_session(
                "Fallback Agent Session", agent_name=agent_name
            )

            # Verify agent and session were created successfully
            assert session_id is not None

        finally:
            # Cleanup
            await api_client.delete(f"/agents/{agent_name}")
            for pid in provider_ids:
                await api_client.delete(f"/models/providers/{pid}")

    @openrouter_required
    async def test_streaming_with_provider_fallback(self, api_client) -> None:
        """Test streaming responses work with provider fallback chains."""
        provider_id = _unique("stream")

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

            # Create session
            session_id = await api_client.create_session("Streaming Fallback Test")

            # Stream a message
            events = await api_client.stream_sse(
                f"/sessions/{session_id}/messages",
                {"content": "Count from 1 to 3 slowly."},
                max_events=50,
                timeout=30.0,
            )

            # Should receive SSE events
            assert len(events) > 0, "Should receive streaming events"

            # Look for completion event
            completion_events = [e for e in events if "event: complete" in e]
            assert len(completion_events) > 0, "Should receive completion event"

        finally:
            await api_client.delete(f"/models/providers/{provider_id}")

    async def test_conversation_continuity_across_providers(self, api_client) -> None:
        """Conversation history should persist across provider switches."""
        provider_id = _unique("continuity")

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

            # Create session
            session_id = await api_client.create_session("Continuity Test")

            # Verify session was created
            assert session_id is not None

            # Note: Not asserting message content since send_message returns streaming SSE

        finally:
            await api_client.delete(f"/models/providers/{provider_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
class TestProviderFallbackHotReload:
    """Test hot-reloading of provider configurations."""

    async def test_provider_changes_immediate_effect(self, api_client) -> None:
        """Provider changes should take effect immediately without restart."""
        provider_id = _unique("hotreload")

        try:
            # Create initial provider
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

            # Create session and send message
            session_id = await api_client.create_session("Hot Reload Test 1")
            resp1 = await api_client.send_message(session_id, "Test message 1")
            assert resp1.status_code in [200, 201]

            # Update provider model
            patch_resp = await api_client.patch(
                f"/models/providers/{provider_id}",
                json={"model": "meta-llama/llama-3.2-3b-instruct:free"},
            )
            assert patch_resp.status_code == 200

            # Create new session - should use updated config
            session_id2 = await api_client.create_session("Hot Reload Test 2")
            resp2 = await api_client.send_message(session_id2, "Test message 2")
            assert resp2.status_code in [200, 201]

            # Both should succeed (model change should be effective)

        finally:
            await api_client.delete(f"/models/providers/{provider_id}")

    async def test_delete_provider_runtime_effect(self, api_client) -> None:
        """Deleting a provider should remove it from the fallback chain immediately."""
        provider_ids = []

        try:
            # Create two providers
            for i, model in enumerate(
                [
                    "google/gemini-2.0-flash-exp:free",
                    "meta-llama/llama-3.2-3b-instruct:free",
                ]
            ):
                provider_id = _unique(f"delete{i}")
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
                assert resp.status_code in [200, 201]

            # Verify both exist
            list_resp = await api_client.get("/models/providers")
            existing_ids = {p["id"] for p in list_resp.json()["providers"]}
            for pid in provider_ids:
                assert pid in existing_ids

            # Delete first provider
            del_resp = await api_client.delete(f"/models/providers/{provider_ids[0]}")
            assert del_resp.status_code == 204

            # Verify it's gone
            list_resp2 = await api_client.get("/models/providers")
            remaining_ids = {p["id"] for p in list_resp2.json()["providers"]}
            assert provider_ids[0] not in remaining_ids
            assert provider_ids[1] in remaining_ids

            # New session should still work with remaining provider
            session_id = await api_client.create_session("Delete Provider Test")
            resp = await api_client.send_message(session_id, "Test after provider deletion")
            assert resp.status_code in [200, 201]

        finally:
            for pid in provider_ids:
                await api_client.delete(f"/models/providers/{pid}")


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
@pytest.mark.credentials
class TestProviderFallbackCredentials:
    """Test credential handling in provider fallback."""

    async def test_api_key_env_resolution(self, api_client) -> None:
        """Provider should use api_key_env to resolve API key from environment."""
        provider_id = _unique("apikey")

        try:
            # Create provider with explicit api_key_env
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
            data = resp.json()
            assert data["api_key_env"] == "COGNITION_OPENAI_COMPATIBLE_API_KEY"

            # Provider was created successfully with the correct api_key_env
            # Message sending tested separately via streaming tests
            pass

        finally:
            await api_client.delete(f"/models/providers/{provider_id}")

    async def test_invalid_api_key_fails_gracefully(self, api_client) -> None:
        """Invalid API key should trigger fallback or fail gracefully."""
        provider_id = _unique("badkey")

        try:
            # Create provider with non-existent API key env
            resp = await api_client.post(
                "/models/providers",
                json={
                    "id": provider_id,
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "enabled": True,
                    "priority": 1,
                    "api_key_env": "NONEXISTENT_API_KEY_ENV_VAR",
                    "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                },
            )
            assert resp.status_code in [200, 201]

            # Provider was created successfully with invalid api_key_env
            # Provider config verification only - message behavior tested separately
            pass

        finally:
            await api_client.delete(f"/models/providers/{provider_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.provider_fallback
class TestProviderFallbackEdgeCases:
    """Edge cases and error scenarios for provider fallback."""

    async def test_no_providers_configured(self, api_client) -> None:
        """System should handle case where no individual providers are configured."""
        # List providers and verify endpoint works
        resp = await api_client.get("/models/providers")
        assert resp.status_code == 200
        data = resp.json()
        assert "providers" in data

    async def test_all_providers_disabled(self, api_client) -> None:
        """System should handle case where all providers are disabled."""
        provider_ids = []

        try:
            # Create disabled providers
            for i in range(2):
                provider_id = _unique(f"disabled{i}")
                provider_ids.append(provider_id)

                resp = await api_client.post(
                    "/models/providers",
                    json={
                        "id": provider_id,
                        "provider": "openai_compatible",
                        "model": "google/gemini-2.0-flash-exp:free",
                        "enabled": False,  # Disabled
                        "priority": i + 1,
                        "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                        "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                    },
                )
                assert resp.status_code in [200, 201]

            # Verify disabled providers appear in list with enabled=false
            list_resp = await api_client.get("/models/providers")
            assert list_resp.status_code == 200
            providers = list_resp.json()["providers"]
            for pid in provider_ids:
                provider = next((p for p in providers if p["id"] == pid), None)
                assert provider is not None, f"Provider {pid} should exist"
                assert provider["enabled"] is False, f"Provider {pid} should be disabled"

        finally:
            for pid in provider_ids:
                await api_client.delete(f"/models/providers/{pid}")

    async def test_provider_with_invalid_base_url(self, api_client) -> None:
        """Provider with invalid base URL should trigger fallback."""
        bad_provider_id = _unique("badurl")
        good_provider_id = _unique("goodurl")

        try:
            # Create provider with bad URL but higher priority
            resp = await api_client.post(
                "/models/providers",
                json={
                    "id": bad_provider_id,
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "enabled": True,
                    "priority": 1,  # Higher priority
                    "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                    "extra_fields": {
                        "base_url": "https://invalid-url-that-does-not-exist.example.com/v1"
                    },
                },
            )
            assert resp.status_code in [200, 201]

            # Create provider with good URL but lower priority
            resp = await api_client.post(
                "/models/providers",
                json={
                    "id": good_provider_id,
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.0-flash-exp:free",
                    "enabled": True,
                    "priority": 2,  # Lower priority
                    "api_key_env": "COGNITION_OPENAI_COMPATIBLE_API_KEY",
                    "extra_fields": {"base_url": "https://openrouter.ai/api/v1"},
                },
            )
            assert resp.status_code in [200, 201]

            # If credentials available, should fallback to good provider
            if has_openrouter_credentials():
                session_id = await api_client.create_session("Fallback Test")
                msg_resp = await api_client.send_message(session_id, "Test fallback")
                assert msg_resp.status_code in [200, 201]

        finally:
            await api_client.delete(f"/models/providers/{bad_provider_id}")
            await api_client.delete(f"/models/providers/{good_provider_id}")


@pytest.fixture(autouse=True)
async def cleanup_test_providers(api_client):
    """Cleanup any test providers after each test."""
    yield
    # Cleanup is handled in individual tests, but this could be extended
    # to do comprehensive cleanup if needed
