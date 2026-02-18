"""Server-Sent Events (SSE) utilities.

Helper functions and classes for streaming SSE events with reconnection support.

Features:
- Event ID generation for resumable streams
- Retry directive for client auto-reconnection
- Keepalive heartbeat events
- Last-Event-ID header support for stream resumption
- Event buffering for replay
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from collections.abc import AsyncGenerator
from dataclasses import dataclass, field
from typing import Any

from fastapi import Request
from fastapi.responses import StreamingResponse

from server.app.settings import Settings, get_settings


@dataclass
class SSEEvent:
    """Represents a single SSE event with all optional fields."""

    event_type: str
    data: dict[str, Any]
    event_id: str | None = None
    retry_ms: int | None = None
    timestamp: float = field(default_factory=time.time)

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


@dataclass
class BufferedEvent:
    """Event stored in buffer for replay on reconnection."""

    event_id: str
    event_type: str
    data: dict[str, Any]
    timestamp: float


class EventBuffer:
    """Thread-safe circular buffer for SSE events.

    Stores recent events for replay when clients reconnect with Last-Event-ID.
    """

    def __init__(self, max_size: int = 100):
        """Initialize event buffer.

        Args:
            max_size: Maximum number of events to buffer
        """
        self._buffer: deque[BufferedEvent] = deque(maxlen=max_size)
        self._lock = asyncio.Lock()
        self._max_size = max_size

    async def add(self, event_id: str, event_type: str, data: dict[str, Any]) -> None:
        """Add an event to the buffer.

        Args:
            event_id: Unique event identifier
            event_type: Type of event
            data: Event data payload
        """
        async with self._lock:
            self._buffer.append(
                BufferedEvent(
                    event_id=event_id,
                    event_type=event_type,
                    data=data,
                    timestamp=time.time(),
                )
            )

    async def get_events_after(self, last_event_id: str) -> list[BufferedEvent]:
        """Get all events after the specified event ID.

        Args:
            last_event_id: Event ID to resume from (exclusive)

        Returns:
            List of events that occurred after last_event_id
        """
        async with self._lock:
            events = list(self._buffer)

        # Find the index of the last_event_id
        try:
            start_idx = next(i for i, e in enumerate(events) if e.event_id == last_event_id)
            # Return events after the found index
            return events[start_idx + 1 :]
        except StopIteration:
            # Event ID not found in buffer, return all events
            return events

    async def clear(self) -> None:
        """Clear all buffered events."""
        async with self._lock:
            self._buffer.clear()


class SSEStream:
    """Helper class for streaming SSE events with reconnection support.

    Features:
    - Unique event ID generation (sequential counter + UUID)
    - Configurable retry directive for client reconnection
    - Periodic keepalive heartbeat comments
    - Event buffering for replay on reconnection
    - Last-Event-ID header support for stream resumption
    """

    def __init__(
        self,
        retry_ms: int = 3000,
        heartbeat_interval: float = 15.0,
        buffer_size: int = 100,
    ):
        """Initialize SSE stream with reconnection settings.

        Args:
            retry_ms: Retry delay in milliseconds for client auto-reconnect
            heartbeat_interval: Seconds between keepalive heartbeats
            buffer_size: Maximum number of events to buffer for replay
        """
        self.retry_ms = retry_ms
        self.heartbeat_interval = heartbeat_interval
        self._event_buffer = EventBuffer(max_size=buffer_size)
        self._event_counter = 0
        self._counter_lock = asyncio.Lock()

    @classmethod
    def from_settings(cls, settings: Settings | None = None) -> SSEStream:
        """Create SSEStream from application settings.

        Args:
            settings: Application settings (loads from get_settings() if None)

        Returns:
            Configured SSEStream instance
        """
        if settings is None:
            settings = get_settings()
        return cls(
            retry_ms=settings.sse_retry_interval_ms,
            heartbeat_interval=settings.sse_heartbeat_interval_seconds,
            buffer_size=settings.sse_buffer_size,
        )

    async def _generate_event_id(self) -> str:
        """Generate a unique sequential event ID.

        Returns:
            Event ID in format "{counter}-{uuid_prefix}"
        """
        async with self._counter_lock:
            self._event_counter += 1
            counter = self._event_counter
        return f"{counter}-{uuid.uuid4().hex[:8]}"

    @staticmethod
    def format_event(
        event_type: str,
        data: dict,
        event_id: str | None = None,
        retry_ms: int | None = None,
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
    def format_keepalive(message: str = "heartbeat") -> str:
        """Format a keepalive comment.

        SSE comments start with ':' and are ignored by the EventSource API,
        but keep the connection alive.

        Args:
            message: Optional message to include in comment

        Returns:
            Keepalive SSE comment string
        """
        return f":{message}\n\n"

    async def _send_buffered_events(self, last_event_id: str) -> AsyncGenerator[str, None]:
        """Send buffered events that occurred after last_event_id.

        Args:
            last_event_id: Event ID to resume from

        Yields:
            Formatted SSE event strings for replay
        """
        events = await self._event_buffer.get_events_after(last_event_id)
        for event in events:
            yield self.format_event(
                event_type=event.event_type,
                data=event.data,
                event_id=event.event_id,
            )

    async def event_generator(
        self,
        event_stream: AsyncGenerator[dict, None],
        request: Request,
        last_event_id: str | None = None,
    ) -> AsyncGenerator[str, None]:
        """Generate SSE formatted events from an async generator.

        Includes:
        - Event ID generation for resumption
        - Retry directive on first event
        - Keepalive heartbeats during idle periods
        - Client disconnection detection
        - Event buffering for replay on reconnection

        Args:
            event_stream: Async generator yielding event dictionaries
            request: FastAPI request object for disconnection detection
            last_event_id: Optional ID to resume from (from Last-Event-ID header)

        Yields:
            Formatted SSE event strings
        """
        # Send retry directive first
        yield f"retry: {self.retry_ms}\n\n"

        # If resuming, send buffered events first
        if last_event_id:
            async for event_str in self._send_buffered_events(last_event_id):
                if await request.is_disconnected():
                    return
                yield event_str

            # Send reconnection confirmation event
            reconnect_event_id = await self._generate_event_id()
            await self._event_buffer.add(
                reconnect_event_id, "reconnected", {"last_event_id": last_event_id}
            )
            yield self.format_event(
                event_type="reconnected",
                data={"last_event_id": last_event_id, "resumed": True},
                event_id=reconnect_event_id,
            )

        try:
            while True:
                # Check for client disconnection
                if await request.is_disconnected():
                    break

                # Wait for next event with timeout for heartbeat
                try:
                    # Use asyncio.wait_for to implement heartbeat
                    event = await asyncio.wait_for(
                        event_stream.__anext__(),
                        timeout=self.heartbeat_interval,
                    )

                    # Process the event
                    event_type = event.get("event", "message")
                    data = event.get("data", {})

                    # Generate unique event ID
                    event_id = await self._generate_event_id()

                    # Buffer the event for potential replay
                    await self._event_buffer.add(event_id, event_type, data)

                    # Yield the formatted event
                    yield self.format_event(event_type, data, event_id)

                except TimeoutError:
                    # Send keepalive heartbeat
                    if not await request.is_disconnected():
                        yield self.format_keepalive()

        except StopAsyncIteration:
            # Event stream exhausted normally
            pass
        except Exception as e:
            # Send error event
            error_event_id = await self._generate_event_id()
            await self._event_buffer.add(
                error_event_id,
                "error",
                {"message": str(e), "code": "STREAM_ERROR"},
            )
            yield self.format_event(
                "error",
                {"message": str(e), "code": "STREAM_ERROR"},
                error_event_id,
            )

    def create_response(
        self,
        event_stream: AsyncGenerator[dict, None],
        request: Request,
        last_event_id: str | None = None,
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
        return {
            "event": "tool_call",
            "data": {"name": name, "args": args, "id": tool_call_id},
        }

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
    def done(assistant_data: dict[str, Any] | None = None) -> dict:
        """Create a done event.

        Args:
            assistant_data: Optional assistant message data for persistence
        """
        data = {}
        if assistant_data:
            data["assistant_data"] = assistant_data
        return {"event": "done", "data": data}

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


def get_last_event_id(request: Request) -> str | None:
    """Extract Last-Event-ID header from request.

    Args:
        request: FastAPI request object

    Returns:
        Last event ID if present, None otherwise
    """
    return request.headers.get("Last-Event-ID")
