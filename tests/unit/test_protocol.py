"""Unit tests for the protocol module."""

import json
from datetime import datetime

from shared import (
    CreateProject,
    MessageType,
    UserMessage,
    message_to_json,
    parse_message,
)


class TestProtocol:
    """Test suite for shared protocol."""

    def test_message_to_json_includes_type_field(self):
        """Test that serialization includes the aliased 'type' field."""
        msg = CreateProject(user_prefix="test")
        json_str = message_to_json(msg)
        data = json.loads(json_str)

        assert "type" in data
        assert data["type"] == MessageType.CREATE_PROJECT
        assert "msg_type" not in data  # Should be aliased

    def test_parse_message_from_json(self):
        """Test parsing JSON back to object."""
        json_str = json.dumps(
            {
                "type": "user_message",
                "session_id": "123",
                "content": "hello",
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

        msg = parse_message(json_str)
        assert isinstance(msg, UserMessage)
        assert msg.session_id == "123"
        assert msg.content == "hello"
        assert msg.msg_type == MessageType.USER_MESSAGE
