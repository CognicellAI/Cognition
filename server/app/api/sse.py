"""Server-Sent Events (SSE) utilities.

Helper functions and classes for streaming SSE events.
"""

from __future__ import annotations

import json
from typing import AsyncGenerator, Any

from fastapi import Request
from fastapi.responses import StreamingResponse


class SSEStream:
    """Helper class for streaming SSE events."""

    @staticmethod
    def format_event(event_type: str, data: dict) -> str:
        """Format an SSE event.

        Args:
            event_type: The event type (e.g., 'token', 'tool_call', 'done')
            data: The event data as a dictionary

        Returns:
            Formatted SSE event string
        """
        return f"event: {event_type}\ndata: {json.dumps(data)}\n\n"

    @staticmethod
    async def event_generator(
        event_stream: AsyncGenerator[dict, None],
        request: Request,
    ) -> AsyncGenerator[str, None]:
        """Generate SSE formatted events from an async generator.

        Args:
            event_stream: Async generator yielding event dictionaries
            request: FastAPI request object for disconnection detection

        Yields:
            Formatted SSE event strings
        """
        try:
            async for event in event_stream:
                # Check if client disconnected
                if await request.is_disconnected():
                    break

                event_type = event.get("event", "message")
                data = event.get("data", {})
                yield SSEStream.format_event(event_type, data)

        except Exception as e:
            # Send error event
            yield SSEStream.format_event("error", {"message": str(e)})

    @staticmethod
    def create_response(
        event_stream: AsyncGenerator[dict, None],
        request: Request,
        status_code: int = 200,
    ) -> StreamingResponse:
        """Create a StreamingResponse for SSE.

        Args:
            event_stream: Async generator yielding event dictionaries
            request: FastAPI request object
            status_code: HTTP status code

        Returns:
            FastAPI StreamingResponse configured for SSE
        """
        return StreamingResponse(
            SSEStream.event_generator(event_stream, request),
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
