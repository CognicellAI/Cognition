"""Unit tests for DeepAgentRuntime.astream_events() v2 streaming translation.

These tests verify that the LangGraph v2 astream() chunks are correctly
translated to the canonical AgentEvent domain model:

  - AIMessageChunk.content         → TokenEvent
  - AIMessageChunk.tool_call_chunks→ ToolCallEvent (with real IDs)
  - ToolMessage                    → ToolResultEvent (correlated by real ID)
  - subgraph namespace non-empty   → DelegationEvent (once per subagent)
  - custom chunk {"status": "..."}  → StatusEvent
  - stream end                     → DoneEvent
  - exception                      → ErrorEvent

The key regression covered here is tool_call_id correlation: the old
astream_events() used id(data) — a CPython memory address — which produced
different IDs for on_tool_start vs on_tool_end, making them impossible to
correlate. The new implementation uses the real LangGraph-assigned IDs.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessageChunk, ToolMessage

from server.app.agent.runtime import (
    DeepAgentRuntime,
    DelegationEvent,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_chunk(
    chunk_type: str,
    data: Any,
    ns: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Build a v2 StreamPart dict."""
    return {"type": chunk_type, "ns": ns, "data": data}


def _ai_token(content: str) -> dict[str, Any]:
    """Build a messages-mode chunk carrying a token."""
    msg = AIMessageChunk(content=content)
    return _make_chunk("messages", (msg, {}))


def _ai_tool_call_chunk(
    tool_call_id: str,
    name: str = "",
    args: str = "",
    index: int = 0,
) -> dict[str, Any]:
    """Build a messages-mode chunk carrying a tool call fragment."""
    tc_chunk = {"id": tool_call_id, "name": name, "args": args, "index": index}
    msg = AIMessageChunk(content="", tool_call_chunks=[tc_chunk])
    return _make_chunk("messages", (msg, {}))


def _tool_result(tool_call_id: str, content: str, name: str = "my_tool") -> dict[str, Any]:
    """Build a messages-mode chunk carrying a ToolMessage (tool result)."""
    msg = ToolMessage(
        content=content,
        tool_call_id=tool_call_id,
        name=name,
    )
    return _make_chunk("messages", (msg, {}))


def _subagent_update(subagent_ns: str = "tools:call_abc123") -> dict[str, Any]:
    """Build an updates-mode chunk from a subagent namespace."""
    return _make_chunk(
        "updates",
        {"model_request": {"messages": []}},
        ns=(subagent_ns,),
    )


def _custom_status(status: str) -> dict[str, Any]:
    """Build a custom-mode status chunk."""
    return _make_chunk("custom", {"status": status})


async def _stream(*chunks: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
    """Fake async iterator over pre-built v2 chunks."""
    for chunk in chunks:
        yield chunk


def _make_runtime(*chunks: dict[str, Any]) -> DeepAgentRuntime:
    """Create a DeepAgentRuntime whose underlying agent yields the given chunks."""
    mock_agent = MagicMock()
    mock_agent.astream = MagicMock(return_value=_stream(*chunks))
    mock_checkpointer = MagicMock()
    return DeepAgentRuntime(
        agent=mock_agent,
        checkpointer=mock_checkpointer,
        thread_id="test-thread",
    )


async def _collect(runtime: DeepAgentRuntime) -> list[Any]:
    """Drain astream_events into a list."""
    events: list[Any] = []
    async for event in runtime.astream_events("hello", thread_id="test-thread"):
        events.append(event)
    return events


# ---------------------------------------------------------------------------
# Token streaming
# ---------------------------------------------------------------------------


class TestTokenStreaming:
    @pytest.mark.asyncio
    async def test_ai_content_yields_token_event(self):
        runtime = _make_runtime(_ai_token("Hello"))
        events = await _collect(runtime)
        token_events = [e for e in events if isinstance(e, TokenEvent)]
        assert len(token_events) == 1
        assert token_events[0].content == "Hello"

    @pytest.mark.asyncio
    async def test_multiple_tokens_in_order(self):
        runtime = _make_runtime(
            _ai_token("Hello"),
            _ai_token(", "),
            _ai_token("world"),
        )
        events = await _collect(runtime)
        token_events = [e for e in events if isinstance(e, TokenEvent)]
        assert [e.content for e in token_events] == ["Hello", ", ", "world"]

    @pytest.mark.asyncio
    async def test_empty_content_not_emitted(self):
        msg = AIMessageChunk(content="")
        runtime = _make_runtime(_make_chunk("messages", (msg, {})))
        events = await _collect(runtime)
        token_events = [e for e in events if isinstance(e, TokenEvent)]
        assert len(token_events) == 0

    @pytest.mark.asyncio
    async def test_always_ends_with_done(self):
        runtime = _make_runtime(_ai_token("hi"))
        events = await _collect(runtime)
        assert isinstance(events[-1], DoneEvent)


# ---------------------------------------------------------------------------
# Tool call ID correlation (the primary regression this fixes)
# ---------------------------------------------------------------------------


class TestToolCallIdCorrelation:
    @pytest.mark.asyncio
    async def test_tool_call_event_emitted_on_first_name_chunk(self):
        """ToolCallEvent is emitted when the tool name first appears in a chunk."""
        runtime = _make_runtime(
            _ai_tool_call_chunk("call_xyz", name="read_file", args='{"path":'),
            _ai_tool_call_chunk("call_xyz", name="", args='"foo.py"}'),
        )
        events = await _collect(runtime)
        tool_call_events = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(tool_call_events) == 1
        tc = tool_call_events[0]
        assert tc.name == "read_file"
        assert tc.tool_call_id == "call_xyz"

    @pytest.mark.asyncio
    async def test_tool_result_uses_real_id(self):
        """ToolResultEvent.tool_call_id matches the real LangGraph-assigned ID."""
        runtime = _make_runtime(
            _ai_tool_call_chunk("call_real_id", name="write_file"),
            _tool_result("call_real_id", "wrote 42 bytes"),
        )
        events = await _collect(runtime)
        result_events = [e for e in events if isinstance(e, ToolResultEvent)]
        assert len(result_events) == 1
        assert result_events[0].tool_call_id == "call_real_id"
        assert result_events[0].output == "wrote 42 bytes"

    @pytest.mark.asyncio
    async def test_tool_call_and_result_share_same_id(self):
        """ToolCallEvent.tool_call_id == ToolResultEvent.tool_call_id for the same call."""
        call_id = "call_correlate_me"
        runtime = _make_runtime(
            _ai_tool_call_chunk(call_id, name="search"),
            _tool_result(call_id, "results here"),
        )
        events = await _collect(runtime)
        call_event = next(e for e in events if isinstance(e, ToolCallEvent))
        result_event = next(e for e in events if isinstance(e, ToolResultEvent))
        assert call_event.tool_call_id == result_event.tool_call_id == call_id

    @pytest.mark.asyncio
    async def test_multiple_tool_calls_distinct_ids(self):
        """Multiple tool calls in one turn each get their own distinct IDs."""
        runtime = _make_runtime(
            _ai_tool_call_chunk("call_1", name="tool_a"),
            _ai_tool_call_chunk("call_2", name="tool_b"),
            _tool_result("call_1", "result_a"),
            _tool_result("call_2", "result_b"),
        )
        events = await _collect(runtime)
        call_events = [e for e in events if isinstance(e, ToolCallEvent)]
        result_events = [e for e in events if isinstance(e, ToolResultEvent)]

        assert len(call_events) == 2
        assert len(result_events) == 2

        call_ids = {e.tool_call_id for e in call_events}
        result_ids = {e.tool_call_id for e in result_events}
        assert call_ids == result_ids == {"call_1", "call_2"}

    @pytest.mark.asyncio
    async def test_chunk_without_id_is_ignored(self):
        """Tool call chunks with no id are silently ignored."""
        tc_chunk = {"id": None, "name": "broken_tool", "args": "", "index": 0}
        msg = AIMessageChunk(content="", tool_call_chunks=[tc_chunk])
        runtime = _make_runtime(_make_chunk("messages", (msg, {})))
        events = await _collect(runtime)
        call_events = [e for e in events if isinstance(e, ToolCallEvent)]
        assert len(call_events) == 0


# ---------------------------------------------------------------------------
# Subagent delegation
# ---------------------------------------------------------------------------


class TestSubagentDelegation:
    @pytest.mark.asyncio
    async def test_subagent_activity_yields_delegation_event(self):
        """Non-empty namespace in updates chunk → DelegationEvent."""
        runtime = _make_runtime(_subagent_update("tools:call_sub123"))
        events = await _collect(runtime)
        delegation_events = [e for e in events if isinstance(e, DelegationEvent)]
        assert len(delegation_events) == 1
        assert delegation_events[0].from_agent == "main"
        assert delegation_events[0].to_agent == "subagent"

    @pytest.mark.asyncio
    async def test_delegation_event_emitted_only_once_per_subagent(self):
        """Multiple chunks from the same subagent emit DelegationEvent only once."""
        runtime = _make_runtime(
            _subagent_update("tools:call_once"),
            _subagent_update("tools:call_once"),
            _subagent_update("tools:call_once"),
        )
        events = await _collect(runtime)
        delegation_events = [e for e in events if isinstance(e, DelegationEvent)]
        assert len(delegation_events) == 1

    @pytest.mark.asyncio
    async def test_main_agent_updates_no_delegation(self):
        """Updates with empty namespace (main agent) do not produce DelegationEvent."""
        runtime = _make_runtime(
            _make_chunk("updates", {"model_request": {}}, ns=()),
        )
        events = await _collect(runtime)
        delegation_events = [e for e in events if isinstance(e, DelegationEvent)]
        assert len(delegation_events) == 0

    @pytest.mark.asyncio
    async def test_multiple_subagents_each_get_delegation(self):
        """Distinct subagent namespaces each produce their own DelegationEvent."""
        runtime = _make_runtime(
            _subagent_update("tools:call_sub_a"),
            _subagent_update("tools:call_sub_b"),
        )
        events = await _collect(runtime)
        delegation_events = [e for e in events if isinstance(e, DelegationEvent)]
        assert len(delegation_events) == 2


# ---------------------------------------------------------------------------
# Status / custom events
# ---------------------------------------------------------------------------


class TestStatusEvents:
    @pytest.mark.asyncio
    async def test_custom_status_chunk_yields_status_event(self):
        runtime = _make_runtime(_custom_status("thinking"))
        events = await _collect(runtime)
        status_events = [e for e in events if isinstance(e, StatusEvent)]
        assert len(status_events) == 1
        assert status_events[0].status == "thinking"

    @pytest.mark.asyncio
    async def test_custom_chunk_without_status_ignored(self):
        runtime = _make_runtime(
            _make_chunk("custom", {"progress": 50}),
        )
        events = await _collect(runtime)
        status_events = [e for e in events if isinstance(e, StatusEvent)]
        assert len(status_events) == 0

    @pytest.mark.asyncio
    async def test_custom_non_dict_ignored(self):
        runtime = _make_runtime(
            _make_chunk("custom", "bare string"),
        )
        events = await _collect(runtime)
        status_events = [e for e in events if isinstance(e, StatusEvent)]
        assert len(status_events) == 0


# ---------------------------------------------------------------------------
# Abort handling
# ---------------------------------------------------------------------------


class TestAbortHandling:
    @pytest.mark.asyncio
    async def test_abort_before_stream_yields_error(self):
        runtime = _make_runtime(_ai_token("should not see this"))
        await runtime.abort("test-thread")
        events = await _collect(runtime)
        assert len(events) == 1
        assert isinstance(events[0], ErrorEvent)
        assert events[0].code == "ABORTED"

    @pytest.mark.asyncio
    async def test_abort_mid_stream_stops_execution(self):
        """If aborted mid-stream, an ErrorEvent is emitted and iteration stops."""
        # We'll inject the abort signal by having the runtime abort itself
        # after yielding the first token.
        mock_agent = MagicMock()
        runtime = DeepAgentRuntime(
            agent=mock_agent,
            checkpointer=MagicMock(),
            thread_id="test-thread",
        )

        async def _chunks_with_abort() -> AsyncIterator[dict[str, Any]]:
            yield _ai_token("first")
            runtime._aborted.add("test-thread")  # simulate external abort
            yield _ai_token("second")  # should not reach caller

        mock_agent.astream = MagicMock(return_value=_chunks_with_abort())
        events = await _collect(runtime)

        # Should have gotten the first token, then an abort error, not the second token
        token_events = [e for e in events if isinstance(e, TokenEvent)]
        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(token_events) == 1
        assert token_events[0].content == "first"
        assert len(error_events) == 1
        assert error_events[0].code == "ABORTED"


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


class TestErrorHandling:
    @pytest.mark.asyncio
    async def test_exception_in_stream_yields_error_event(self):
        mock_agent = MagicMock()

        async def _exploding_stream() -> AsyncIterator[dict[str, Any]]:
            yield _ai_token("before error")
            raise RuntimeError("something went wrong")

        mock_agent.astream = MagicMock(return_value=_exploding_stream())
        runtime = DeepAgentRuntime(
            agent=mock_agent,
            checkpointer=MagicMock(),
            thread_id="test-thread",
        )
        events = await _collect(runtime)
        error_events = [e for e in events if isinstance(e, ErrorEvent)]
        assert len(error_events) == 1
        assert error_events[0].code == "RUNTIME_ERROR"
        assert "something went wrong" in error_events[0].message
