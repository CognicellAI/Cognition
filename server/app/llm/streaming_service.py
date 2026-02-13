"""Streaming LLM service for REST API.

Provides real LLM streaming with tool calling support.
Streams tokens, tool calls, and usage info via async generators.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Callable, Optional, Sequence

import structlog
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    SystemMessage,
    ToolMessage,
)
from langchain_core.tools import BaseTool, tool

from server.app.settings import Settings
from server.app.sandbox import LocalSandbox
from server.app.llm.provider_fallback import ProviderFallbackChain
from server.app.observability import get_tracer, span

logger = structlog.get_logger(__name__)
tracer = get_tracer(__name__)


@dataclass
class StreamingConfig:
    """Configuration for streaming LLM calls."""

    model: Any = None  # LangChain model instance
    system_prompt: str = "You are a helpful coding assistant."
    temperature: float = 0.7
    max_tokens: Optional[int] = None
    tools: Optional[list[BaseTool]] = None


@dataclass
class TokenEvent:
    """A streaming token from the LLM."""

    content: str


@dataclass
class ToolCallEvent:
    """A tool call requested by the LLM."""

    name: str
    args: dict[str, Any]
    tool_call_id: str


@dataclass
class ToolResultEvent:
    """Result of a tool execution."""

    tool_call_id: str
    output: str
    exit_code: int = 0


@dataclass
class UsageEvent:
    """Token usage information."""

    input_tokens: int
    output_tokens: int
    estimated_cost: float = 0.0
    provider: str = "unknown"
    model: str = "unknown"


@dataclass
class DoneEvent:
    """Stream completion signal."""

    pass


@dataclass
class ErrorEvent:
    """Error during streaming."""

    message: str
    code: str = "ERROR"


# Union type for all events
StreamEvent = TokenEvent | ToolCallEvent | ToolResultEvent | UsageEvent | DoneEvent | ErrorEvent


def create_file_tools(sandbox: LocalSandbox) -> list[BaseTool]:
    """Create file operation tools bound to a sandbox.

    Args:
        sandbox: The sandbox to execute file operations in.

    Returns:
        List of LangChain tools for file operations.
    """

    @tool
    def read_file(path: str) -> str:
        """Read the contents of a file.

        Args:
            path: Path to the file relative to workspace root.

        Returns:
            File contents or error message.
        """
        result = sandbox.execute(f'cat "{path}"')
        if result.exit_code != 0:
            return f"Error reading file: {result.output}"
        return result.output

    @tool
    def write_file(path: str, content: str) -> str:
        """Write content to a file.

        Args:
            path: Path to the file relative to workspace root.
            content: Content to write.

        Returns:
            Success message or error.
        """
        # Use printf to avoid shell interpretation issues
        escaped = content.replace("'", "'\\''")
        result = sandbox.execute(f"printf '%s' '{escaped}' > '{path}'")
        if result.exit_code != 0:
            return f"Error writing file: {result.output}"
        return f"Successfully wrote to {path}"

    @tool
    def list_files(path: str = ".") -> str:
        """List files in a directory.

        Args:
            path: Directory path relative to workspace root.

        Returns:
            List of files or error message.
        """
        result = sandbox.execute(f'ls -la "{path}"')
        return result.output

    @tool
    def execute_command(command: str) -> str:
        """Execute a shell command.

        Args:
            command: Shell command to execute.

        Returns:
            Command output.
        """
        result = sandbox.execute(command)
        return result.output

    @tool
    def search_files(pattern: str, path: str = ".") -> str:
        """Search files using grep.

        Args:
            pattern: Search pattern.
            path: Directory to search in.

        Returns:
            Search results.
        """
        result = sandbox.execute(f'grep -rn "{pattern}" "{path}"')
        return result.output

    return [read_file, write_file, list_files, execute_command, search_files]


class StreamingLLMService:
    """Service for streaming LLM interactions with tool calling.

    This service handles:
    - LLM initialization with fallback providers
    - Token streaming via LangChain's astream
    - Tool binding and execution
    - Usage tracking
    - Conversation history management
    """

    def __init__(
        self,
        settings: Settings,
        sandbox: Optional[LocalSandbox] = None,
    ):
        """Initialize the streaming LLM service.

        Args:
            settings: Application settings for LLM configuration.
            sandbox: Optional sandbox for tool execution.
        """
        self.settings = settings
        self.sandbox = sandbox
        self._fallback_chain = ProviderFallbackChain.from_settings(settings)
        self._conversation_history: dict[str, list[BaseMessage]] = {}

    async def _get_model(self) -> Any:
        """Get LLM model with fallback support."""
        result = await self._fallback_chain.get_model(self.settings)
        return result.model

    def _get_or_create_history(self, session_id: str) -> list[BaseMessage]:
        """Get or initialize conversation history for a session."""
        if session_id not in self._conversation_history:
            self._conversation_history[session_id] = []
        return self._conversation_history[session_id]

    def clear_history(self, session_id: str) -> None:
        """Clear conversation history for a session."""
        self._conversation_history.pop(session_id, None)

    async def stream_response(
        self,
        session_id: str,
        content: str,
        config: Optional[StreamingConfig] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream LLM response with potential tool calls.

        Args:
            session_id: Unique session identifier.
            content: User message content.
            config: Optional streaming configuration.

        Yields:
            Stream events (tokens, tool calls, usage, etc.)
        """
        config = config or StreamingConfig()

        try:
            # Get model
            if config.model:
                model = config.model
            else:
                model = await self._get_model()

            # Get conversation history
            history = self._get_or_create_history(session_id)

            # Add system prompt if history is empty
            if not history:
                history.append(SystemMessage(content=config.system_prompt))

            # Add user message
            history.append(HumanMessage(content=content))

            # Bind tools if sandbox is available
            if self.sandbox and config.tools is None:
                config.tools = create_file_tools(self.sandbox)

            if config.tools:
                model_with_tools = model.bind_tools(config.tools)
            else:
                model_with_tools = model

            # Stream the response
            input_tokens = len(content.split())  # Rough estimate
            output_tokens = 0
            accumulated_content = ""

            # First call - may return tool calls
            async for chunk in model_with_tools.astream(history):
                if hasattr(chunk, "content") and chunk.content:
                    accumulated_content += chunk.content
                    output_tokens += len(chunk.content.split())
                    yield TokenEvent(content=chunk.content)

                # Handle tool calls
                if hasattr(chunk, "tool_calls") and chunk.tool_calls:
                    for tool_call in chunk.tool_calls:
                        tool_call_id = tool_call.get("id", str(uuid.uuid4()))
                        yield ToolCallEvent(
                            name=tool_call.get("name", "unknown"),
                            args=tool_call.get("args", {}),
                            tool_call_id=tool_call_id,
                        )

                        # Execute tool if sandbox available
                        if self.sandbox and config.tools:
                            result = await self._execute_tool(tool_call, config.tools)
                            yield ToolResultEvent(
                                tool_call_id=tool_call_id,
                                output=result.get("output", ""),
                                exit_code=result.get("exit_code", 0),
                            )

                            # Add tool result to history
                            history.append(
                                ToolMessage(
                                    content=result.get("output", ""),
                                    tool_call_id=tool_call_id,
                                )
                            )

            # Add assistant message to history
            if accumulated_content:
                history.append(AIMessage(content=accumulated_content))

            # Yield usage info
            yield UsageEvent(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=self._estimate_cost(
                    input_tokens, output_tokens, self.settings.llm_provider
                ),
                provider=self.settings.llm_provider,
                model=self.settings.llm_model,
            )

            # Signal completion
            yield DoneEvent()

        except Exception as e:
            logger.error("Streaming error", error=str(e), session_id=session_id)
            yield ErrorEvent(message=str(e), code="STREAMING_ERROR")

    async def _execute_tool(
        self,
        tool_call: dict[str, Any],
        tools: list[BaseTool],
    ) -> dict[str, Any]:
        """Execute a tool call.

        Args:
            tool_call: Tool call specification.
            tools: Available tools.

        Returns:
            Tool execution result.
        """
        tool_name = tool_call.get("name")
        tool_args = tool_call.get("args", {})

        # Find the tool
        tool_map = {t.name: t for t in tools}
        if tool_name not in tool_map:
            return {
                "output": f"Unknown tool: {tool_name}",
                "exit_code": 1,
            }

        try:
            # Execute tool
            tool = tool_map[tool_name]
            result = await tool.ainvoke(tool_args)
            return {
                "output": str(result),
                "exit_code": 0,
            }
        except Exception as e:
            return {
                "output": f"Error executing {tool_name}: {str(e)}",
                "exit_code": 1,
            }

    def _estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        provider: str,
    ) -> float:
        """Estimate cost based on provider pricing.

        Args:
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            provider: LLM provider name.

        Returns:
            Estimated cost in USD.
        """
        # Rough pricing estimates (per 1K tokens)
        pricing = {
            "openai": {"input": 0.0025, "output": 0.01},  # GPT-4o
            "bedrock": {"input": 0.003, "output": 0.015},  # Claude 3
            "mock": {"input": 0.0, "output": 0.0},
            "openai_compatible": {"input": 0.001, "output": 0.002},
        }

        rates = pricing.get(provider, pricing["openai"])
        input_cost = (input_tokens / 1000) * rates["input"]
        output_cost = (output_tokens / 1000) * rates["output"]

        return round(input_cost + output_cost, 6)


class SessionLLMManager:
    """Manages LLM services per session.

    Creates and caches LLM services for each session with proper
    sandbox and tool configuration.
    """

    def __init__(self, settings: Settings):
        """Initialize the session manager.

        Args:
            settings: Application settings.
        """
        self.settings = settings
        self._services: dict[str, StreamingLLMService] = {}
        self._project_paths: dict[str, str] = {}

    def register_session(
        self,
        session_id: str,
        project_path: str,
    ) -> StreamingLLMService:
        """Register a new session with its project path.

        Args:
            session_id: Unique session identifier.
            project_path: Path to the project workspace.

        Returns:
            Configured StreamingLLMService for the session.
        """
        # Create sandbox for this session
        sandbox = LocalSandbox(root_dir=project_path)

        # Create service with tools bound to sandbox
        service = StreamingLLMService(
            settings=self.settings,
            sandbox=sandbox,
        )

        self._services[session_id] = service
        self._project_paths[session_id] = project_path

        logger.info(
            "Session registered",
            session_id=session_id,
            project_path=project_path,
        )

        return service

    def get_service(self, session_id: str) -> Optional[StreamingLLMService]:
        """Get the LLM service for a session.

        Args:
            session_id: Session identifier.

        Returns:
            StreamingLLMService if found, None otherwise.
        """
        return self._services.get(session_id)

    def unregister_session(self, session_id: str) -> None:
        """Unregister a session and clean up resources.

        Args:
            session_id: Session to unregister.
        """
        service = self._services.pop(session_id, None)
        if service:
            service.clear_history(session_id)

        self._project_paths.pop(session_id, None)

        logger.info("Session unregistered", session_id=session_id)


# Global manager instance
_session_manager: SessionLLMManager | None = None


def get_session_llm_manager(settings: Settings) -> SessionLLMManager:
    """Get or create the global session LLM manager.

    Args:
        settings: Application settings.

    Returns:
        SessionLLMManager instance.
    """
    global _session_manager
    if _session_manager is None:
        _session_manager = SessionLLMManager(settings)
    return _session_manager
