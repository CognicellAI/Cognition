"""E2E Scenarios: AgentDefinition field wiring (Phase 3a — #22).

As a builder defining agents via YAML or the REST API,
I want all AgentDefinition fields (system_prompt, skills, config) to
actually take effect at runtime,
so that an agent definition alone is sufficient to configure how my
agent behaves — without modifying server code.

Business Value:
- Per-agent system prompts take effect (previously only global default applied)
- Per-agent skills are loaded (previously ignored)
- Unrecognised agent names fall back gracefully to default
- Two agents with different prompts produce different responses to the same message

Run against: docker-compose environment at http://localhost:8000
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


def _unique(prefix: str = "agent") -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


async def _collect_events(
    api_client: ScenarioTestClient,
    session_id: str,
    content: str,
    timeout: float = 30.0,
) -> list[dict]:
    """Parse SSE stream into a list of event dicts."""
    events: list[dict] = []
    current_event_type: str | None = None

    try:
        async with api_client.client.stream(
            "POST",
            f"{api_client.base_url}/sessions/{session_id}/messages",
            json={"content": content},
            headers={
                "Accept": "text/event-stream",
                "Content-Type": "application/json",
                **api_client.scope_header,
            },
            timeout=timeout,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if line.startswith("event: "):
                    current_event_type = line[7:].strip()
                elif line.startswith("data: "):
                    try:
                        payload = json.loads(line[6:])
                        if current_event_type:
                            payload["event"] = current_event_type
                        events.append(payload)
                        if current_event_type == "done":
                            break
                        current_event_type = None
                    except json.JSONDecodeError:
                        continue
    except Exception:
        pass

    return events


def _assembled_response(events: list[dict]) -> str:
    """Concatenate token event content into a full response string."""
    return "".join(e.get("content", "") for e in events if e.get("event") == "token")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestAgentSystemPrompt:
    """Per-agent system_prompt is consumed and affects agent behavior."""

    async def test_custom_system_prompt_takes_effect(self, api_client: ScenarioTestClient) -> None:
        """An agent registered with a distinctive system_prompt produces a response
        that reflects that prompt.

        We register an agent instructed to always prefix its reply with a unique
        marker string. This is unambiguous: if the marker appears in the response,
        the system prompt was consumed.
        """
        agent_name = _unique("agent")
        marker = f"MARKER-{uuid.uuid4().hex[:6].upper()}"

        # Register agent with a distinctive system prompt
        create_resp = await api_client.post(
            "/agents",
            json={
                "name": agent_name,
                "system_prompt": (
                    f"You are a test agent. "
                    f"Always start every reply with the exact string: {marker}"
                ),
            },
        )
        # If agent API not available, skip
        if create_resp.status_code == 404:
            pytest.skip("POST /agents endpoint not available")
        assert create_resp.status_code in (200, 201), (
            f"Failed to register agent: {create_resp.status_code} {create_resp.text}"
        )

        try:
            session_id = await api_client.create_session(_unique("session"), agent_name=agent_name)
            try:
                events = await _collect_events(api_client, session_id, "Hello")
                response_text = _assembled_response(events)

                done_events = [e for e in events if e.get("event") == "done"]
                assert len(done_events) > 0, "Stream did not complete"

                assert marker in response_text, (
                    f"Expected marker '{marker}' in response but got: {response_text[:200]}"
                )
            finally:
                await api_client.delete(f"/sessions/{session_id}")
        finally:
            await api_client.delete(f"/agents/{agent_name}")

    async def test_two_agents_different_prompts_different_responses(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Two agents with different system prompts produce different responses
        to the same message.

        This validates that agent-level system_prompt isolation works — agent A's
        prompt doesn't bleed into agent B's session.
        """
        marker_a = f"ALPHA-{uuid.uuid4().hex[:6].upper()}"
        marker_b = f"BETA-{uuid.uuid4().hex[:6].upper()}"
        agent_a = _unique("agent-a")
        agent_b = _unique("agent-b")

        for name, marker in [(agent_a, marker_a), (agent_b, marker_b)]:
            resp = await api_client.post(
                "/agents",
                json={
                    "name": name,
                    "system_prompt": (f"Always start every reply with: {marker}"),
                },
            )
            if resp.status_code == 404:
                pytest.skip("POST /agents endpoint not available")
            assert resp.status_code in (200, 201)

        try:
            session_a = await api_client.create_session(_unique(), agent_name=agent_a)
            session_b = await api_client.create_session(_unique(), agent_name=agent_b)

            try:
                events_a = await _collect_events(api_client, session_a, "Hello")
                events_b = await _collect_events(api_client, session_b, "Hello")

                response_a = _assembled_response(events_a)
                response_b = _assembled_response(events_b)

                assert marker_a in response_a, (
                    f"Agent A marker '{marker_a}' not in: {response_a[:200]}"
                )
                assert marker_b in response_b, (
                    f"Agent B marker '{marker_b}' not in: {response_b[:200]}"
                )
                # Sanity: each agent's marker should NOT be in the other's response
                assert marker_b not in response_a, "Agent B's marker leaked into Agent A's response"
                assert marker_a not in response_b, "Agent A's marker leaked into Agent B's response"
            finally:
                await api_client.delete(f"/sessions/{session_a}")
                await api_client.delete(f"/sessions/{session_b}")
        finally:
            await api_client.delete(f"/agents/{agent_a}")
            await api_client.delete(f"/agents/{agent_b}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestAgentFallback:
    """Sessions bound to unknown agent names are rejected at creation time."""

    async def test_unknown_agent_name_rejected_at_session_creation(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Creating a session with a non-existent agent_name returns 422.

        The API validates agent_name at session creation time and rejects
        unknown names immediately rather than silently falling back. This
        protects builders from accidentally running sessions with the wrong
        agent due to a typo.
        """
        response = await api_client.post(
            "/sessions",
            json={
                "title": _unique("session"),
                "agent_name": "definitely-does-not-exist-xyz-9999",
            },
        )
        assert response.status_code == 422, (
            f"Expected 422 for unknown agent_name, got {response.status_code}: {response.text}"
        )
        data = response.json()
        assert "detail" in data or "error" in data, (
            "422 response should include a detail or error message"
        )

    async def test_default_agent_session_works(self, api_client: ScenarioTestClient) -> None:
        """Sessions bound to the 'default' agent work normally."""
        session_id = await api_client.create_session(_unique("session"), agent_name="default")

        try:
            events = await _collect_events(api_client, session_id, "Say: pong")

            done_events = [e for e in events if e.get("event") == "done"]
            assert len(done_events) > 0, "Default agent session stream did not complete"
        finally:
            await api_client.delete(f"/sessions/{session_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestAgentSkills:
    """Per-agent skills are loaded and available to the agent.

    Skills registered in the DB are passed to create_deep_agent(skills=...)
    when the agent's session starts (wired in #22).
    """

    async def test_agent_with_skill_registered_completes_stream(
        self, api_client: ScenarioTestClient
    ) -> None:
        """An agent definition referencing a registered skill can still stream.

        This is a smoke test: we don't verify the skill's content influenced
        the response (that would require a real LLM + specific prompt), but we
        do verify that having a skill reference doesn't break the session.
        """
        skill_name = _unique("skill")
        agent_name = _unique("agent")

        # Register a skill
        skill_resp = await api_client.post(
            "/skills",
            json={
                "name": skill_name,
                "content": f"# {skill_name}\n\nThis is a test skill.",
                "description": "E2E test skill",
            },
        )
        if skill_resp.status_code == 404:
            pytest.skip("POST /skills endpoint not available")
        assert skill_resp.status_code in (200, 201), (
            f"Failed to register skill: {skill_resp.status_code}"
        )

        # Register an agent that references the skill
        agent_resp = await api_client.post(
            "/agents",
            json={
                "name": agent_name,
                "system_prompt": "You are a helpful assistant.",
                "skills": [skill_name],
            },
        )
        if agent_resp.status_code == 404:
            pytest.skip("POST /agents endpoint not available")
        assert agent_resp.status_code in (200, 201)

        try:
            session_id = await api_client.create_session(_unique("session"), agent_name=agent_name)
            try:
                events = await _collect_events(api_client, session_id, "Say: ok")
                done_events = [e for e in events if e.get("event") == "done"]
                error_events = [e for e in events if e.get("event") == "error"]

                assert len(done_events) > 0 or len(error_events) > 0, (
                    "Stream with skill-enabled agent never terminated"
                )
                # No hard error from skill loading
                for err in error_events:
                    assert "skill" not in err.get("message", "").lower(), (
                        f"Skill-related error in stream: {err}"
                    )
            finally:
                await api_client.delete(f"/sessions/{session_id}")
        finally:
            await api_client.delete(f"/agents/{agent_name}")
            await api_client.delete(f"/skills/{skill_name}")
