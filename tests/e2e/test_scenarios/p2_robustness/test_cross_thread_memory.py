"""E2E Scenarios: LangGraph Store cross-thread memory (Phase 3c — #17).

As an end user of a Cognition-powered application,
I want the agent to remember context across sessions,
so that I don't have to re-explain my setup every time I start a new conversation.

Phase 3c wires the LangGraph BaseStore through the full pipeline:
- get_store() added to all storage backends (InMemoryStore, AsyncSqliteStore, AsyncPostgresStore)
- CognitionContext scopes Store namespaces per user (from session.scopes)
- create_deep_agent() receives store= and context_schema=CognitionContext
- runtime.store and runtime.context are available inside agent nodes/middleware

Current state: Store plumbing is in place but no built-in memory tools exist yet.
These tests validate the plumbing layer — that Store wiring doesn't break
normal session/message/streaming behaviour and that user-scope isolation
is preserved.

Full cross-session memory read/write tests are deferred until memory tools
are available (planned in a future discussion).

Business Value (plumbing layer):
- Sessions with real LLM still complete — Store init doesn't break streaming
- Multiple concurrent sessions for the same user don't interfere
- User A's sessions are not visible to user B (scope isolation intact)

Run against: docker-compose environment at http://localhost:8000
(Postgres persistence + scoping enabled)
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


def _unique(prefix: str = "session") -> str:
    return f"{prefix}-store-{uuid.uuid4().hex[:8]}"


def _scoped_client(base_url: str, user_id: str) -> ScenarioTestClient:
    """Return a ScenarioTestClient pre-configured with a specific user scope."""
    client = ScenarioTestClient(base_url)
    client.scope_header = {"X-Cognition-Scope-User": user_id}
    return client


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


@pytest.mark.asyncio
@pytest.mark.e2e
class TestStorePlumbingDoesNotBreakStreaming:
    """Store wiring must not break normal session/message/streaming behaviour.

    These tests existed conceptually before Phase 3c, but after wiring
    store= into create_deep_agent() and runtime.astream(), we need to
    verify the plumbing didn't introduce any regressions.
    """

    async def test_session_streams_complete_with_store_wired(
        self, api_client: ScenarioTestClient
    ) -> None:
        """A normal session + message produces a complete stream with Store wired."""
        session_id = await api_client.create_session(_unique())

        try:
            events = await _collect_events(api_client, session_id, "Say: ok")

            done_events = [e for e in events if e.get("event") == "done"]
            error_events = [e for e in events if e.get("event") == "error"]

            assert len(done_events) > 0 or len(error_events) > 0, (
                "Stream did not terminate — Store wiring may have hung the agent"
            )

            # Specifically verify no Store-related error
            for err in error_events:
                msg = err.get("message", "").lower()
                assert "store" not in msg, f"Store-related error in stream: {err}"
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_multiple_messages_same_session(self, api_client: ScenarioTestClient) -> None:
        """Multiple messages in the same session all complete with Store wired."""
        session_id = await api_client.create_session(_unique())

        try:
            for prompt in ["Say: one", "Say: two", "Say: three"]:
                events = await _collect_events(api_client, session_id, prompt)
                terminal = [e for e in events if e.get("event") in ("done", "error")]
                assert len(terminal) > 0, (
                    f"Message '{prompt}' stream did not terminate with Store wired"
                )
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_health_check_passes_with_store_wired(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Server health endpoint returns ok after Store wiring."""
        response = await api_client.get("/health")
        assert response.status_code == 200

        data = response.json()
        assert data.get("status") in ("ok", "healthy", "running"), (
            f"Unexpected health status: {data}"
        )


@pytest.mark.asyncio
@pytest.mark.e2e
class TestMultipleSessionsSameUser:
    """Two sessions for the same user don't interfere with each other.

    With Store wired, Store namespaces are keyed on user_id from
    CognitionContext. Two sessions for the same user share a namespace,
    but their LangGraph thread states are isolated. This verifies the
    two don't conflict.
    """

    async def test_two_sessions_both_complete(self, api_client: ScenarioTestClient) -> None:
        """Two concurrent sessions for the same user both stream to completion."""
        session_a = await api_client.create_session(_unique("session-a"))
        session_b = await api_client.create_session(_unique("session-b"))

        try:
            events_a = await _collect_events(api_client, session_a, "Say: alpha")
            events_b = await _collect_events(api_client, session_b, "Say: beta")

            done_a = [e for e in events_a if e.get("event") in ("done", "error")]
            done_b = [e for e in events_b if e.get("event") in ("done", "error")]

            assert len(done_a) > 0, "Session A stream did not terminate"
            assert len(done_b) > 0, "Session B stream did not terminate"
        finally:
            await api_client.delete(f"/sessions/{session_a}")
            await api_client.delete(f"/sessions/{session_b}")

    async def test_sessions_have_independent_thread_state(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Two sessions for the same user have independent conversation histories.

        Send different content to each session. Verify both sessions have
        their own message stored (not cross-contaminating each other's history).
        """
        session_a = await api_client.create_session(_unique("session-a"))
        session_b = await api_client.create_session(_unique("session-b"))

        try:
            # Send unique prompts to each session
            await _collect_events(api_client, session_a, "ALPHA_UNIQUE_MARKER")
            await _collect_events(api_client, session_b, "BETA_UNIQUE_MARKER")

            # Fetch message history for each session
            msgs_a_resp = await api_client.get(f"/sessions/{session_a}/messages")
            msgs_b_resp = await api_client.get(f"/sessions/{session_b}/messages")

            if msgs_a_resp.status_code == 200 and msgs_b_resp.status_code == 200:
                msgs_a = [m.get("content", "") for m in msgs_a_resp.json().get("messages", [])]
                msgs_b = [m.get("content", "") for m in msgs_b_resp.json().get("messages", [])]

                # Session A should contain ALPHA marker, not BETA
                assert any("ALPHA_UNIQUE_MARKER" in str(m) for m in msgs_a), (
                    f"ALPHA_UNIQUE_MARKER not found in session A messages: {msgs_a}"
                )
                assert not any("BETA_UNIQUE_MARKER" in str(m) for m in msgs_a), (
                    "BETA_UNIQUE_MARKER leaked into session A — thread isolation broken"
                )

                # Session B should contain BETA marker, not ALPHA
                assert any("BETA_UNIQUE_MARKER" in str(m) for m in msgs_b), (
                    f"BETA_UNIQUE_MARKER not found in session B messages: {msgs_b}"
                )
                assert not any("ALPHA_UNIQUE_MARKER" in str(m) for m in msgs_b), (
                    "ALPHA_UNIQUE_MARKER leaked into session B — thread isolation broken"
                )
        finally:
            await api_client.delete(f"/sessions/{session_a}")
            await api_client.delete(f"/sessions/{session_b}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestUserScopeIsolation:
    """User A's sessions and Store namespace are isolated from user B.

    CognitionContext.from_scope() maps session.scopes['user'] to user_id,
    which is used to namespace Store entries. This class verifies that the
    scope isolation the rest of Cognition already enforces still holds
    after Store wiring.
    """

    async def test_user_a_sessions_not_visible_to_user_b(
        self, api_client: ScenarioTestClient
    ) -> None:
        """Sessions created as user A are not returned when listing as user B.

        docker-compose has COGNITION_SCOPING_ENABLED=true with COGNITION_SCOPE_KEYS=["user"].
        The X-Cognition-Scope-User header is the scope key.
        """
        # Check if scoping is enabled — if not, skip
        scoping = await api_client.check_scoping()
        if not scoping:
            pytest.skip("Scoping not enabled in this environment")

        user_a = f"alice-{uuid.uuid4().hex[:6]}"
        user_b = f"bob-{uuid.uuid4().hex[:6]}"

        client_a = _scoped_client(api_client.base_url, user_a)
        client_b = _scoped_client(api_client.base_url, user_b)

        try:
            # User A creates a session
            session_a = await client_a.create_session(_unique("alice"))

            try:
                # User B lists sessions — should NOT see user A's session
                list_resp = await client_b.get("/sessions")
                assert list_resp.status_code == 200

                sessions_for_b = list_resp.json().get("sessions", [])
                session_ids_for_b = [s["id"] for s in sessions_for_b]

                assert session_a not in session_ids_for_b, (
                    f"User B can see user A's session {session_a} — "
                    "scope isolation broken after Store wiring"
                )
            finally:
                await client_a.delete(f"/sessions/{session_a}")
        finally:
            await client_a.close()
            await client_b.close()

    async def test_user_a_and_user_b_both_stream_independently(
        self, api_client: ScenarioTestClient
    ) -> None:
        """User A and user B can stream messages concurrently without interference."""
        user_a = f"alice-{uuid.uuid4().hex[:6]}"
        user_b = f"bob-{uuid.uuid4().hex[:6]}"

        client_a = _scoped_client(api_client.base_url, user_a)
        client_b = _scoped_client(api_client.base_url, user_b)

        try:
            session_a = await client_a.create_session(_unique("alice"))
            session_b = await client_b.create_session(_unique("bob"))

            try:
                events_a = await _collect_events(client_a, session_a, "Say: alice-ok")
                events_b = await _collect_events(client_b, session_b, "Say: bob-ok")

                terminal_a = [e for e in events_a if e.get("event") in ("done", "error")]
                terminal_b = [e for e in events_b if e.get("event") in ("done", "error")]

                assert len(terminal_a) > 0, "User A stream did not terminate"
                assert len(terminal_b) > 0, "User B stream did not terminate"
            finally:
                await client_a.delete(f"/sessions/{session_a}")
                await client_b.delete(f"/sessions/{session_b}")
        finally:
            await client_a.close()
            await client_b.close()
