"""WebSocket client with reconnection and typed event handling."""

import asyncio
import json
from dataclasses import dataclass
from enum import Enum, auto
from typing import Any

import websockets
from textual.app import App
from textual.message import Message
from websockets.exceptions import ConnectionClosed, ConnectionClosedOK

from client.tui.config import settings


class ConnectionState(Enum):
    """WebSocket connection state."""

    DISCONNECTED = auto()
    CONNECTING = auto()
    CONNECTED = auto()
    RECONNECTING = auto()


@dataclass
class ServerEvent:
    """Base class for server events."""

    event_type: str
    session_id: str | None
    data: dict[str, Any]


@dataclass
class SessionStartedEvent(ServerEvent):
    """Session started successfully."""

    network_mode: str
    workspace_path: str


@dataclass
class AssistantMessageEvent(ServerEvent):
    """Assistant text message (streaming)."""

    content: str


@dataclass
class ToolStartEvent(ServerEvent):
    """Tool execution started."""

    tool: str
    tool_input: dict[str, Any]


@dataclass
class ToolOutputEvent(ServerEvent):
    """Tool output chunk (streaming)."""

    stream: str  # "stdout" or "stderr"
    chunk: str


@dataclass
class ToolEndEvent(ServerEvent):
    """Tool execution completed."""

    tool: str
    exit_code: int


@dataclass
class DiffAppliedEvent(ServerEvent):
    """Code diff applied."""

    files_changed: list[str]
    diff_preview: str


@dataclass
class TestsFinishedEvent(ServerEvent):
    """Test execution completed."""

    exit_code: int
    summary: str


@dataclass
class ErrorEvent(ServerEvent):
    """Error occurred."""

    message: str
    code: str | None


@dataclass
class DoneEvent(ServerEvent):
    """Agent turn complete."""

    pass


class WebSocketMessages:
    """Textual messages for WebSocket state changes."""

    class Connected(Message):
        """WebSocket connected."""

        pass

    class Disconnected(Message):
        """WebSocket disconnected."""

        permanent: bool

        def __init__(self, permanent: bool = False) -> None:
            self.permanent = permanent
            super().__init__()

    class Reconnecting(Message):
        """WebSocket reconnecting."""

        attempt: int
        max_attempts: int
        delay: float

        def __init__(self, attempt: int, max_attempts: int, delay: float) -> None:
            self.attempt = attempt
            self.max_attempts = max_attempts
            self.delay = delay
            super().__init__()

    class EventReceived(Message):
        """Server event received."""

        event: ServerEvent

        def __init__(self, event: ServerEvent) -> None:
            self.event = event
            super().__init__()


class WebSocketClient:
    """WebSocket client with auto-reconnection and typed events.

    Handles connection lifecycle, reconnection with backoff,
    and parses incoming server events into typed objects.
    """

    def __init__(self, app: App[Any]) -> None:
        """Initialize WebSocket client.

        Args:
            app: Textual App instance for posting messages.
        """
        self.app = app
        self.uri = settings.ws_url
        self.state = ConnectionState.DISCONNECTED
        self.ws: websockets.WebSocketClientProtocol | None = None
        self._receive_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._reconnect_attempt = 0
        self._stop_requested = False

    @property
    def is_connected(self) -> bool:
        """Check if currently connected."""
        return self.state == ConnectionState.CONNECTED and self.ws is not None

    async def connect(self) -> None:
        """Connect to WebSocket server.

        Initiates connection and starts receive/heartbeat loops.
        Posts Connected message on success.
        """
        if self.state in (ConnectionState.CONNECTED, ConnectionState.CONNECTING):
            return

        self.state = ConnectionState.CONNECTING
        self._stop_requested = False

        try:
            self.ws = await websockets.connect(
                self.uri,
                ping_interval=None,  # We'll handle heartbeats manually
            )
            self.state = ConnectionState.CONNECTED
            self._reconnect_attempt = 0
            self.app.post_message(WebSocketMessages.Connected())

            # Start background tasks
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        except Exception as e:
            self.state = ConnectionState.DISCONNECTED
            raise ConnectionError(f"Failed to connect to {self.uri}: {e}")

    async def disconnect(self, permanent: bool = False) -> None:
        """Disconnect from WebSocket server.

        Args:
            permanent: If True, prevents auto-reconnection.
        """
        self._stop_requested = permanent

        # Cancel background tasks
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                pass
            self._heartbeat_task = None

        # Close WebSocket
        if self.ws:
            try:
                await self.ws.close()
            except Exception:
                pass
            self.ws = None

        self.state = ConnectionState.DISCONNECTED
        self.app.post_message(WebSocketMessages.Disconnected(permanent=permanent))

    async def send(self, message: dict[str, Any]) -> None:
        """Send a JSON message to the server.

        Args:
            message: Dictionary to serialize and send.

        Raises:
            ConnectionError: If not connected.
        """
        if not self.is_connected or not self.ws:
            raise ConnectionError("WebSocket not connected")

        try:
            await self.ws.send(json.dumps(message))
        except ConnectionClosed:
            self.state = ConnectionState.DISCONNECTED
            raise ConnectionError("WebSocket closed while sending")

    async def _receive_loop(self) -> None:
        """Background task: receive messages and parse events."""
        try:
            while self.is_connected and self.ws:
                try:
                    message = await self.ws.recv()
                    if isinstance(message, str):
                        data = json.loads(message)
                        event = self._parse_event(data)
                        if event:
                            self.app.post_message(WebSocketMessages.EventReceived(event))

                except ConnectionClosedOK:
                    break
                except ConnectionClosed:
                    break
                except json.JSONDecodeError as e:
                    # Log invalid JSON but continue
                    pass

        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            # Connection lost, trigger reconnection if not stopped
            if not self._stop_requested:
                asyncio.create_task(self._reconnect())

    async def _heartbeat_loop(self) -> None:
        """Background task: send periodic pings."""
        try:
            while self.is_connected and self.ws:
                await asyncio.sleep(settings.ws_heartbeat_interval)
                if self.is_connected and self.ws:
                    try:
                        # Send ping frame
                        pong_waiter = await self.ws.ping()
                        await asyncio.wait_for(pong_waiter, timeout=10.0)
                    except Exception:
                        # Ping failed, connection may be dead
                        break
        except asyncio.CancelledError:
            raise

    async def _reconnect(self) -> None:
        """Attempt to reconnect with exponential backoff."""
        if self._stop_requested or self.state == ConnectionState.RECONNECTING:
            return

        self.state = ConnectionState.RECONNECTING
        self.ws = None

        while self._reconnect_attempt < settings.ws_reconnect_attempts:
            self._reconnect_attempt += 1
            delay = min(
                settings.ws_reconnect_delay * (2 ** (self._reconnect_attempt - 1)),
                30.0,  # Max 30 second delay
            )

            self.app.post_message(
                WebSocketMessages.Reconnecting(
                    attempt=self._reconnect_attempt,
                    max_attempts=settings.ws_reconnect_attempts,
                    delay=delay,
                )
            )

            await asyncio.sleep(delay)

            try:
                await self.connect()
                return
            except ConnectionError:
                continue

        # Reconnection failed
        self.state = ConnectionState.DISCONNECTED
        self.app.post_message(WebSocketMessages.Disconnected(permanent=True))

    def _parse_event(self, data: dict[str, Any]) -> ServerEvent | None:
        """Parse server event JSON into typed event object."""
        event_type = data.get("event")
        session_id = data.get("session_id")

        if event_type == "session_started":
            return SessionStartedEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
                network_mode=data.get("network_mode", "OFF"),
                workspace_path=data.get("workspace_path", ""),
            )
        elif event_type == "assistant_message":
            return AssistantMessageEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
                content=data.get("content", ""),
            )
        elif event_type == "tool_start":
            return ToolStartEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
                tool=data.get("tool", ""),
                tool_input=data.get("input", {}),
            )
        elif event_type == "tool_output":
            return ToolOutputEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
                stream=data.get("stream", "stdout"),
                chunk=data.get("chunk", ""),
            )
        elif event_type == "tool_end":
            return ToolEndEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
                tool=data.get("tool", ""),
                exit_code=data.get("exit_code", 0),
            )
        elif event_type == "diff_applied":
            return DiffAppliedEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
                files_changed=data.get("files_changed", []),
                diff_preview=data.get("diff_preview", ""),
            )
        elif event_type == "tests_finished":
            return TestsFinishedEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
                exit_code=data.get("exit_code", 0),
                summary=data.get("summary", ""),
            )
        elif event_type == "error":
            return ErrorEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
                message=data.get("message", "Unknown error"),
                code=data.get("code"),
            )
        elif event_type == "done":
            return DoneEvent(
                event_type=event_type,
                session_id=session_id,
                data=data,
            )

        # Unknown event type - return generic
        return ServerEvent(
            event_type=event_type or "unknown",
            session_id=session_id,
            data=data,
        )
