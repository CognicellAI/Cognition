"""Regression tests for TokenEvent content normalisation.

LangChain's BaseMessage.content is typed ``str | list[str | dict]``.
Different providers use different formats:

* OpenAI / OpenAI-compatible: plain ``str``
* Bedrock Converse (streaming deltas): ``list[dict]`` — often WITHOUT a
  ``"type"`` key on individual delta chunks (e.g. ``[{"text": "J", "index": 0}]``)
* Bedrock Converse (stop/metadata events): empty ``str`` or ``list``
* Future / unknown providers: anything conforming to the BaseMessage contract

``TokenEvent.__post_init__`` must coerce all of these to a plain ``str``
so that every downstream consumer (accumulation, SSE serialisation, token
counting) always receives a string.
"""

from __future__ import annotations

import pytest
from langchain_core.messages import AIMessageChunk

from server.app.agent.runtime import TokenEvent


# ---------------------------------------------------------------------------
# Helpers — build chunks the way each provider does
# ---------------------------------------------------------------------------


def openai_chunk(text: str) -> AIMessageChunk:
    """OpenAI / OpenAI-compatible streaming delta — content is always str."""
    return AIMessageChunk(content=text)


def bedrock_delta_chunk(text: str, index: int = 0) -> AIMessageChunk:
    """Bedrock Converse streaming delta — list[dict] WITHOUT a 'type' key.

    This is the format that triggered the original bug:
    ``can only concatenate str (not "list") to str``
    """
    return AIMessageChunk(content=[{"text": text, "index": index}])


def bedrock_typed_chunk(text: str, index: int = 0) -> AIMessageChunk:
    """Bedrock Converse streaming delta — list[dict] WITH a 'type' key."""
    return AIMessageChunk(content=[{"type": "text", "text": text, "index": index}])


def bedrock_stop_chunk() -> AIMessageChunk:
    """Bedrock Converse stop / metadata event — empty str or empty list."""
    return AIMessageChunk(content="")


def bedrock_empty_list_chunk() -> AIMessageChunk:
    """Bedrock Converse contentBlockStop — empty list."""
    return AIMessageChunk(content=[])


# ---------------------------------------------------------------------------
# TokenEvent construction tests
# ---------------------------------------------------------------------------


class TestTokenEventNormalisation:
    """TokenEvent must always produce a str regardless of input format."""

    def test_openai_str_content_passthrough(self):
        """Plain str content is forwarded unchanged."""
        chunk = openai_chunk("Hello, world!")
        event = TokenEvent(content=chunk.text)
        assert event.content == "Hello, world!"
        assert isinstance(event.content, str)

    def test_bedrock_delta_no_type_key(self):
        """Bedrock delta chunk without 'type' key must be normalised to str.

        This is the exact format that caused the original crash.
        Real Bedrock Converse streaming: most deltas after the first have no
        "type" key — only {"text": "...", "index": N}.
        ``BaseMessage.text`` silently drops these; ``_content_to_str`` must not.
        """
        chunk = bedrock_delta_chunk("Hello")
        # Verify the chunk really does have list content (the dangerous format)
        assert isinstance(chunk.content, list)
        # Verify that BaseMessage.text drops this (the root cause of the bug)
        assert chunk.text == "", "Expected .text to drop typeless blocks (confirms the bug)"

        event = TokenEvent(content=chunk.content)  # type: ignore[arg-type]
        assert event.content == "Hello"
        assert isinstance(event.content, str)

    def test_bedrock_delta_with_type_key(self):
        """Bedrock delta chunk with explicit 'type': 'text' key."""
        chunk = bedrock_typed_chunk("World")
        assert isinstance(chunk.content, list)

        event = TokenEvent(content=chunk.text)
        assert event.content == "World"
        assert isinstance(event.content, str)

    def test_bedrock_stop_chunk_empty_str(self):
        """Empty-string stop chunk produces empty string, not crash."""
        chunk = bedrock_stop_chunk()
        event = TokenEvent(content=chunk.text)
        assert event.content == ""
        assert isinstance(event.content, str)

    def test_bedrock_empty_list_chunk(self):
        """Empty-list contentBlockStop produces empty string."""
        chunk = bedrock_empty_list_chunk()
        event = TokenEvent(content=chunk.text)
        assert event.content == ""
        assert isinstance(event.content, str)

    def test_post_init_coerces_raw_list_directly(self):
        """__post_init__ acts as last-resort barrier even if caller bypasses normalisation.

        If some future code path passes chunk.content (a list) directly to
        TokenEvent instead of going through _content_to_str, __post_init__
        must still coerce it — including the typeless Bedrock delta format.
        """
        raw_list_content = [{"text": "Safety net", "index": 0}]
        event = TokenEvent(content=raw_list_content)  # type: ignore[arg-type]
        assert event.content == "Safety net"
        assert isinstance(event.content, str)

    def test_post_init_coerces_list_with_type_key(self):
        """__post_init__ handles typed content blocks directly."""
        raw_list_content = [{"type": "text", "text": "Barrier works"}]
        event = TokenEvent(content=raw_list_content)  # type: ignore[arg-type]
        assert event.content == "Barrier works"
        assert isinstance(event.content, str)

    def test_post_init_coerces_empty_list(self):
        """__post_init__ handles empty list → empty string."""
        event = TokenEvent(content=[])  # type: ignore[arg-type]
        assert event.content == ""
        assert isinstance(event.content, str)

    def test_post_init_skips_coercion_for_str(self):
        """__post_init__ must not import langchain for plain str inputs (performance)."""
        # We can't easily assert the import didn't happen, but we can verify
        # the value is unchanged and no exception is raised.
        event = TokenEvent(content="plain string")
        assert event.content == "plain string"

    def test_multiblock_content_concatenated(self):
        """Multiple text blocks in a single chunk are concatenated in order."""
        raw = [
            {"type": "text", "text": "Hello", "index": 0},
            {"type": "text", "text": ", world", "index": 1},
        ]
        event = TokenEvent(content=raw)  # type: ignore[arg-type]
        assert event.content == "Hello, world"

    def test_non_text_blocks_ignored(self):
        """Non-text content blocks (images, tool calls, etc.) are dropped."""
        raw = [
            {"type": "image", "source": {"type": "base64", "data": "..."}},
            {"type": "text", "text": "text only"},
            {"type": "tool_call", "id": "xyz"},
        ]
        event = TokenEvent(content=raw)  # type: ignore[arg-type]
        assert event.content == "text only"

    def test_downstream_accumulation_safe(self):
        """TokenEvent.content can always be concatenated — the crash scenario."""
        accumulated = ""
        chunks = [
            bedrock_delta_chunk("Hello"),
            bedrock_delta_chunk(", "),
            bedrock_delta_chunk("world"),
            bedrock_stop_chunk(),
        ]
        for chunk in chunks:
            # Simulate what runtime.py does: _content_to_str then TokenEvent
            # This is the exact pattern from deep_agent_service.py that crashed
            accumulated += TokenEvent(content=chunk.content).content  # type: ignore[arg-type]
        assert accumulated == "Hello, world"

    def test_downstream_split_safe(self):
        """TokenEvent.content.split() never raises — the second crash scenario."""
        chunk = bedrock_delta_chunk("count these words please")
        event = TokenEvent(content=chunk.content)  # type: ignore[arg-type]
        # This was the second bomb: len(event.content.split()) on a list
        count = len(event.content.split())
        assert count == 4
