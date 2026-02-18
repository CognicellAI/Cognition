"""Server-Sent Events (SSE) utilities.

Helper functions and classes for streaming SSE events with reconnection support.

Features:
- Event ID generation for resumable streams
- Retry directive for client auto-reconnection
- Keepalive heartbeat events
- Last-Event-ID header support for stream resumption
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import AsyncGenerator, Any, Optional

from fastapi import Request
from fastapi.responses import StreamingResponse


@dataclass
class SSEEvent:
    """Represents a single SSE event with all optional fields."""

    event_type: str
    data: dict[str, Any]
    event_id: Optional[str] = None
    retry_ms: Optional[int] = None

    def format(self) -> str:
        """Format as SSE event string."""
        lines = []
        if self.event_id:
            lines.append(f"id: {self.event_id}")
        if self.retry_ms:
            lines.append(f"retry: {self.retry_ms}")
        lines.append(f"event: {self.event_type}")
        lines.append(f"data: {json.dumps(self.data)}")
        return "\n".join(lines) + "\n\n"


class SSEStream:
    """Helper class for streaming SSE events with reconnection support."""

    def __init__(self, retry_ms: int = 3000, heartbeat_interval: float = 15.0):
        """Initialize SSE stream with reconnection settings.

        Args:
            retry_ms: Retry delay in milliseconds for client auto-reconnect
            heartbeat_interval: Seconds between keepalive heartbeats
        """
        self.retry_ms = retry_ms
        self.heartbeat_interval = heartbeat_interval

    @staticmethod
    def format_event(
        event_type: str,
        data: dict,
        event_id: Optional[str] = None,
        retry_ms: Optional[int] = None,
    ) -> str:
        """Format an SSE event with optional ID and retry.

        Args:
            event_type: The event type (e.g., 'token', 'tool_call', 'done')
            data: The event data as a dictionary
            event_id: Optional event ID for resumption
            retry_ms: Optional retry directive in milliseconds

        Returns:
            Formatted SSE event string
        """
        lines = []
        if event_id:
            lines.append(f"id: {event_id}")
        if retry_ms is not None:
            lines.append(f"retry: {retry_ms}")
        lines.append(f"event: {event_type}")
        lines.append(f"data: {json.dumps(data)}")
        return "\n".join(lines) + "\n\n"

    @staticmethod
    def format_keepalive() -> str:
        """Format a keepalive comment (empty line that won't be parsed as event).

        Returns:
            Keepalive SSE comment string
        """
        return ":\n\n"

    async def event_generator(
        self,
        event_stream: AsyncGenerator[dict, None],
        request: Request,
        last_event_id: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """Generate SSE formatted events from an async generator.

        Includes:
        - Event ID generation for resumption
        - Retry directive on first event
        - Keepalive heartbeats during idle periods
        - Client disconnection detection

        Args:
            event_stream: Async generator yielding event dictionaries
            request: FastAPI request object for disconnection detection
            last_event_id: Optional ID to resume from (not yet implemented)

        Yields:
            Formatted SSE event strings
        """
        import asyncio

        event_counter = 0
        last_event_timestamp = datetime.utcnow()
        first_event = True

        try:
            async for event in event_stream:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                event_type = event.get("event", "message")
                data = event.get("data", {})

                # Generate event ID
                event_counter += 1
                event_id = f"{event_counter}-{uuid.uuid4().hex[:8]}"

                # Include retry directive on first event
                retry_ms = self.retry_ms if first_event else None
                first_event = False

                # Update timestamp
                last_event_timestamp = datetime.utcnow()

                yield self.format_event(event_type, data, event_id, retry_ms)

                # Send keepalive if enough time has passed since last event
                now = datetime.utcnow()
                elapsed = (now - last_event_timestamp).total_seconds()
                if elapsed >= self.heartbeat_interval:
                    yield self.format_keepalive()
                    last_event_timestamp = now

        except Exception as e:
            # Send error event with retry directive
            error_event_id = f"error-{uuid.uuid4().hex[:8]}"
            yield self.format_event(
                "error",
                {"message": str(e), "code": "STREAM_ERROR"},
                error_event_id,
                self.retry_ms,
            )

    def create_response(
        self,
        event_stream: AsyncGenerator[dict, None],
        request: Request,
        last_event_id: Optional[str] = None,
        status_code: int = 200,
    ) -> StreamingResponse:
        """Create a StreamingResponse for SSE with reconnection support.

        Args:
            event_stream: Async generator yielding event dictionaries
            request: FastAPI request object
            last_event_id: Optional ID for resumption (from Last-Event-ID header)
            status_code: HTTP status code

        Returns:
            FastAPI StreamingResponse configured for SSE
        """
        return StreamingResponse(
            self.event_generator(event_stream, request, last_event_id),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # Disable Nginx buffering
            },
            status_code=status_code,
        )


class EventBuilder:
    """Builder for creating SSE events."""

    @staticmethod
    def token(content: str) -> dict:
        """Create a token event."""
        return {"event": "token", "data": {"content": content}}

    @staticmethod
    def tool_call(name: str, args: dict, tool_call_id: str) -> dict:
        """Create a tool call event."""
        return {"event": "tool_call", "data": {"name": name, "args": args, "id": tool_call_id}}

    @staticmethod
    def tool_result(tool_call_id: str, output: str, exit_code: int = 0) -> dict:
        """Create a tool result event."""
        return {
            "event": "tool_result",
            "data": {
                "tool_call_id": tool_call_id,
                "output": output,
                "exit_code": exit_code,
            },
        }

    @staticmethod
    def error(message: str, code: str | None = None) -> dict:
        """Create an error event."""
        data = {"message": message}
        if code:
            data["code"] = code
        return {"event": "error", "data": data}

    @staticmethod
    def done() -> dict:
        """Create a done event."""
        return {"event": "done", "data": {}}

    @staticmethod
    def usage(
        input_tokens: int,
        output_tokens: int,
        estimated_cost: float,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict:
        """Create a usage event."""
        return {
            "event": "usage",
            "data": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost": estimated_cost,
                "provider": provider,
                "model": model,
            },
        }

    @staticmethod
    def planning(todos: list[dict]) -> dict:
        """Create a planning event showing the task breakdown."""
        return {"event": "planning", "data": {"todos": todos}}

    @staticmethod
    def step_complete(step_number: int, total_steps: int, description: str) -> dict:
        """Create a step completion event."""
        return {
            "event": "step_complete",
            "data": {
                "step_number": step_number,
                "total_steps": total_steps,
                "description": description,
            },
        }

    @staticmethod
    def status(status: str) -> dict:
        """Create a status update event."""
        return {"event": "status", "data": {"status": status}}

    @staticmethod
    def reconnected(last_event_id: str) -> dict:
        """Create a reconnection confirmation event."""
        return {
            "event": "reconnected",
            "data": {"last_event_id": last_event_id, "resumed": True},
        }
