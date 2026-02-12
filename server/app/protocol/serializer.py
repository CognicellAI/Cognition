"""JSON serialization helpers for protocol messages and events."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from server.app.protocol.events import ServerEvent
    from server.app.protocol.messages import ClientMessage


def serialize_event(event: ServerEvent) -> str:
    """Serialize a server event to JSON string."""
    return event.model_dump_json()


def deserialize_message(data: str | bytes) -> ClientMessage:
    """Deserialize a client message from JSON string."""
    obj = json.loads(data)
    msg_type = obj.get("type")

    if msg_type == "create_session":
        from server.app.protocol.messages import CreateSessionRequest

        return CreateSessionRequest.model_validate(obj)
    elif msg_type == "user_msg":
        from server.app.protocol.messages import UserMessage

        return UserMessage.model_validate(obj)
    else:
        raise ValueError(f"Unknown message type: {msg_type}")


def parse_json(data: str | bytes) -> dict[str, Any]:
    """Parse JSON data."""
    return json.loads(data)
