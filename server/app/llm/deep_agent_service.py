"""Streaming LLM service using DeepAgents for multi-step task completion.

This service leverages deepagents' built-in capabilities:
- Automatic ReAct loop (LLM → tool → LLM until completion)
- State persistence via thread_id checkpointing
- Built-in planning with write_todos tool
- Context management and conversation summarization
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncGenerator, Optional

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from server.app.settings import Settings
from server.app.agent import create_cognition_agent
from server.app.persistence.factory import create_persistence_backend

logger = structlog.get_logger(__name__)


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


@dataclass
class PlanningEvent:
    """Agent is creating a plan for multi-step task."""

    todos: list[dict[str, Any]]


@dataclass
class StepCompleteEvent:
    """A step in the plan has been completed."""

    step_number: int
    total_steps: int
    description: str


# Union type for all events
StreamEvent = (
    TokenEvent
    | ToolCallEvent
    | ToolResultEvent
    | UsageEvent
    | DoneEvent
    | ErrorEvent
    | PlanningEvent
    | StepCompleteEvent
)


class DeepAgentStreamingService:
    """Streaming service using DeepAgents for multi-step completion.

    This service uses deepagents' create_deep_agent which provides:
    - Automatic multi-turn ReAct loop
    - Built-in write_todos for planning
    - State checkpointing via thread_id
    - Context window management
    """

    def __init__(
        self,
        settings: Settings,
    ):
        """Initialize the deep agent streaming service.

        Args:
            settings: Application settings for LLM configuration.
        """
        self.settings = settings
        self.persistence_backend = create_persistence_backend(settings)

    async def stream_response(
        self,
        session_id: str,
        thread_id: str,
        project_path: str,
        content: str,
        system_prompt: Optional[str] = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream LLM response using DeepAgents with multi-step support."""
        try:
            # Get session to get its specific LLM config
            from server.app.session_store import get_session_store

            store = get_session_store(project_path)
            session = await store.get_session(session_id)

            # Use session settings if available, else fallback to global
            llm_settings = self.settings
            if session and session.config:
                # Merge session config into a temp settings object
                from copy import copy

                llm_settings = copy(self.settings)
                if session.config.provider:
                    llm_settings.llm_provider = session.config.provider
                if session.config.model:
                    llm_settings.llm_model = session.config.model
                if session.config.temperature is not None:
                    llm_settings.llm_temperature = session.config.temperature

            # Get the model with specific settings
            model = await self._get_model(llm_settings)

            # Get checkpointer from persistence backend
            checkpointer = await self.persistence_backend.get_checkpointer()

            # Create the deep agent for this session with the model
            agent = create_cognition_agent(
                project_path=project_path,
                model=model,
                store=None,
                checkpointer=checkpointer,
            )

            # Build the input with enhanced system prompt
            messages = self._build_messages(content, system_prompt)

            # Track state for the stream
            input_tokens = len(content.split())
            output_tokens = 0
            accumulated_content = ""
            current_tool_call = None
            planning_mode = False

            # Stream events from deepagents
            # The agent automatically handles the ReAct loop
            async for event in agent.astream_events(
                {"messages": messages},
                config={"configurable": {"thread_id": thread_id}},
                version="v2",
            ):
                event_type = event.get("event")
                data = event.get("data", {})
                name = event.get("name", "")

                # Handle different event types from deepagents
                if event_type == "on_chat_model_stream":
                    # Streaming tokens from the LLM
                    chunk = data.get("chunk", {})
                    if hasattr(chunk, "content") and chunk.content:
                        accumulated_content += chunk.content
                        output_tokens += len(chunk.content.split())
                        yield TokenEvent(content=chunk.content)

                elif event_type == "on_tool_start":
                    # Tool execution starting
                    tool_name = name
                    tool_args = data.get("input", {})
                    tool_call_id = str(uuid.uuid4())[:8]
                    current_tool_call = tool_call_id

                    # Check if this is a planning tool
                    if tool_name == "write_todos":
                        planning_mode = True
                        todos = tool_args.get("todos", [])
                        yield PlanningEvent(todos=todos)

                    yield ToolCallEvent(
                        name=tool_name,
                        args=tool_args,
                        tool_call_id=tool_call_id,
                    )

                elif event_type == "on_tool_end":
                    # Tool execution completed
                    output = data.get("output", "")
                    tool_call_id = current_tool_call or str(uuid.uuid4())[:8]

                    yield ToolResultEvent(
                        tool_call_id=tool_call_id,
                        output=str(output),
                        exit_code=0,
                    )

                    current_tool_call = None

                elif event_type == "on_chain_end":
                    # The agent's turn is complete
                    # This happens after the ReAct loop finishes
                    pass

            # Yield final usage info
            yield UsageEvent(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=self._estimate_cost(
                    input_tokens, output_tokens, llm_settings.llm_provider
                ),
                provider=llm_settings.llm_provider,
                model=llm_settings.llm_model,
            )

            # Signal completion
            yield DoneEvent()

        except Exception as e:
            logger.error("DeepAgents streaming error", error=str(e), session_id=session_id)
            yield ErrorEvent(message=str(e), code="STREAMING_ERROR")

    def _build_messages(self, content: str, custom_system_prompt: Optional[str] = None) -> list:
        """Build message list with system prompt.

        Args:
            content: User message content.
            custom_system_prompt: Optional custom system prompt.

        Returns:
            List of messages for the agent.
        """
        # Enhanced system prompt with planning instructions
        base_prompt = custom_system_prompt or self._get_default_system_prompt()

        # Add planning instructions if not already present
        if "write_todos" not in base_prompt:
            base_prompt += """

For complex tasks (refactoring, implementing features, debugging):
1. First call write_todos to break down the task into steps
2. Execute each step systematically
3. Mark completion when all todos are done

For simple tasks (single file edits, quick checks), execute directly."""

        messages = [
            SystemMessage(content=base_prompt),
            HumanMessage(content=content),
        ]

        return messages

    def _get_default_system_prompt(self) -> str:
        """Get the default system prompt with planning support."""
        return """You are Cognition, an expert AI coding assistant.

Your goal is to help users write, edit, and understand code. You have access to a filesystem and can execute commands.

Key capabilities:
- Read and write files in the workspace
- List directory contents
- Search files using glob patterns and grep
- Execute shell commands (tests, git, etc.)
- Break down complex tasks using write_todos

Best practices:
1. Always check what files exist before making changes
2. Read relevant files before editing
3. Use edit_file for precise changes rather than rewriting entire files
4. Run tests after making changes
5. Explain your reasoning before taking actions

For complex tasks (refactoring, implementing features, debugging):
1. First call write_todos to create a step-by-step plan
2. Execute each step systematically
3. Mark completion when all todos are done

For simple tasks (single file edits, quick checks), execute directly."""

    async def _get_model(self, settings: Settings) -> Any:
        """Get LLM model from specific settings."""
        from server.app.llm.provider_fallback import ProviderFallbackChain

        fallback_chain = ProviderFallbackChain.from_settings(settings)
        result = await fallback_chain.get_model(settings)
        return result.model

    def _estimate_cost(
        self,
        input_tokens: int,
        output_tokens: int,
        provider: str,
    ) -> float:
        """Estimate cost based on provider pricing."""
        pricing = {
            "openai": {"input": 0.0025, "output": 0.01},
            "bedrock": {"input": 0.003, "output": 0.015},
            "mock": {"input": 0.0, "output": 0.0},
            "openai_compatible": {"input": 0.001, "output": 0.002},
        }
        rates = pricing.get(provider, pricing["openai"])
        input_cost = (input_tokens / 1000) * rates["input"]
        output_cost = (output_tokens / 1000) * rates["output"]
        return round(input_cost + output_cost, 6)


class SessionAgentManager:
    """Manages DeepAgent services per session.

    Creates and caches agent services for each session.
    """

    def __init__(self, settings: Settings):
        """Initialize the session manager.

        Args:
            settings: Application settings.
        """
        self.settings = settings
        self._services: dict[str, DeepAgentStreamingService] = {}
        self._project_paths: dict[str, str] = {}

    def register_session(
        self,
        session_id: str,
        project_path: str,
    ) -> DeepAgentStreamingService:
        """Register a new session.

        Args:
            session_id: Unique session identifier.
            project_path: Path to the project workspace.

        Returns:
            Configured DeepAgentStreamingService for the session.
        """
        service = DeepAgentStreamingService(settings=self.settings)

        self._services[session_id] = service
        self._project_paths[session_id] = project_path

        logger.info(
            "Session registered with DeepAgents",
            session_id=session_id,
            project_path=project_path,
        )

        return service

    def get_service(self, session_id: str) -> Optional[DeepAgentStreamingService]:
        """Get the agent service for a session.

        Args:
            session_id: Session identifier.

        Returns:
            DeepAgentStreamingService if found, None otherwise.
        """
        return self._services.get(session_id)

    def get_project_path(self, session_id: str) -> Optional[str]:
        """Get the project path for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Project path if found, None otherwise.
        """
        return self._project_paths.get(session_id)

    def unregister_session(self, session_id: str) -> None:
        """Unregister a session and clean up resources.

        Args:
            session_id: Session to unregister.
        """
        self._services.pop(session_id, None)
        self._project_paths.pop(session_id, None)

        logger.info("Session unregistered", session_id=session_id)


# Global manager instance
_agent_manager: SessionAgentManager | None = None


def get_session_agent_manager(settings: Settings) -> SessionAgentManager:
    """Get or create the global session agent manager.

    Args:
        settings: Application settings.

    Returns:
        SessionAgentManager instance.
    """
    global _agent_manager
    if _agent_manager is None:
        _agent_manager = SessionAgentManager(settings)
    return _agent_manager
