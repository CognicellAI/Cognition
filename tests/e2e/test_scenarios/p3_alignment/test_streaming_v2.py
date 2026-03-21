"""E2E Scenarios: Streaming v2 correctness (Phase 2 — #34).

As a builder integrating Cognition's SSE stream into my frontend,
I want tool call events to have stable correlatable IDs and all streams
to terminate cleanly,
so that my UI can match tool call spinners to their results and never
display a hung loading state.

Business Value:
- Tool call start/result events are correlated by a stable ID (fixed: was
  using CPython id(data) — a memory address that differed between events)
- Every stream terminates with a done or error event
- Token events always carry content
- SSE event payloads are always valid JSON

Run against: docker-compose environment at http://localhost:8000
"""

from __future__ import annotations

import json
import uuid

import pytest

from tests.e2e.test_scenarios.conftest import ScenarioTestClient


def _unique(prefix: str = "session") -> str:
    return f"{prefix}-stream-{uuid.uuid4().hex[:8]}"


async def _collect_events(
    api_client: ScenarioTestClient,
    session_id: str,
    content: str,
    timeout: float = 30.0,
    max_events: int = 200,
) -> list[dict]:
    """Stream SSE events from a message send and parse into dicts.

    Each returned dict has an ``event`` key (SSE event type) plus
    the JSON payload fields.
    """
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
                    if len(events) >= max_events:
                        break
    except Exception:
        pass

    return events


@pytest.mark.asyncio
@pytest.mark.e2e
class TestStreamTermination:
    """Every stream must end with a done or error event — no hung streams.

    The v2 astream() rewrite (Phase 2) ensures the generator always yields
    DoneEvent or ErrorEvent. This class verifies that guarantee against a
    real server with a real LLM.
    """

    async def test_simple_message_ends_with_done(self, api_client: ScenarioTestClient) -> None:
        """A simple question produces a stream that terminates with done."""
        session_id = await api_client.create_session(_unique())

        try:
            events = await _collect_events(api_client, session_id, "Reply with exactly: pong")

            terminal = [e for e in events if e.get("event") in ("done", "error")]
            assert len(terminal) >= 1, (
                f"Stream did not terminate. Got event types: {[e.get('event') for e in events]}"
            )
            assert terminal[-1]["event"] in ("done", "error")
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_multiple_messages_all_terminate(self, api_client: ScenarioTestClient) -> None:
        """Three consecutive messages all produce terminated streams."""
        session_id = await api_client.create_session(_unique())

        try:
            prompts = [
                "Say: one",
                "Say: two",
                "Say: three",
            ]
            for prompt in prompts:
                events = await _collect_events(api_client, session_id, prompt)
                terminal = [e for e in events if e.get("event") in ("done", "error")]
                assert len(terminal) >= 1, (
                    f"Stream for '{prompt}' did not terminate. "
                    f"Events: {[e.get('event') for e in events]}"
                )
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_done_is_last_event(self, api_client: ScenarioTestClient) -> None:
        """The done event is always the final event in a stream."""
        session_id = await api_client.create_session(_unique())

        try:
            events = await _collect_events(api_client, session_id, "Say: hi")

            if events and events[-1].get("event") == "done":
                # done arrived last — as expected
                pass
            else:
                # Find the first done and verify nothing comes after it
                done_indices = [i for i, e in enumerate(events) if e.get("event") == "done"]
                if done_indices:
                    first_done = done_indices[0]
                    assert first_done == len(events) - 1, (
                        f"done event at index {first_done} but {len(events) - 1 - first_done} "
                        f"events followed it"
                    )
        finally:
            await api_client.delete(f"/sessions/{session_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestTokenEventIntegrity:
    """Token events must carry non-empty content and form a coherent response."""

    async def test_token_events_have_content_field(self, api_client: ScenarioTestClient) -> None:
        """Every token event has a non-empty content field."""
        session_id = await api_client.create_session(_unique())

        try:
            events = await _collect_events(api_client, session_id, "Count to three: 1, 2, 3")

            token_events = [e for e in events if e.get("event") == "token"]
            assert len(token_events) > 0, "Expected at least one token event"

            for tok in token_events:
                assert "content" in tok, f"Token event missing content field: {tok}"
                assert isinstance(tok["content"], str), (
                    f"Token content must be str, got {type(tok['content'])}"
                )
                assert len(tok["content"]) > 0, "Token event has empty content"
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_concatenated_tokens_form_response(self, api_client: ScenarioTestClient) -> None:
        """Token events concatenated produce a non-empty response string."""
        session_id = await api_client.create_session(_unique())

        try:
            events = await _collect_events(
                api_client, session_id, "Reply with exactly the word: hello"
            )

            token_events = [e for e in events if e.get("event") == "token"]
            assembled = "".join(e.get("content", "") for e in token_events)

            assert len(assembled) > 0, "Assembled token content is empty"
        finally:
            await api_client.delete(f"/sessions/{session_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestEventPayloadValidity:
    """All SSE data payloads must be valid JSON with required fields."""

    async def test_all_data_lines_are_valid_json(self, api_client: ScenarioTestClient) -> None:
        """Every data: line in the SSE stream is valid JSON."""
        session_id = await api_client.create_session(_unique())

        raw_data_lines: list[str] = []

        try:
            async with api_client.client.stream(
                "POST",
                f"{api_client.base_url}/sessions/{session_id}/messages",
                json={"content": "Say: ok"},
                headers={
                    "Accept": "text/event-stream",
                    "Content-Type": "application/json",
                    **api_client.scope_header,
                },
                timeout=30.0,
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line.startswith("data: "):
                        raw_data_lines.append(line[6:])
                        if len(raw_data_lines) > 100:
                            break
        except Exception:
            pass
        finally:
            await api_client.delete(f"/sessions/{session_id}")

        assert len(raw_data_lines) > 0, "No data lines received"

        for raw in raw_data_lines:
            try:
                json.loads(raw)
            except json.JSONDecodeError as exc:
                pytest.fail(f"Invalid JSON in SSE data line: {raw!r} — {exc}")

    async def test_events_have_event_type(self, api_client: ScenarioTestClient) -> None:
        """Every parsed event dict has an event type field."""
        session_id = await api_client.create_session(_unique())

        try:
            events = await _collect_events(api_client, session_id, "Say: ok")

            for evt in events:
                assert "event" in evt, f"Event missing 'event' key: {evt}"
                assert isinstance(evt["event"], str), f"event type must be str: {evt}"
                assert len(evt["event"]) > 0, f"Empty event type: {evt}"
        finally:
            await api_client.delete(f"/sessions/{session_id}")


@pytest.mark.asyncio
@pytest.mark.e2e
class TestToolCallIdCorrelation:
    """Tool call start and result events share a stable tool_call_id.

    This was broken before Phase 2: the old astream_events() used
    id(data) — a CPython memory address — which differed between the
    on_tool_start and on_tool_end events. After the astream() v2 rewrite,
    tool_call_id comes from the real LangGraph-assigned ID.
    """

    async def test_tool_call_and_result_share_id(self, api_client: ScenarioTestClient) -> None:
        """tool_call and tool_result events for the same invocation share tool_call_id."""
        session_id = await api_client.create_session(_unique())

        try:
            # Explicitly instruct the model to use a specific tool — avoids the model
            # answering from general knowledge without invoking any tool
            events = await _collect_events(
                api_client,
                session_id,
                "Use the ls tool to list files in the current directory and show me what you find.",
                timeout=45.0,
            )

            tool_calls = [e for e in events if e.get("event") == "tool_call"]
            tool_results = [e for e in events if e.get("event") == "tool_result"]

            if not tool_calls:
                pytest.skip(
                    "No tool_call events emitted — model may not have invoked a tool. "
                    "Run against a model that uses tools."
                )

            # tool_call events use 'id' as the key (EventBuilder.tool_call serialization)
            for tc in tool_calls:
                assert "id" in tc, f"tool_call event missing 'id' field: {tc}"
                assert tc["id"], "tool_call 'id' field is empty"

            # tool_result events use 'tool_call_id' as the key
            for tr in tool_results:
                assert "tool_call_id" in tr, f"tool_result event missing 'tool_call_id': {tr}"
                assert tr["tool_call_id"], "tool_call_id is empty"

            # The IDs must overlap — results correlate to calls
            call_ids = {tc["id"] for tc in tool_calls}
            result_ids = {tr["tool_call_id"] for tr in tool_results}

            assert call_ids & result_ids, (
                f"No matching IDs between tool_calls {call_ids} and tool_results {result_ids}. "
                "This indicates broken tool call correlation."
            )
        finally:
            await api_client.delete(f"/sessions/{session_id}")

    async def test_tool_call_precedes_its_result(self, api_client: ScenarioTestClient) -> None:
        """For each tool_call_id, the tool_call event precedes the tool_result event."""
        session_id = await api_client.create_session(_unique())

        try:
            events = await _collect_events(
                api_client,
                session_id,
                "Use the ls tool to list files in the current directory.",
                timeout=45.0,
            )

            tool_calls = [e for e in events if e.get("event") == "tool_call"]
            tool_results = [e for e in events if e.get("event") == "tool_result"]

            if not tool_calls:
                pytest.skip("No tool_call events — model did not use tools")

            event_list = list(events)

            for tc in tool_calls:
                # tool_call events use 'id', tool_result events use 'tool_call_id'
                tc_id = tc.get("id")
                if not tc_id:
                    continue

                matching_results = [tr for tr in tool_results if tr.get("tool_call_id") == tc_id]
                if not matching_results:
                    continue

                tc_idx = event_list.index(tc)
                tr_idx = event_list.index(matching_results[0])

                assert tc_idx < tr_idx, (
                    f"tool_call (idx={tc_idx}) came AFTER tool_result (idx={tr_idx}) for id={tc_id}"
                )
        finally:
            await api_client.delete(f"/sessions/{session_id}")
