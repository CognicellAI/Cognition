"""Tests for protocol messages and events."""

import json

import pytest

from server.app.protocol.events import (
    AssistantMessage,
    Error,
    SessionStarted,
    ToolEnd,
    ToolOutput,
    ToolStart,
)
from server.app.protocol.messages import CreateSessionRequest, UserMessage
from server.app.protocol.serializer import deserialize_message, serialize_event


class TestMessageSerialization:
    """Test message serialization."""

    def test_create_session_request(self) -> None:
        """Test CreateSessionRequest serialization."""
        msg = CreateSessionRequest(network_mode="OFF")
        data = msg.model_dump_json()
        parsed = json.loads(data)

        assert parsed["type"] == "create_session"
        assert parsed["network_mode"] == "OFF"

    def test_user_message(self) -> None:
        """Test UserMessage serialization."""
        msg = UserMessage(session_id="test-123", content="Hello")
        data = msg.model_dump_json()
        parsed = json.loads(data)

        assert parsed["type"] == "user_msg"
        assert parsed["session_id"] == "test-123"
        assert parsed["content"] == "Hello"

    def test_deserialize_create_session(self) -> None:
        """Test deserializing create_session message."""
        data = '{"type": "create_session", "network_mode": "ON"}'
        msg = deserialize_message(data)

        assert isinstance(msg, CreateSessionRequest)
        assert msg.network_mode == "ON"

    def test_deserialize_user_message(self) -> None:
        """Test deserializing user_msg."""
        data = '{"type": "user_msg", "session_id": "abc", "content": "test"}'
        msg = deserialize_message(data)

        assert isinstance(msg, UserMessage)
        assert msg.session_id == "abc"
        assert msg.content == "test"

    def test_deserialize_unknown_type(self) -> None:
        """Test deserializing unknown message type."""
        data = '{"type": "unknown"}'
        with pytest.raises(ValueError, match="Unknown message type"):
            deserialize_message(data)


class TestEventSerialization:
    """Test event serialization."""

    def test_session_started(self) -> None:
        """Test SessionStarted event."""
        event = SessionStarted(
            session_id="test-123",
            network_mode="OFF",
            workspace_path="/tmp/workspace",
        )
        data = serialize_event(event)
        parsed = json.loads(data)

        assert parsed["event"] == "session_started"
        assert parsed["session_id"] == "test-123"
        assert parsed["network_mode"] == "OFF"

    def test_assistant_message(self) -> None:
        """Test AssistantMessage event."""
        event = AssistantMessage(session_id="test-123", content="Hello world")
        data = serialize_event(event)
        parsed = json.loads(data)

        assert parsed["event"] == "assistant_message"
        assert parsed["content"] == "Hello world"

    def test_tool_start(self) -> None:
        """Test ToolStart event."""
        event = ToolStart(
            session_id="test-123",
            tool="read_file",
            input={"path": "test.py"},
        )
        data = serialize_event(event)
        parsed = json.loads(data)

        assert parsed["event"] == "tool_start"
        assert parsed["tool"] == "read_file"
        assert parsed["input"]["path"] == "test.py"

    def test_tool_output(self) -> None:
        """Test ToolOutput event."""
        event = ToolOutput(
            session_id="test-123",
            stream="stdout",
            chunk="test output",
        )
        data = serialize_event(event)
        parsed = json.loads(data)

        assert parsed["event"] == "tool_output"
        assert parsed["stream"] == "stdout"

    def test_tool_end(self) -> None:
        """Test ToolEnd event."""
        event = ToolEnd(
            session_id="test-123",
            tool="pytest",
            exit_code=0,
        )
        data = serialize_event(event)
        parsed = json.loads(data)

        assert parsed["event"] == "tool_end"
        assert parsed["exit_code"] == 0

    def test_error(self) -> None:
        """Test Error event."""
        event = Error(
            session_id="test-123",
            message="Something went wrong",
            code="TEST_ERROR",
        )
        data = serialize_event(event)
        parsed = json.loads(data)

        assert parsed["event"] == "error"
        assert parsed["message"] == "Something went wrong"
        assert parsed["code"] == "TEST_ERROR"
