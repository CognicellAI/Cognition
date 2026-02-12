"""Tests for internal protocol (server ↔ agent messages)."""

from __future__ import annotations

import pytest

from shared.protocol.internal import (
    AgentReadyEvent,
    AgentStartMessage,
    AssistantMessageEvent,
    CancelMessage,
    DoneEvent,
    ErrorEvent,
    ShutdownMessage,
    ToolEndEvent,
    ToolOutputEvent,
    ToolStartEvent,
    UserMessage,
    parse_agent_event,
    parse_server_message,
    serialize_message,
)


class TestProtocolSerialization:
    """Test message serialization and deserialization."""

    def test_serialize_user_message(self):
        """Test UserMessage serialization."""
        msg = UserMessage(
            session_id="test-session",
            content="Hello, agent!",
            turn_number=1,
        )
        json_str = serialize_message(msg)

        assert '"type": "user_message"' in json_str
        assert '"session_id": "test-session"' in json_str
        assert '"content": "Hello, agent!"' in json_str
        assert '"turn_number": 1' in json_str

    def test_serialize_agent_start(self):
        """Test AgentStartMessage serialization."""
        msg = AgentStartMessage(
            session_id="test-session",
            project_id="test-project",
            workspace_path="/workspace/repo",
            llm_provider="openai",
            llm_model="gpt-4",
            llm_temperature=0.7,
            max_iterations=50,
        )
        json_str = serialize_message(msg)

        assert '"type": "agent_start"' in json_str
        assert '"llm_provider": "openai"' in json_str
        assert '"llm_model": "gpt-4"' in json_str

    def test_serialize_assistant_message_event(self):
        """Test AssistantMessageEvent serialization."""
        event = AssistantMessageEvent(
            session_id="test-session",
            content="Hello, user!",
            is_complete=True,
        )
        json_str = serialize_message(event)

        assert '"event": "assistant_message"' in json_str
        assert '"is_complete": true' in json_str

    def test_serialize_tool_events(self):
        """Test tool event serialization."""
        start = ToolStartEvent(
            session_id="test-session",
            tool_name="run_tests",
            tool_args={"cmd": "pytest"},
        )
        output = ToolOutputEvent(
            session_id="test-session",
            stream="stdout",
            content="Test output",
        )
        end = ToolEndEvent(
            session_id="test-session",
            tool_name="run_tests",
            exit_code=0,
        )

        assert '"event": "tool_start"' in serialize_message(start)
        assert '"event": "tool_output"' in serialize_message(output)
        assert '"event": "tool_end"' in serialize_message(end)


class TestProtocolParsing:
    """Test message parsing from JSON."""

    def test_parse_user_message(self):
        """Test parsing UserMessage from JSON."""
        json_str = (
            '{"type": "user_message", "session_id": "s1", "content": "Hello", "turn_number": 1}'
        )
        msg = parse_server_message(json_str)

        assert isinstance(msg, UserMessage)
        assert msg.session_id == "s1"
        assert msg.content == "Hello"
        assert msg.turn_number == 1

    def test_parse_cancel_message(self):
        """Test parsing CancelMessage from JSON."""
        json_str = '{"type": "cancel", "session_id": "s1"}'
        msg = parse_server_message(json_str)

        assert isinstance(msg, CancelMessage)
        assert msg.session_id == "s1"

    def test_parse_shutdown_message(self):
        """Test parsing ShutdownMessage from JSON."""
        json_str = '{"type": "shutdown", "session_id": "s1"}'
        msg = parse_server_message(json_str)

        assert isinstance(msg, ShutdownMessage)

    def test_parse_agent_ready_event(self):
        """Test parsing AgentReadyEvent from JSON."""
        json_str = '{"event": "agent_ready", "session_id": "s1"}'
        event = parse_agent_event(json_str)

        assert isinstance(event, AgentReadyEvent)
        assert event.session_id == "s1"

    def test_parse_assistant_message_event(self):
        """Test parsing AssistantMessageEvent from JSON."""
        json_str = '{"event": "assistant_message", "session_id": "s1", "content": "Hi", "is_complete": false}'
        event = parse_agent_event(json_str)

        assert isinstance(event, AssistantMessageEvent)
        assert event.content == "Hi"
        assert event.is_complete is False

    def test_parse_error_event(self):
        """Test parsing ErrorEvent from JSON."""
        json_str = '{"event": "error", "session_id": "s1", "message": "Something went wrong", "error_type": "TestError"}'
        event = parse_agent_event(json_str)

        assert isinstance(event, ErrorEvent)
        assert event.message == "Something went wrong"
        assert event.error_type == "TestError"

    def test_parse_done_event(self):
        """Test parsing DoneEvent from JSON."""
        json_str = '{"event": "done", "session_id": "s1"}'
        event = parse_agent_event(json_str)

        assert isinstance(event, DoneEvent)

    def test_parse_unknown_server_message_type(self):
        """Test parsing unknown server message type raises error."""
        json_str = '{"type": "unknown", "session_id": "s1"}'

        with pytest.raises(ValueError, match="Unknown server message type"):
            parse_server_message(json_str)

    def test_parse_unknown_agent_event_type(self):
        """Test parsing unknown agent event type raises error."""
        json_str = '{"event": "unknown", "session_id": "s1"}'

        with pytest.raises(ValueError, match="Unknown agent event type"):
            parse_agent_event(json_str)


class TestProtocolRoundTrip:
    """Test serialize → parse round trips."""

    def test_user_message_round_trip(self):
        """Test UserMessage survives serialize → parse."""
        original = UserMessage(
            session_id="test",
            content="Hello",
            turn_number=5,
        )
        json_str = serialize_message(original)
        parsed = parse_server_message(json_str)

        assert parsed.session_id == original.session_id
        assert parsed.content == original.content
        assert parsed.turn_number == original.turn_number

    def test_assistant_event_round_trip(self):
        """Test AssistantMessageEvent survives serialize → parse."""
        original = AssistantMessageEvent(
            session_id="test",
            content="Response",
            is_complete=True,
        )
        json_str = serialize_message(original)
        parsed = parse_agent_event(json_str)

        assert parsed.session_id == original.session_id
        assert parsed.content == original.content
        assert parsed.is_complete == original.is_complete
