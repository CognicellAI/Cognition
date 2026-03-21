"""AgentRuntime protocol and DeepAgentRuntime implementation.

This module defines the AgentRuntime protocol and provides a DeepAgentRuntime
wrapper that implements it using the Deep Agents graph compiled by
cognition_agent.py.

Streaming uses LangGraph's native astream() v2 format with
stream_mode=["messages", "updates", "custom"] and subgraphs=True, replacing
the previous brittle astream_events() callback-event parser.

Layer: 4 (Agent Runtime)

Architecture:
- AgentRuntime: Protocol defining the runtime interface
- DeepAgentRuntime: Concrete implementation wrapping Deep Agents
- create_agent_runtime(): Factory function for creating runtimes
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import structlog

logger = structlog.get_logger(__name__)

from langchain_core.messages import AIMessageChunk, ToolMessage
from langgraph.checkpoint.base import BaseCheckpointSaver

from server.app.agent.cognition_agent import create_cognition_agent
from server.app.agent.definition import AgentDefinition
from server.app.settings import Settings, get_settings
from server.app.storage.factory import create_storage_backend

# ============================================================================
# Content normalisation
# ============================================================================


def _content_to_str(content: str | list | None) -> str:
    """Normalise LangChain message content to a plain string.

    LangChain's ``BaseMessage.content`` is typed ``str | list[str | dict]``.
    Different providers (and different events within the same provider) use
    different formats:

    * OpenAI / OpenAI-compatible: plain ``str`` for every delta.
    * Bedrock Converse — *first* delta in a block:
        ``[{"type": "text", "text": "J", "index": 0}]``  (has "type")
    * Bedrock Converse — *subsequent* deltas in the same block:
        ``[{"text": "ello", "index": 0}]``               (no "type" key!)
    * Bedrock Converse — stop / metadata events:
        ``""`` or ``[]``

    ``BaseMessage.text`` only extracts blocks where ``block["type"] == "text"``,
    so it silently drops every Bedrock delta after the first one in each block.
    This function handles all cases, including blocks that carry ``"text"``
    without a ``"type"`` key.
    """
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if isinstance(text, str):
                    # Accept blocks with OR without a "type" key — both appear
                    # in real Bedrock streaming output.
                    parts.append(text)
        return "".join(parts)
    return str(content)


# ============================================================================
# Canonical Event Types
# ============================================================================
# These event types are the single source of truth for agent runtime events.
# They are used across:
# - Agent runtime (this file)
# - Deep agent service streaming
# - API layer serialization (converted to Pydantic models in api/models.py)


@dataclass
class AgentEvent:
    """Base class for agent runtime events."""

    pass


@dataclass
class TokenEvent(AgentEvent):
    """Streaming token from the LLM.

    ``content`` is always a plain ``str``. The ``__post_init__`` coerces any
    ``str | list[str | dict]`` value (the raw LangChain content type) to str
    at construction time, so every downstream consumer is guaranteed a string
    regardless of which provider emitted the chunk.
    """

    content: str

    def __post_init__(self) -> None:
        # LangChain's BaseMessage.content is typed `str | list[str | dict]`.
        # Providers such as Bedrock return list[dict] deltas during streaming,
        # often WITHOUT a "type" key (e.g. [{"text": "J", "index": 0}]).
        # BaseMessage.text only matches {"type": "text"} blocks, so it silently
        # drops most Bedrock deltas. _content_to_str handles all known formats.
        if not isinstance(self.content, str):
            self.content = _content_to_str(self.content)


@dataclass
class ToolCallEvent(AgentEvent):
    """Tool call requested by the agent."""

    name: str
    args: dict[str, Any]
    tool_call_id: str


@dataclass
class ToolResultEvent(AgentEvent):
    """Result of tool execution."""

    tool_call_id: str
    output: str
    exit_code: int = 0


@dataclass
class StatusEvent(AgentEvent):
    """Agent status update."""

    status: str


@dataclass
class DoneEvent(AgentEvent):
    """Stream completion signal."""

    # ISSUE-019: Include message_id so clients can correlate with persisted message
    message_id: str | None = None


@dataclass
class ErrorEvent(AgentEvent):
    """Error during execution."""

    message: str
    code: str = "ERROR"


@dataclass
class UsageEvent(AgentEvent):
    """Token usage information."""

    input_tokens: int
    output_tokens: int
    estimated_cost: float = 0.0
    provider: str = "unknown"
    model: str = "unknown"


@dataclass
class PlanningEvent(AgentEvent):
    """Agent is creating a plan for multi-step task."""

    todos: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class StepCompleteEvent(AgentEvent):
    """A step in the plan has been completed."""

    step_number: int
    total_steps: int
    description: str


@dataclass
class DelegationEvent(AgentEvent):
    """Agent is delegating to a sub-agent."""

    from_agent: str
    to_agent: str
    task: str


# Union type for all events
StreamEvent = (
    TokenEvent
    | ToolCallEvent
    | ToolResultEvent
    | StatusEvent
    | DoneEvent
    | ErrorEvent
    | UsageEvent
    | PlanningEvent
    | StepCompleteEvent
    | DelegationEvent
)


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
        recursion_limit: int = 1000,
        context: Any | None = None,
    ):
        """Initialize the DeepAgentRuntime.

        Args:
            agent: The Deep Agent instance from create_cognition_agent
            checkpointer: Checkpoint saver for state persistence
            thread_id: Optional default thread ID
            recursion_limit: Maximum recursion depth for agent ReACT loops
            context: Optional invocation context (e.g. CognitionContext) for
                Store namespace scoping. Forwarded to astream() and ainvoke()
                so that ``runtime.context`` is available inside nodes and
                middleware.
        """
        self._agent = agent
        self._checkpointer = checkpointer
        self._thread_id = thread_id
        self._recursion_limit = recursion_limit
        self._aborted: set[str] = set()
        self._context = context

    async def astream_events(
        self,
        input_data: str | dict[str, Any],
        thread_id: str | None = None,
    ) -> AsyncIterator[AgentEvent]:
        """Stream agent execution events using the LangGraph v2 astream format.

        Uses ``stream_mode=["messages", "updates", "custom"]`` with
        ``subgraphs=True`` and ``version="v2"`` so that:

        * Token streaming comes from the structured ``messages`` chunks rather
          than raw ``on_chat_model_stream`` callbacks — avoids the brittle
          string-matching that the old ``astream_events`` approach required.
        * Tool call IDs are real IDs taken from ``tool_call_chunks[*].id`` and
          from the ``ToolMessage.tool_call_id`` field, so ``ToolCallEvent`` and
          ``ToolResultEvent`` can be correlated by the client.
        * Subagent execution is visible via ``chunk["ns"]`` — events that
          arrive with a non-empty namespace came from a subagent and are
          translated to ``DelegationEvent``.
        * Custom status events emitted by tools / middleware via
          ``get_stream_writer()`` arrive as ``custom`` chunks.

        Args:
            input_data: User input (string or structured dict with ``messages``
                key)
            thread_id: Optional thread ID for state persistence

        Yields:
            AgentEvent: TokenEvent, ToolCallEvent, ToolResultEvent,
                       DelegationEvent, StatusEvent, DoneEvent, or ErrorEvent
        """
        tid = thread_id or self._thread_id or "default"

        if tid == "default" and not thread_id and not self._thread_id:
            logger.warning(
                "No thread_id set on astream_events — all operations share the 'default' "
                "thread which may cause cross-session state bleed in multi-user deployments"
            )

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

            config = {"configurable": {"thread_id": tid}, "recursion_limit": self._recursion_limit}

            # Accumulator for streaming tool call chunks.
            # Maps tool_call_id -> {"name": str, "args": str} so we can emit
            # ToolCallEvent once the name is first seen and assemble args.
            _pending_tool_calls: dict[str, dict[str, Any]] = {}

            # Track active subagent delegations so we emit DelegationEvent once
            # per subagent invocation (on first subagent activity), not on every
            # chunk.
            _active_delegations: set[str] = set()

            async for chunk in self._agent.astream(
                agent_input,
                config=config,
                context=self._context,
                stream_mode=["messages", "updates", "custom"],
                subgraphs=True,
                version="v2",
            ):
                # Check if aborted mid-stream
                if tid in self._aborted:
                    self._aborted.discard(tid)
                    yield ErrorEvent(message="Execution was aborted", code="ABORTED")
                    return

                chunk_type: str = chunk.get("type", "")
                ns: tuple[str, ...] = chunk.get("ns", ())
                data: Any = chunk.get("data")

                # ── messages mode ────────────────────────────────────────────
                # Yields (message_chunk, metadata) tuples.
                # message_chunk is a LangChain BaseMessage subclass:
                #   AIMessageChunk  → token content or tool call fragments
                #   ToolMessage     → tool execution result
                if chunk_type == "messages":
                    msg, _metadata = data

                    # ── Token streaming ──────────────────────────────────────
                    # AIMessageChunk with text content → TokenEvent
                    if isinstance(msg, AIMessageChunk) and msg.content:
                        text = _content_to_str(msg.content)
                        if text:
                            yield TokenEvent(content=text)

                    # ── Tool call start ──────────────────────────────────────
                    # AIMessageChunk with tool_call_chunks carries real IDs.
                    # Chunks stream in over multiple messages; we emit
                    # ToolCallEvent on the first chunk that has a name.
                    if isinstance(msg, AIMessageChunk) and getattr(msg, "tool_call_chunks", None):
                        for tc_chunk in msg.tool_call_chunks:
                            tc_id: str | None = tc_chunk.get("id")
                            tc_name: str | None = tc_chunk.get("name")
                            tc_args: str = tc_chunk.get("args") or ""

                            if not tc_id:
                                continue

                            if tc_id not in _pending_tool_calls:
                                _pending_tool_calls[tc_id] = {"name": "", "args": ""}

                            # Name arrives on the first chunk for this tool call
                            if tc_name and not _pending_tool_calls[tc_id]["name"]:
                                _pending_tool_calls[tc_id]["name"] = tc_name
                                yield ToolCallEvent(
                                    name=tc_name,
                                    args={},  # args stream in; full args on result
                                    tool_call_id=tc_id,
                                )

                            _pending_tool_calls[tc_id]["args"] += tc_args

                    # ── Tool result ──────────────────────────────────────────
                    # ToolMessage carries the real tool_call_id that correlates
                    # to the ToolCallEvent emitted above.
                    if isinstance(msg, ToolMessage):
                        tool_call_id: str = getattr(msg, "tool_call_id", "") or ""
                        output = _content_to_str(msg.content) if msg.content else ""
                        _pending_tool_calls.pop(tool_call_id, None)
                        yield ToolResultEvent(
                            tool_call_id=tool_call_id,
                            output=output,
                            exit_code=0,
                        )

                # ── updates mode ─────────────────────────────────────────────
                # Yields {node_name: state_updates} dicts.
                # Used to detect subagent lifecycle events via namespace.
                elif chunk_type == "updates":
                    is_subagent = any(s.startswith("tools:") for s in ns)

                    if is_subagent:
                        # Extract the pregel task ID from the namespace to use
                        # as a stable delegation identifier.
                        delegation_key = next((s for s in ns if s.startswith("tools:")), "")
                        if delegation_key and delegation_key not in _active_delegations:
                            _active_delegations.add(delegation_key)
                            # Extract tool_call_id portion (after "tools:")
                            subagent_id = (
                                delegation_key.split(":", 1)[1]
                                if ":" in delegation_key
                                else delegation_key
                            )
                            yield DelegationEvent(
                                from_agent="main",
                                to_agent="subagent",
                                task=subagent_id,
                            )

                # ── custom mode ───────────────────────────────────────────────
                # Yields arbitrary dicts emitted via get_stream_writer() in
                # tools or middleware — e.g. {"status": "thinking"}.
                elif chunk_type == "custom":
                    if isinstance(data, dict):
                        status = data.get("status")
                        if status and isinstance(status, str):
                            yield StatusEvent(status=status)

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

            config = {"configurable": {"thread_id": tid}, "recursion_limit": self._recursion_limit}

            # Invoke the agent
            result = await self._agent.ainvoke(agent_input, config=config, context=self._context)

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
            logger.warning(
                "Failed to retrieve agent state — returning None",
                thread_id=tid,
                exc_info=True,
            )
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


def _resolve_middleware(mw_spec: str | dict[str, Any]) -> Any | None:
    """Resolve middleware from string path or dict spec.

    Supports well-known upstream middleware names:
    - tool_retry -> ToolRetryMiddleware
    - tool_call_limit -> ToolCallLimitMiddleware
    - pii -> PIIMiddleware
    - human_in_the_loop -> HumanInTheLoopMiddleware

    Unknown names fall back to dotted import path resolution.

    Args:
        mw_spec: Middleware specification (string path or dict with name and kwargs)

    Returns:
        Instantiated middleware or None if resolution failed
    """
    import structlog

    logger = structlog.get_logger(__name__)

    # Handle dict spec: {"name": "...", "max_retries": 3, ...}
    if isinstance(mw_spec, dict):
        name = mw_spec.get("name", "")
        kwargs = {k: v for k, v in mw_spec.items() if k != "name"}
    else:
        name = mw_spec
        kwargs = {}

    # Well-known upstream middleware mappings
    upstream_middleware: dict[str, tuple[str, str]] = {
        "tool_retry": ("langchain.agents.middleware", "ToolRetryMiddleware"),
        "tool_call_limit": ("langchain.agents.middleware", "ToolCallLimitMiddleware"),
        "pii": ("langchain.agents.middleware", "PIIMiddleware"),
        "human_in_the_loop": ("langchain.agents.middleware", "HumanInTheLoopMiddleware"),
    }

    try:
        if name in upstream_middleware:
            # Import upstream middleware
            module_path, class_name = upstream_middleware[name]
            module = __import__(module_path, fromlist=[class_name])
            mw_class = getattr(module, class_name)
            return mw_class(**kwargs)
        else:
            # Fall back to dotted import path resolution
            parts = name.split(".")
            if len(parts) < 2:
                logger.warning(f"Invalid middleware path: {name}")
                return None

            module_path = ".".join(parts[:-1])
            class_name = parts[-1]
            module = __import__(module_path, fromlist=[class_name])
            mw_class = getattr(module, class_name)
            return mw_class(**kwargs)

    except ImportError as e:
        logger.warning(f"Failed to import middleware: {name}", error=str(e))
        return None
    except Exception as e:
        logger.warning(f"Failed to instantiate middleware: {name}", error=str(e))
        return None


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

    # Resolve tool paths with namespace validation
    tools: list[Any] = []
    trusted_namespaces = (
        getattr(settings, "trusted_tool_namespaces", ["server.app.tools"])
        if settings
        else ["server.app.tools"]
    )
    for tool_path in definition.tools:
        try:
            module_path, tool_name = tool_path.rsplit(".", 1)

            # Validate namespace allowlist
            is_trusted = any(
                module_path == ns or module_path.startswith(ns + ".") for ns in trusted_namespaces
            )
            if not is_trusted:
                from server.app.exceptions import CognitionError

                raise CognitionError(
                    f"Tool path '{tool_path}' is not in a trusted namespace. "
                    f"Allowed namespaces: {trusted_namespaces}"
                )

            module = __import__(module_path, fromlist=[tool_name])
            tool = getattr(module, tool_name)
            tools.append(tool)
        except ImportError as e:
            import structlog

            logger = structlog.get_logger(__name__)
            logger.warning(f"Failed to import tool: {tool_path}", error=str(e))
        except AttributeError as e:
            import structlog

            logger = structlog.get_logger(__name__)
            logger.warning(f"Tool not found in module: {tool_path}", error=str(e))
        except Exception as e:
            # Check if this is a CognitionError by checking module
            if hasattr(e, "__module__") and "exceptions" in str(e.__module__):
                # Re-raise our own errors
                raise
            import structlog

            logger = structlog.get_logger(__name__)
            logger.warning(f"Failed to load tool: {tool_path}", error=str(e))

    # Resolve middleware with upstream support
    resolved_middleware: list[Any] = []
    for mw_spec in definition.middleware:
        mw_instance = _resolve_middleware(mw_spec)
        if mw_instance is not None:
            resolved_middleware.append(mw_instance)

    # Create the Deep Agent
    agent = await create_cognition_agent(
        project_path=workspace_path,
        system_prompt=definition.system_prompt,
        tools=tools if tools else None,
        memory=definition.memory,
        skills=definition.skills,
        middleware=resolved_middleware if resolved_middleware else None,
        checkpointer=checkpointer,
        settings=settings,
    )

    # Create and return the runtime
    # Per-agent recursion_limit overrides the global default (1000) when set
    effective_recursion_limit = (
        definition.config.recursion_limit if definition.config.recursion_limit is not None else 1000
    )
    return DeepAgentRuntime(
        agent=agent,
        checkpointer=checkpointer,
        thread_id=thread_id,
        recursion_limit=effective_recursion_limit,
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
    "StepCompleteEvent",
    "PlanningEvent",
    "UsageEvent",
    "AgentRuntimeType",
]
