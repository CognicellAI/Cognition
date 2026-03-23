"""Helpers for rebuilding the API message projection from checkpoint state."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from server.app.models import Message, ToolCall


def project_checkpoint_messages(session_id: str, checkpoint_messages: list[Any]) -> list[Message]:
    """Convert authoritative LangChain checkpoint messages into API messages."""
    projected: list[Message] = []
    current_parent_id: str | None = None
    base_time = datetime.now(UTC)

    for index, checkpoint_message in enumerate(checkpoint_messages, start=1):
        role: Literal["user", "assistant", "system", "tool"] | None = None
        tool_calls = None
        tool_call_id = None
        content = getattr(checkpoint_message, "content", None)

        if isinstance(checkpoint_message, HumanMessage):
            role = "user"
        elif isinstance(checkpoint_message, AIMessage):
            role = "assistant"
            raw_tool_calls = getattr(checkpoint_message, "tool_calls", None) or []
            if raw_tool_calls:
                tool_calls = [
                    ToolCall(
                        name=str(tool_call.get("name", "")),
                        args=dict(tool_call.get("args", {})),
                        id=str(tool_call.get("id", f"tool-call-{index}")),
                    )
                    for tool_call in raw_tool_calls
                ]
        elif isinstance(checkpoint_message, SystemMessage):
            role = "system"
        elif isinstance(checkpoint_message, ToolMessage):
            role = "tool"
            tool_call_id = checkpoint_message.tool_call_id
        else:
            continue

        message_id = f"{session_id}:projection:{index}"
        projected_message = Message(
            id=message_id,
            session_id=session_id,
            role=role,
            content=content if isinstance(content, str) or content is None else str(content),
            parent_id=current_parent_id if role in {"assistant", "tool"} else None,
            created_at=base_time + timedelta(microseconds=index),
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            metadata={"projection_source": "checkpoint"},
        )
        projected.append(projected_message)

        if role in {"user", "assistant"}:
            current_parent_id = message_id

    return projected
