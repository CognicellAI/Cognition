"""AgentRuntime protocol and DeepAgentRuntime implementation.

This module defines the AgentRuntime protocol (P1-6 roadmap item) and provides
a DeepAgentRuntime wrapper that implements the protocol using the existing
Deep Agents implementation from cognition_agent.py.

Layer: 4 (Agent Runtime)

Architecture:
- AgentRuntime: Protocol defining the runtime interface
- DeepAgentRuntime: Concrete implementation wrapping Deep Agents
- create_agent_runtime(): Factory function for creating runtimes
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from langgraph.checkpoint.base import BaseCheckpointSaver

from server.app.agent.cognition_agent import create_cognition_agent
from server.app.agent.definition import AgentDefinition
from server.app.storage.factory import create_storage_backend
from server.app.settings import Settings, get_settings


# Event types that can be streamed from the runtime
class AgentEvent:
    """Base class for agent runtime events."""

    pass


class TokenEvent(AgentEvent):
    """Streaming token from the LLM."""

    def __init__(self, content: str) -> None:
        self.content = content


class ToolCallEvent(AgentEvent):
    """Tool call requested by the agent."""

    def __init__(self, name: str, args: dict[str, Any], tool_call_id: str) -> None:
        self.name = name
        self.args = args
        self.tool_call_id = tool_call_id


class ToolResultEvent(AgentEvent):
    """Result of tool execution."""

    def __init__(self, tool_call_id: str, output: str, exit_code: int = 0) -> None:
        self.tool_call_id = tool_call_id
        self.output = output
        self.exit_code = exit_code


class StatusEvent(AgentEvent):
    """Agent status update."""

    def __init__(self, status: str) -> None:
        self.status = status


class DoneEvent(AgentEvent):
    """Stream completion signal."""

    pass


class ErrorEvent(AgentEvent):
    """Error during execution."""

    def __init__(self, message: str, code: str = "ERROR") -> None:
        self.message = message
        self.code = code


@runtime_checkable
class AgentRuntime(Protocol):
    """Protocol defining the agent runtime interface.

    This protocol abstracts the agent execution layer, allowing different
    runtime implementations (Deep Agents, LangGraph, etc.) to be used
    interchangeably.

    Layer: 4 (Agent Runtime)

    Methods:
        astream_events: Stream agent execution events
        ainvoke: Execute agent and return final result
        get_state: Get current agent state
        abort: Cancel current execution
        get_checkpointer: Get checkpoint saver for persistence
    """

    async def astream_events(
        self,
        input_data: str | dict[str, Any],
        thread_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream agent execution events.

        Args:
            input_data: User input (string or structured dict)
            thread_id: Optional thread ID for state persistence

        Yields:
            AgentEvent subclasses representing execution progress
        """
        ...

    async def ainvoke(
        self,
        input_data: str | dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute agent and return final result.

        Args:
            input_data: User input (string or structured dict)
            thread_id: Optional thread ID for state persistence

        Returns:
            Final agent state/output
        """
        ...

    async def get_state(self, thread_id: str | None = None) -> dict[str, Any] | None:
        """Get current agent state.

        Args:
            thread_id: Optional thread ID to get state for

        Returns:
            Current state dict or None if not available
        """
        ...

    async def abort(self, thread_id: str | None = None) -> bool:
        """Cancel current execution.

        Args:
            thread_id: Optional thread ID to abort

        Returns:
            True if abort was successful, False otherwise
        """
        ...

    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get checkpoint saver for state persistence.

        Returns:
            Configured checkpoint saver instance
        """
        ...


class DeepAgentRuntime:
    """Deep Agents implementation of the AgentRuntime protocol.

    This class wraps the existing Deep Agent from cognition_agent.py
    and provides a clean async interface conforming to the AgentRuntime
    protocol.

    Attributes:
        _agent: The underlying Deep Agent instance
        _checkpointer: Checkpoint saver for state persistence
        _thread_id: Default thread ID for this runtime instance
        _aborted: Set of thread IDs that have been aborted
    """

    def __init__(
        self,
        agent: Any,
        checkpointer: BaseCheckpointSaver,
        thread_id: str | None = None,
    ):
        """Initialize the DeepAgentRuntime.

        Args:
            agent: The Deep Agent instance from create_cognition_agent
            checkpointer: Checkpoint saver for state persistence
            thread_id: Optional default thread ID
        """
        self._agent = agent
        self._checkpointer = checkpointer
        self._thread_id = thread_id
        self._aborted: set[str] = set()

    async def astream_events(
        self,
        input_data: str | dict[str, Any],
        thread_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream agent execution events.

        Transforms Deep Agents events into standardized AgentEvent types.

        Args:
            input_data: User input (string or structured dict)
            thread_id: Optional thread ID for state persistence

        Yields:
            AgentEvent: TokenEvent, ToolCallEvent, ToolResultEvent,
                       StatusEvent, DoneEvent, or ErrorEvent
        """
        tid = thread_id or self._thread_id or "default"

        if tid in self._aborted:
            self._aborted.discard(tid)
            yield ErrorEvent(message="Execution was aborted", code="ABORTED")
            return

        try:
            # Prepare input
            if isinstance(input_data, str):
                from langchain_core.messages import HumanMessage

                messages = [HumanMessage(content=input_data)]
                agent_input = {"messages": messages}
            else:
                agent_input = input_data

            config = {"configurable": {"thread_id": tid}}

            # Stream events from Deep Agents
            async for event in self._agent.astream_events(
                agent_input,
                config=config,
                version="v2",
            ):
                # Check if aborted
                if tid in self._aborted:
                    self._aborted.discard(tid)
                    yield ErrorEvent(message="Execution was aborted", code="ABORTED")
                    return

                event_type = event.get("event")
                data = event.get("data", {})
                name = event.get("name", "")

                # Transform events to standardized format
                if event_type == "on_chat_model_stream":
                    chunk = data.get("chunk", {})
                    if hasattr(chunk, "content") and chunk.content:
                        yield TokenEvent(content=chunk.content)

                elif event_type == "on_chain_stream" and name == "model":
                    chunk = data.get("chunk", {})
                    chunks = chunk if isinstance(chunk, list) else [chunk]
                    for c in chunks:
                        content = None
                        if hasattr(c, "update") and isinstance(c.update, dict):
                            messages = c.update.get("messages", [])
                            if messages and hasattr(messages[-1], "content"):
                                content = messages[-1].content
                        elif hasattr(c, "content") and c.content:
                            content = c.content

                        if content:
                            yield TokenEvent(content=content)

                elif event_type == "on_tool_start":
                    tool_name = name
                    tool_args = data.get("input", {})
                    tool_call_id = f"{tool_name}_{id(data)}"

                    yield ToolCallEvent(
                        name=tool_name,
                        args=tool_args,
                        tool_call_id=tool_call_id,
                    )

                elif event_type == "on_tool_end":
                    output = data.get("output", "")
                    tool_call_id = f"{name}_{id(data)}"

                    yield ToolResultEvent(
                        tool_call_id=tool_call_id,
                        output=str(output),
                        exit_code=0,
                    )

                elif event_type == "on_custom_event":
                    event_name = event.get("name", "")
                    if event_name == "status" and isinstance(data, dict) and "status" in data:
                        yield StatusEvent(status=data["status"])

            yield DoneEvent()

        except Exception as e:
            yield ErrorEvent(message=str(e), code="RUNTIME_ERROR")

    async def ainvoke(
        self,
        input_data: str | dict[str, Any],
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute agent and return final result.

        Args:
            input_data: User input (string or structured dict)
            thread_id: Optional thread ID for state persistence

        Returns:
            Final agent state with messages and output
        """
        tid = thread_id or self._thread_id or "default"

        if tid in self._aborted:
            self._aborted.discard(tid)
            return {
                "error": "Execution was aborted",
                "code": "ABORTED",
                "messages": [],
            }

        try:
            # Prepare input
            if isinstance(input_data, str):
                from langchain_core.messages import HumanMessage

                messages = [HumanMessage(content=input_data)]
                agent_input = {"messages": messages}
            else:
                agent_input = input_data

            config = {"configurable": {"thread_id": tid}}

            # Invoke the agent
            result = await self._agent.ainvoke(agent_input, config=config)

            return {
                "output": result,
                "messages": result.get("messages", []),
                "thread_id": tid,
            }

        except Exception as e:
            return {
                "error": str(e),
                "code": "INVOKE_ERROR",
                "messages": [],
            }

    async def get_state(self, thread_id: str | None = None) -> dict[str, Any] | None:
        """Get current agent state.

        Args:
            thread_id: Optional thread ID to get state for

        Returns:
            Current state dict or None
        """
        tid = thread_id or self._thread_id or "default"

        try:
            config = {"configurable": {"thread_id": tid}}
            state = await self._agent.aget_state(config)

            if state:
                return {
                    "values": state.values if hasattr(state, "values") else {},
                    "next": state.next if hasattr(state, "next") else [],
                    "thread_id": tid,
                }
            return None

        except Exception:
            return None

    async def abort(self, thread_id: str | None = None) -> bool:
        """Cancel current execution.

        Args:
            thread_id: Optional thread ID to abort

        Returns:
            True if abort was signaled
        """
        tid = thread_id or self._thread_id or "default"
        self._aborted.add(tid)
        return True

    async def get_checkpointer(self) -> BaseCheckpointSaver:
        """Get checkpoint saver for state persistence.

        Returns:
            Configured checkpoint saver instance
        """
        return self._checkpointer


async def create_agent_runtime(
    definition: AgentDefinition,
    workspace_path: str | Path,
    thread_id: str | None = None,
    settings: Settings | None = None,
    checkpointer: BaseCheckpointSaver | None = None,
) -> DeepAgentRuntime:
    """Create an AgentRuntime from an AgentDefinition.

    Factory function that creates a DeepAgentRuntime instance from a
    declarative agent definition.

    Args:
        definition: AgentDefinition with tools, skills, config, etc.
        workspace_path: Path to the project workspace
        thread_id: Optional thread ID for state persistence
        settings: Optional settings override
        checkpointer: Optional pre-configured checkpointer

    Returns:
        Configured DeepAgentRuntime instance

    Example:
        >>> definition = load_agent_definition(".cognition/agent.yaml")
        >>> runtime = create_agent_runtime(
        ...     definition=definition,
        ...     workspace_path="/path/to/project",
        ...     thread_id="session-123",
        ... )
        >>> async for event in runtime.astream_events("Analyze this code"):
        ...     print(event)
    """
    settings = settings or get_settings()
    workspace_path = Path(workspace_path).resolve()

    # Get checkpointer from storage backend if not provided
    if checkpointer is None:
        storage_backend = create_storage_backend(settings)
        checkpointer = await storage_backend.get_checkpointer()

    # Resolve tool paths
    tools: list[Any] = []
    for tool_path in definition.tools:
        try:
            module_path, tool_name = tool_path.rsplit(".", 1)
            module = __import__(module_path, fromlist=[tool_name])
            tool = getattr(module, tool_name)
            tools.append(tool)
        except (ImportError, AttributeError, ValueError):
            # Log warning but continue
            import structlog

            logger = structlog.get_logger(__name__)
            logger.warning(f"Failed to load tool: {tool_path}")

    # Create the Deep Agent
    agent = create_cognition_agent(
        project_path=workspace_path,
        system_prompt=definition.system_prompt,
        tools=tools if tools else None,
        memory=definition.memory,
        skills=definition.skills,
        checkpointer=checkpointer,
        settings=settings,
    )

    # Create and return the runtime
    return DeepAgentRuntime(
        agent=agent,
        checkpointer=checkpointer,
        thread_id=thread_id,
    )


# Type alias for runtime implementations
AgentRuntimeType = DeepAgentRuntime

__all__ = [
    "AgentRuntime",
    "DeepAgentRuntime",
    "create_agent_runtime",
    "AgentEvent",
    "TokenEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "StatusEvent",
    "DoneEvent",
    "ErrorEvent",
    "AgentRuntimeType",
]
