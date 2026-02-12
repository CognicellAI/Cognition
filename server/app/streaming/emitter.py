"""Unified event emitter for streaming events to clients."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

from server.app.protocol.events import (
    AssistantMessage,
    DiffApplied,
    Done,
    Error,
    ServerEvent,
    SessionStarted,
    TestsFinished,
    ToolEnd,
    ToolOutput,
    ToolStart,
)

if TYPE_CHECKING:
    from fastapi import WebSocket

logger = structlog.get_logger()


class EventEmitter:
    """Emits events to connected WebSocket clients."""

    def __init__(self, websocket: WebSocket, session_id: str) -> None:
        self.websocket = websocket
        self.session_id = session_id

    async def emit(self, event: ServerEvent) -> None:
        """Emit an event to the client."""
        from server.app.protocol.serializer import serialize_event

        try:
            json_str = serialize_event(event)
            await self.websocket.send_text(json_str)
        except Exception as e:
            logger.error(
                "Failed to emit event",
                session_id=self.session_id,
                event_type=type(event).__name__,
                error=str(e),
            )
            raise

    async def session_started(self, network_mode: str, workspace_path: str) -> None:
        """Emit session started event."""
        await self.emit(
            SessionStarted(
                session_id=self.session_id,
                network_mode=network_mode,  # type: ignore[arg-type]
                workspace_path=workspace_path,
            )
        )

    async def assistant_message(self, content: str) -> None:
        """Emit assistant message event."""
        await self.emit(AssistantMessage(session_id=self.session_id, content=content))

    async def tool_start(self, tool: str, input_data: dict[str, Any]) -> None:
        """Emit tool start event."""
        await self.emit(ToolStart(session_id=self.session_id, tool=tool, input=input_data))

    async def tool_output(self, stream: str, chunk: str) -> None:
        """Emit tool output event."""
        await self.emit(
            ToolOutput(
                session_id=self.session_id,
                stream=stream,  # type: ignore[arg-type]
                chunk=chunk,
            )
        )

    async def tool_end(
        self, tool: str, exit_code: int, artifacts: dict[str, Any] | None = None
    ) -> None:
        """Emit tool end event."""
        await self.emit(
            ToolEnd(
                session_id=self.session_id,
                tool=tool,
                exit_code=exit_code,
                artifacts=artifacts or {},
            )
        )

    async def diff_applied(self, files_changed: list[str], diff_preview: str) -> None:
        """Emit diff applied event."""
        await self.emit(
            DiffApplied(
                session_id=self.session_id,
                files_changed=files_changed,
                diff_preview=diff_preview,
            )
        )

    async def tests_finished(self, exit_code: int, summary: str = "") -> None:
        """Emit tests finished event."""
        await self.emit(
            TestsFinished(
                session_id=self.session_id,
                exit_code=exit_code,
                summary=summary,
            )
        )

    async def error(self, message: str, code: str = "UNKNOWN_ERROR") -> None:
        """Emit error event."""
        await self.emit(Error(session_id=self.session_id, message=message, code=code))

    async def done(self) -> None:
        """Emit done event."""
        await self.emit(Done(session_id=self.session_id))
