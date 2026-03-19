"""E2E: Verify agent discovers skills from the DB-backed skill backend.

This test creates a skill via the API, configures a session with a real
LLM provider (OpenRouter), and verifies the skill appears in the agent's
response by streaming SSE events and inspecting the output.

Requires:
    - docker-compose environment running (server at localhost:8000)
    - COGNITION_OPENAI_COMPATIBLE_API_KEY set in the server environment
    - The 'openrouter' provider pre-registered (or env-based openai_compatible)
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient

# Generous timeout for real LLM calls
LLM_TIMEOUT = httpx.Timeout(120.0, connect=10.0)

# Skill with a unique, hard-to-hallucinate name
SKILL_NAME_PREFIX = "xyzzy-unicorn-detector"


def _unique(prefix: str = SKILL_NAME_PREFIX) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:6]}"


@pytest.mark.asyncio
@pytest.mark.e2e
class TestSkillDiscoveryFromDB:
    """Verify that skills stored in ConfigRegistry DB are discovered by the agent."""

    async def test_db_skill_appears_in_agent_system_prompt(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Create a skill via API, send a message, and verify the skill name
        appears in the SSE stream (token events from the LLM response)."""

        skill_name = _unique()
        skill_content = f"""---
name: {skill_name}
description: Detects unicorns in any text using advanced pattern matching
---

# {skill_name}

## When to Use
- When the user asks you to detect unicorns
- When text might contain hidden unicorns

## Instructions
1. Read the input text carefully
2. Look for the word "unicorn" (case-insensitive)
3. Report findings
"""

        # ---- Step 1: Create the skill via API ----
        create_resp = await api_client.post(
            "/skills",
            json={"name": skill_name, "content": skill_content},
        )
        assert create_resp.status_code == 201, (
            f"Failed to create skill: {create_resp.status_code} {create_resp.text}"
        )

        # Verify the skill is stored
        get_resp = await api_client.get(f"/skills/{skill_name}")
        assert get_resp.status_code == 200
        stored_skill = get_resp.json()
        assert stored_skill["name"] == skill_name
        assert stored_skill["enabled"] is True
        assert "/skills/api/" in stored_skill["path"]

        # ---- Step 2: Create a session using OpenRouter (not mock) ----
        session_resp = await api_client.post(
            "/sessions",
            json={"title": f"Skill discovery test - {skill_name}"},
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        # Configure the session to use the real provider
        patch_resp = await api_client.patch(
            f"/sessions/{session_id}",
            json={
                "config": {
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.5-flash",
                }
            },
        )
        assert patch_resp.status_code == 200, (
            f"Failed to patch session: {patch_resp.status_code} {patch_resp.text}"
        )

        # ---- Step 3: Send a message asking about skills ----
        collected_tokens: list[str] = []
        errors: list[str] = []

        headers = {
            **api_client.scope_header,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        try:
            async with api_client.client.stream(
                "POST",
                f"{api_client.base_url}/sessions/{session_id}/messages",
                json={
                    "content": "List all available skills you have access to. Include their exact names."
                },
                headers=headers,
                timeout=LLM_TIMEOUT,
            ) as response:
                assert response.status_code == 200, f"SSE stream returned {response.status_code}"

                event_type = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if event_type == "token":
                            # Token events use "content" key
                            token_text = data.get("content", "") or data.get("token", "")
                            collected_tokens.append(token_text)
                        elif event_type == "error":
                            errors.append(data.get("message", str(data)))
                        elif event_type == "done":
                            # Extract full response from done event's assistant_data
                            assistant_data = data.get("assistant_data", {})
                            if assistant_data and assistant_data.get("content"):
                                collected_tokens.append(assistant_data["content"])
                            break
        except httpx.ReadTimeout:
            pass  # May timeout after collecting enough data

        # ---- Step 4: Verify ----
        full_response = "".join(collected_tokens)

        # If there are errors, fail with details
        assert not errors, f"SSE stream had errors: {errors}"

        # The agent should have received the skill in its system prompt and
        # should mention it when asked to list skills
        assert skill_name in full_response, (
            f"Skill '{skill_name}' NOT found in agent response. "
            f"This means the DB-backed skill was not injected into the system prompt. "
            f"Response: {full_response[:1000]}"
        )

        # ---- Cleanup ----
        await api_client.delete(f"/skills/{skill_name}")
        await api_client.delete(f"/sessions/{session_id}")

    async def test_disabled_db_skill_not_in_response(self, api_client: ScenarioTestClient) -> None:
        """A disabled skill should NOT appear in the agent's response."""

        skill_name = _unique()
        skill_content = f"""---
name: {skill_name}
description: A disabled skill that should not be visible
---

# {skill_name}

This skill is disabled and should not be discoverable.
"""

        # Create the skill as disabled
        create_resp = await api_client.post(
            "/skills",
            json={"name": skill_name, "content": skill_content, "enabled": False},
        )
        assert create_resp.status_code == 201

        # Create session with real provider
        session_resp = await api_client.post(
            "/sessions",
            json={"title": f"Disabled skill test - {skill_name}"},
        )
        assert session_resp.status_code == 201
        session_id = session_resp.json()["id"]

        patch_resp = await api_client.patch(
            f"/sessions/{session_id}",
            json={
                "config": {
                    "provider": "openai_compatible",
                    "model": "google/gemini-2.5-flash",
                }
            },
        )
        assert patch_resp.status_code == 200

        # Stream the response
        collected_tokens: list[str] = []
        headers = {
            **api_client.scope_header,
            "Accept": "text/event-stream",
            "Content-Type": "application/json",
        }

        try:
            async with api_client.client.stream(
                "POST",
                f"{api_client.base_url}/sessions/{session_id}/messages",
                json={
                    "content": "List all available skills you have access to. Include their exact names."
                },
                headers=headers,
                timeout=LLM_TIMEOUT,
            ) as response:
                event_type = None
                async for line in response.aiter_lines():
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("event: "):
                        event_type = line[7:]
                    elif line.startswith("data: "):
                        data_str = line[6:]
                        try:
                            data = json.loads(data_str)
                        except json.JSONDecodeError:
                            continue

                        if event_type == "token":
                            collected_tokens.append(
                                data.get("content", "") or data.get("token", "")
                            )
                        elif event_type == "done":
                            assistant_data = data.get("assistant_data", {})
                            if assistant_data and assistant_data.get("content"):
                                collected_tokens.append(assistant_data["content"])
                            break
        except httpx.ReadTimeout:
            pass

        full_response = "".join(collected_tokens)

        # The disabled skill should NOT appear in the response
        assert skill_name not in full_response, (
            f"Disabled skill '{skill_name}' was found in agent response — "
            f"it should not have been injected into the system prompt."
        )

        # Cleanup
        await api_client.delete(f"/skills/{skill_name}")
        await api_client.delete(f"/sessions/{session_id}")
