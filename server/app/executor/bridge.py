"""Agent bridge - server-side WebSocket client to agent container.

This module provides the bridge between the Cognition server and the
agent runtime running inside a Docker container.
"""

from __future__ import annotations

import asyncio
from typing import Any, AsyncIterator, Callable

import structlog
import websockets
from websockets.client import WebSocketClientProtocol

from shared.protocol.internal import (
    AgentEvent,
    AgentStartMessage,
    CancelMessage,
    ServerMessage,
    ShutdownMessage,
    UserMessage,
    parse_agent_event,
    serialize_message,
)

logger = structlog.get_logger()


class AgentBridge:
    """Bidirectional bridge to an agent running inside a container.

    Connects to the agent's WebSocket server and forwards messages/events.
    """

    def __init__(
        self,
        container_id: str,
        agent_host: str = "localhost",
        agent_port: int = 9000,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        self.container_id = container_id
        self.agent_host = agent_host
        self.agent_port = agent_port
        self.on_event = on_event
        self.websocket: WebSocketClientProtocol | None = None
        self._connected = False
        self._receive_task: asyncio.Task | None = None

    async def connect(self, timeout: float = 30.0) -> None:
        """Connect to the agent container's WebSocket server.

        Args:
            timeout: Connection timeout in seconds

        Raises:
            ConnectionError: If connection fails
        """
        uri = f"ws://{self.agent_host}:{self.agent_port}"
        logger.info(
            "Connecting to agent container",
            container_id=self.container_id,
            uri=uri,
        )

        # Wait for agent to be ready (with exponential backoff)
        start_time = asyncio.get_event_loop().time()
        last_error = None
        max_retries = 15  # Increased from 5
        retry_count = 0

        while retry_count < max_retries:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                raise ConnectionError(
                    f"Timeout connecting to agent container {self.container_id}: {last_error}"
                )

            try:
                self.websocket = await asyncio.wait_for(
                    websockets.connect(uri, ping_interval=20, ping_timeout=10),
                    timeout=min(5.0, timeout - elapsed),  # Use shorter timeout per attempt
                )
                self._connected = True

                # Start receiving events
                self._receive_task = asyncio.create_task(self._receive_loop())

                logger.info(
                    "Connected to agent container",
                    container_id=self.container_id,
                )
                return

            except (
                asyncio.TimeoutError,
                OSError,
                ConnectionRefusedError,
                websockets.exceptions.InvalidMessage,
            ) as e:
                last_error = e
                retry_count += 1
                if retry_count < max_retries:
                    wait_time = min(1.0 * (2**retry_count), 5.0)  # Exponential backoff, max 5s
                    logger.debug(
                        "Agent not ready, retrying",
                        container_id=self.container_id,
                        retry=retry_count,
                        wait_seconds=wait_time,
                        error=str(e),
                    )
                    await asyncio.sleep(wait_time)
                else:
                    logger.error(
                        "Failed to connect to agent container after retries",
                        container_id=self.container_id,
                        retries=retry_count,
                        last_error=str(last_error),
                    )
                    raise ConnectionError(
                        f"Failed to connect to agent container {self.container_id} after {max_retries} attempts: {last_error}"
                    ) from last_error

            except Exception as e:
                logger.error(
                    "Unexpected error connecting to agent",
                    container_id=self.container_id,
                    error=str(e),
                )
                raise ConnectionError(
                    f"Failed to connect to agent container {self.container_id}: {e}"
                ) from e

    async def disconnect(self) -> None:
        """Disconnect from the agent container."""
        if not self._connected:
            return

        logger.info(
            "Disconnecting from agent container",
            container_id=self.container_id,
        )

        # Cancel receive task
        if self._receive_task and not self._receive_task.done():
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass

        # Close websocket
        if self.websocket:
            await self.websocket.close()
            self.websocket = None

        self._connected = False
        logger.info(
            "Disconnected from agent container",
            container_id=self.container_id,
        )

    async def initialize_agent(self, config: AgentStartMessage) -> None:
        """Send agent initialization message.

        Args:
            config: Agent configuration including session_id, LLM settings, etc.
        """
        await self._send_message(config)
        logger.debug(
            "Sent agent_start message",
            container_id=self.container_id,
            session_id=config.session_id,
        )

    async def send_user_message(self, session_id: str, content: str, turn_number: int) -> None:
        """Send a user message to the agent.

        Args:
            session_id: Session identifier
            content: User message content
            turn_number: Turn number for tracking
        """
        msg = UserMessage(
            session_id=session_id,
            content=content,
            turn_number=turn_number,
        )
        await self._send_message(msg)
        logger.debug(
            "Sent user_message",
            container_id=self.container_id,
            session_id=session_id,
            turn_number=turn_number,
        )

    async def cancel(self, session_id: str) -> None:
        """Cancel the current agent turn.

        Args:
            session_id: Session identifier
        """
        msg = CancelMessage(session_id=session_id)
        await self._send_message(msg)
        logger.debug(
            "Sent cancel message",
            container_id=self.container_id,
            session_id=session_id,
        )

    async def shutdown(self, session_id: str) -> None:
        """Request graceful shutdown of the agent.

        Args:
            session_id: Session identifier
        """
        msg = ShutdownMessage(session_id=session_id)
        await self._send_message(msg)
        logger.debug(
            "Sent shutdown message",
            container_id=self.container_id,
            session_id=session_id,
        )

    async def _send_message(self, msg: ServerMessage) -> None:
        """Send a message to the agent."""
        if not self.websocket or not self._connected:
            raise ConnectionError("Not connected to agent container")

        message = serialize_message(msg)
        await self.websocket.send(message)

    async def _receive_loop(self) -> None:
        """Background task to receive events from the agent."""
        if not self.websocket:
            return

        try:
            async for message in self.websocket:
                try:
                    event = parse_agent_event(message)
                    await self._handle_event(event)
                except Exception as e:
                    logger.error(
                        "Failed to parse agent event",
                        container_id=self.container_id,
                        error=str(e),
                        message_preview=message[:200],
                    )

        except websockets.exceptions.ConnectionClosed:
            logger.info(
                "Agent container connection closed",
                container_id=self.container_id,
            )
            self._connected = False

        except asyncio.CancelledError:
            logger.debug(
                "Receive loop cancelled",
                container_id=self.container_id,
            )
            raise

        except Exception as e:
            logger.error(
                "Receive loop error",
                container_id=self.container_id,
                error=str(e),
            )
            self._connected = False

    async def _handle_event(self, event: AgentEvent) -> None:
        """Handle an event from the agent."""
        # Convert dataclass to dict for the callback
        event_dict = event.__dict__

        # Call the event handler if provided
        if self.on_event:
            try:
                if asyncio.iscoroutinefunction(self.on_event):
                    await self.on_event(event_dict)
                else:
                    # Run sync callback in thread
                    await asyncio.to_thread(self.on_event, event_dict)
            except Exception as e:
                logger.error(
                    "Event handler failed",
                    container_id=self.container_id,
                    error=str(e),
                )

    @property
    def is_connected(self) -> bool:
        """Check if connected to the agent container."""
        return self._connected and self.websocket is not None


class AgentBridgePool:
    """Pool of agent bridges for managing multiple sessions."""

    def __init__(self) -> None:
        self._bridges: dict[str, AgentBridge] = {}
        self._lock = asyncio.Lock()

    async def create_bridge(
        self,
        session_id: str,
        container_id: str,
        agent_host: str = "localhost",
        agent_port: int = 9000,
        on_event: Callable[[dict[str, Any]], Any] | None = None,
    ) -> AgentBridge:
        """Create and connect a new bridge.

        Args:
            session_id: Session identifier (key for the pool)
            container_id: Docker container ID
            agent_host: Agent container host
            agent_port: Agent container port
            on_event: Event callback

        Returns:
            Connected AgentBridge
        """
        async with self._lock:
            # Disconnect existing bridge if any
            if session_id in self._bridges:
                old_bridge = self._bridges[session_id]
                await old_bridge.disconnect()

            # Create and connect new bridge
            bridge = AgentBridge(
                container_id=container_id,
                agent_host=agent_host,
                agent_port=agent_port,
                on_event=on_event,
            )
            await bridge.connect()
            self._bridges[session_id] = bridge

            return bridge

    async def get_bridge(self, session_id: str) -> AgentBridge | None:
        """Get an existing bridge by session ID."""
        async with self._lock:
            return self._bridges.get(session_id)

    async def remove_bridge(self, session_id: str) -> None:
        """Remove and disconnect a bridge."""
        async with self._lock:
            if session_id in self._bridges:
                bridge = self._bridges.pop(session_id)
                await bridge.disconnect()

    async def close_all(self) -> None:
        """Close all bridges."""
        async with self._lock:
            for bridge in self._bridges.values():
                await bridge.disconnect()
            self._bridges.clear()
