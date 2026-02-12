"""Agent runtime server - WebSocket server inside the agent container.

This module provides the WebSocket server that runs inside the agent container.
It receives messages from the Cognition server, runs the LangGraph agent,
and streams events back.
"""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
import websockets
from websockets.server import WebSocketServerProtocol

from shared.protocol.internal import (
    AgentEvent,
    AgentReadyEvent,
    CancelMessage,
    ServerMessage,
    ShutdownMessage,
    UserMessage,
    parse_server_message,
    serialize_message,
)

logger = structlog.get_logger()


class AgentRuntimeServer:
    """WebSocket server for the agent runtime inside the container."""

    def __init__(self, host: str = "0.0.0.0", port: int = 9000) -> None:
        self.host = host
        self.port = port
        self.server: websockets.WebSocketServer | None = None
        self.agent: AgentRunner | None = None
        self._shutdown_event = asyncio.Event()
        self._current_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start the WebSocket server."""

        # For websockets 16.0+, use process_request to bypass origin checks
        async def process_request(connection, request):
            # Accept all connections (agent is internal)
            return None

        self.server = await websockets.serve(
            self._handle_connection,
            self.host,
            self.port,
            ping_interval=20,
            ping_timeout=10,
            process_request=process_request,  # Custom request handler
        )
        logger.info("Agent WebSocket server started", host=self.host, port=self.port)

    async def stop(self) -> None:
        """Stop the WebSocket server."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
            logger.info("Agent WebSocket server stopped")

        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass

    async def wait_for_shutdown(self) -> None:
        """Wait for shutdown signal."""
        await self._shutdown_event.wait()

    async def _handle_connection(self, websocket: WebSocketServerProtocol) -> None:
        """Handle a single WebSocket connection from the server."""
        logger.info("Server connected", remote=websocket.remote_address)

        try:
            async for message in websocket:
                try:
                    server_msg = parse_server_message(message)
                    await self._process_message(websocket, server_msg)
                except Exception as e:
                    logger.error("Failed to process message", error=str(e))
                    await self._send_event(
                        websocket,
                        {"event": "error", "message": f"Invalid message: {e}"},
                    )
        except websockets.exceptions.ConnectionClosed:
            logger.info("Server disconnected")
        finally:
            # Cancel any running agent task
            if self._current_task and not self._current_task.done():
                self._current_task.cancel()

    async def _process_message(
        self, websocket: WebSocketServerProtocol, msg: ServerMessage
    ) -> None:
        """Process a message from the server."""
        msg_type = msg.type

        if msg_type == "agent_start":
            await self._handle_agent_start(websocket, msg)
        elif msg_type == "user_message":
            await self._handle_user_message(websocket, msg)
        elif msg_type == "cancel":
            await self._handle_cancel(websocket, msg)
        elif msg_type == "shutdown":
            await self._handle_shutdown(websocket, msg)
        else:
            logger.warning("Unknown message type", type=msg_type)

    async def _handle_agent_start(self, websocket: WebSocketServerProtocol, msg: Any) -> None:
        """Initialize the agent with the provided configuration."""
        logger.info(
            "Initializing agent",
            session_id=msg.session_id,
            project_id=msg.project_id,
            llm_provider=msg.llm_provider,
            llm_model=msg.llm_model,
        )

        # Import here to avoid circular dependencies
        from agent.sandbox import LocalSandboxBackend

        # Create the sandbox backend (local filesystem + subprocess execution)
        backend = LocalSandboxBackend(workspace_path=msg.workspace_path)

        # Create the agent runner
        self.agent = AgentRunner(
            session_id=msg.session_id,
            backend=backend,
            llm_provider=msg.llm_provider,
            llm_model=msg.llm_model,
            llm_temperature=msg.llm_temperature,
            system_prompt=msg.system_prompt,
            max_iterations=msg.max_iterations,
            history=msg.history,
        )

        # Signal ready
        await self._send_event(websocket, AgentReadyEvent(session_id=msg.session_id))
        logger.info("Agent initialized and ready", session_id=msg.session_id)

    async def _handle_user_message(
        self, websocket: WebSocketServerProtocol, msg: UserMessage
    ) -> None:
        """Process a user message and stream events back."""
        if not self.agent:
            await self._send_event(
                websocket,
                {
                    "event": "error",
                    "message": "Agent not initialized. Send agent_start first.",
                },
            )
            return

        logger.info(
            "Processing user message",
            session_id=msg.session_id,
            turn_number=msg.turn_number,
            content_length=len(msg.content),
        )

        # Create an event emitter that forwards to the WebSocket
        async def emit_event(event: dict[str, Any]) -> None:
            await self._send_event(websocket, event)

        # Run the agent in a background task so we can cancel it
        self._current_task = asyncio.create_task(self.agent.run_turn(msg.content, emit_event))

        try:
            await self._current_task
        except asyncio.CancelledError:
            logger.info("Agent turn cancelled", session_id=msg.session_id)
            await emit_event({"event": "done", "session_id": msg.session_id, "cancelled": True})
        finally:
            self._current_task = None

    async def _handle_cancel(self, websocket: WebSocketServerProtocol, msg: CancelMessage) -> None:
        """Cancel the current agent turn."""
        if self._current_task and not self._current_task.done():
            logger.info("Cancelling agent turn", session_id=msg.session_id)
            self._current_task.cancel()
        else:
            logger.debug("No active agent turn to cancel", session_id=msg.session_id)

    async def _handle_shutdown(
        self, websocket: WebSocketServerProtocol, msg: ShutdownMessage
    ) -> None:
        """Gracefully shut down the agent."""
        logger.info("Shutdown requested", session_id=msg.session_id)

        # Cancel any running task
        if self._current_task and not self._current_task.done():
            self._current_task.cancel()
            try:
                await self._current_task
            except asyncio.CancelledError:
                pass

        # Signal shutdown
        self._shutdown_event.set()

    async def _send_event(
        self, websocket: WebSocketServerProtocol, event: AgentEvent | dict[str, Any]
    ) -> None:
        """Send an event to the server."""
        if isinstance(event, dict):
            message = serialize_message(event)
        else:
            message = serialize_message(event)
        await websocket.send(message)


class AgentRunner:
    """Runs the LangGraph agent and emits events."""

    def __init__(
        self,
        session_id: str,
        backend: Any,
        llm_provider: str,
        llm_model: str,
        llm_temperature: float = 0.7,
        system_prompt: str | None = None,
        max_iterations: int = 50,
        history: list[dict[str, Any]] | None = None,
    ) -> None:
        self.session_id = session_id
        self.backend = backend
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.llm_temperature = llm_temperature
        self.system_prompt = system_prompt
        self.max_iterations = max_iterations
        self.history = history or []
        self._cancelled = False

    async def run_turn(self, content: str, emit_event: Any) -> None:
        """Run a single agent turn with real-time streaming.

        Emits events as they happen:
        - assistant_message: Streaming tokens from the LLM (if supported)
        - tool_start: When a tool execution begins
        - tool_output: Real-time stdout/stderr from tools
        - tool_end: When a tool execution completes
        - done: When the turn is complete
        """
        try:
            from deepagents import create_deep_agent

            # Build LLM model with streaming callbacks
            model = self._create_llm()

            # Create tools with streaming wrappers
            tools = self._create_streaming_tools(emit_event)

            # Create the agent
            agent = create_deep_agent(
                model=model,
                tools=tools,
                system_prompt=self.system_prompt or self._get_default_prompt(),
                backend=self.backend,
                max_iterations=self.max_iterations,
            )

            # Use streaming if available, fallback to invoke
            try:
                # Try to use astream for token-by-token streaming
                async for chunk in agent.astream(
                    {"messages": [{"role": "user", "content": content}]}
                ):
                    if self._cancelled:
                        break

                    # Extract and emit assistant content chunks
                    if "messages" in chunk:
                        for msg in chunk["messages"]:
                            if hasattr(msg, "content") and msg.content:
                                await emit_event(
                                    {
                                        "event": "assistant_message",
                                        "session_id": self.session_id,
                                        "content": msg.content,
                                        "is_complete": False,
                                    }
                                )

            except AttributeError:
                # Fallback: use invoke (no token streaming, but tools still stream)
                logger.debug("Streaming not available, using invoke")
                result = await asyncio.to_thread(
                    agent.invoke, {"messages": [{"role": "user", "content": content}]}
                )

                # Emit the complete assistant response
                if "messages" in result and result["messages"]:
                    last_message = result["messages"][-1]
                    if hasattr(last_message, "content"):
                        await emit_event(
                            {
                                "event": "assistant_message",
                                "session_id": self.session_id,
                                "content": last_message.content,
                                "is_complete": True,
                            }
                        )

            # Signal completion
            await emit_event({"event": "done", "session_id": self.session_id})

        except asyncio.CancelledError:
            logger.info("Agent turn cancelled", session_id=self.session_id)
            await emit_event({"event": "done", "session_id": self.session_id, "cancelled": True})
            raise

        except Exception as e:
            logger.error("Agent turn failed", error=str(e))
            await emit_event(
                {
                    "event": "error",
                    "session_id": self.session_id,
                    "message": str(e),
                    "error_type": type(e).__name__,
                }
            )

    def _create_llm(self) -> Any:
        """Create the LLM model based on configuration."""
        import os

        if self.llm_provider == "openai":
            from langchain_openai import ChatOpenAI

            return ChatOpenAI(
                model=self.llm_model or "gpt-4-turbo-preview",
                temperature=self.llm_temperature,
                api_key=os.environ.get("OPENAI_API_KEY"),
            )
        elif self.llm_provider == "anthropic":
            from langchain_anthropic import ChatAnthropic

            return ChatAnthropic(
                model=self.llm_model or "claude-3-sonnet-20240229",
                temperature=self.llm_temperature,
                api_key=os.environ.get("ANTHROPIC_API_KEY"),
            )
        else:
            raise ValueError(f"Unsupported LLM provider: {self.llm_provider}")

    def _create_tools(self) -> list[Any]:
        """Create the tools available to the agent."""
        from agent.local_tools import GitTools, TestTools

        tools = []

        # Add custom tools
        git_tools = GitTools()
        test_tools = TestTools()

        tools.extend(
            [
                git_tools.git_status,
                git_tools.git_diff,
                test_tools.run_tests,
            ]
        )

        return tools

    def _create_streaming_tools(self, emit_event: Any) -> list[Any]:
        """Create tools that emit events during execution for real-time streaming."""
        from langchain.tools import Tool

        original_tools = self._create_tools()
        streaming_tools = []

        for tool in original_tools:
            # Wrap the tool to emit events
            wrapped_tool = self._wrap_tool_for_streaming(tool, emit_event)
            streaming_tools.append(wrapped_tool)

        return streaming_tools

    def _wrap_tool_for_streaming(self, tool: Any, emit_event: Any) -> Any:
        """Wrap a tool to emit tool_start/tool_end events."""
        import asyncio
        from typing import Any

        original_func = tool.func
        tool_name = tool.name

        async def streaming_wrapper(*args: Any, **kwargs: Any) -> Any:
            # Emit tool_start
            await emit_event(
                {
                    "event": "tool_start",
                    "session_id": self.session_id,
                    "tool_name": tool_name,
                    "tool_args": {"args": args, "kwargs": kwargs} if args or kwargs else {},
                }
            )

            try:
                # Run the original function
                if asyncio.iscoroutinefunction(original_func):
                    result = await original_func(*args, **kwargs)
                else:
                    result = await asyncio.to_thread(original_func, *args, **kwargs)

                # Emit tool_end
                await emit_event(
                    {
                        "event": "tool_end",
                        "session_id": self.session_id,
                        "tool_name": tool_name,
                        "exit_code": 0,
                        "result_summary": str(result)[:200] if result else "",
                    }
                )

                return result

            except Exception as e:
                # Emit tool_end with error
                await emit_event(
                    {
                        "event": "tool_end",
                        "session_id": self.session_id,
                        "tool_name": tool_name,
                        "exit_code": -1,
                        "result_summary": f"Error: {e}",
                    }
                )
                raise

        # Create new tool with wrapped function
        from langchain.tools import Tool

        return Tool(
            name=tool.name,
            func=streaming_wrapper,
            description=tool.description,
            args_schema=tool.args_schema if hasattr(tool, "args_schema") else None,
        )

    def _get_default_prompt(self) -> str:
        """Get the default system prompt."""
        return """You are a helpful coding assistant. You have access to the filesystem,
tools for running tests and git operations, and can help with code changes.

Always explain what you're doing before making changes. Use the task tool for
complex multi-step operations that can be parallelized.
"""
