"""Streaming LLM service using DeepAgents for multi-step task completion.

This service leverages deepagents' built-in capabilities:
- Automatic ReAct loop (LLM → tool → LLM until completion)
- State persistence via thread_id checkpointing
- Built-in planning with write_todos tool
- Context management and conversation summarization

LLM provider/model configuration is now read from the ConfigRegistry rather
than directly from Settings. Settings still controls infrastructure concerns
(sandbox, persistence, observability, etc.).
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Any

import structlog
from langchain_core.messages import HumanMessage, SystemMessage

from server.app.agent import create_cognition_agent
from server.app.agent.runtime import (
    DeepAgentRuntime,
    DelegationEvent,
    DoneEvent,
    ErrorEvent,
    PlanningEvent,
    StatusEvent,
    StepCompleteEvent,  # noqa: F401 — re-exported for consumers of this module
    StreamEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    UsageEvent,
)
from server.app.settings import Settings
from server.app.storage import get_storage_backend
from server.app.storage.factory import create_storage_backend

logger = structlog.get_logger(__name__)


def _get_model_id_from_provider_config(provider: str, model: str, bedrock_model: str | None) -> str:
    """Extract the effective model ID string given provider/model values."""
    if provider == "bedrock" and bedrock_model:
        return bedrock_model
    return model


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
            settings: Application settings for infrastructure configuration.
        """
        self.settings = settings
        self.storage_backend = create_storage_backend(settings)

    async def stream_response(
        self,
        session_id: str,
        thread_id: str,
        project_path: str,
        content: str,
        system_prompt: str | None = None,
        manager: SessionAgentManager | None = None,
        scope: dict[str, str] | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Stream LLM response using DeepAgents with multi-step support."""
        runtime: DeepAgentRuntime | None = None
        try:
            # Get session to get its specific LLM config
            storage = get_storage_backend()
            session = await storage.get_session(session_id)

            # Resolve provider/model from ConfigRegistry (scope-aware)
            # Session-level overrides take highest priority.
            provider, model_id, max_tokens, recursion_limit = await self._resolve_llm_config(
                session=session,
                scope=scope,
            )

            # Build a minimal settings-like object for the model factory
            # (only the fields that provider_fallback actually reads)
            llm_settings = _LlmSettings(
                provider=provider,
                model=model_id,
                max_tokens=max_tokens,
                recursion_limit=recursion_limit,
                settings=self.settings,
            )

            # Get the model with specific settings
            model = await self._get_model(llm_settings)

            # Get checkpointer from storage backend
            checkpointer = await self.storage_backend.get_checkpointer()

            # Get tools from AgentRegistry if available
            custom_tools = None
            try:
                from server.app.agent_registry import get_agent_registry

                registry = get_agent_registry()
                custom_tools = registry.create_tools()
            except RuntimeError:
                # Registry not initialized (e.g., in test contexts)
                registry = None
                custom_tools = []
            except Exception:
                # Any other error, fall back to no custom tools
                registry = None
                custom_tools = []

            # Determine agent definition to use
            # 1. Use session.agent_name if valid
            # 2. Fall back to "default"
            # 3. Use generic config if registry is missing
            system_prompt = system_prompt  # Use override if provided
            subagents = []
            agent_skills = []

            if registry and session:
                from server.app.agent.agent_definition_registry import (
                    get_agent_definition_registry,
                )

                def_registry = get_agent_definition_registry()
                if def_registry:
                    # Look up the agent definition
                    agent_def = def_registry.get(session.agent_name)
                    if not agent_def:
                        # Fallback to default if bound agent is missing
                        agent_def = def_registry.get("default")

                    if agent_def:
                        # Use agent's system prompt if not overridden
                        if system_prompt is None:
                            system_prompt = agent_def.system_prompt

                        # Use agent's skills and add API skills route
                        if agent_def.skills:
                            agent_skills = list(agent_def.skills)
                            # Always include API skills route for ConfigRegistry-backed skills
                            if "/skills/api/" not in agent_skills:
                                agent_skills.append("/skills/api/")

                        # Add subagents available to this agent
                        # Primary agents get all subagents except themselves
                        all_subagents = def_registry.subagents()
                        subagents = [
                            s.to_subagent() for s in all_subagents if s.name != agent_def.name
                        ]

            # Resolve MCP servers from ConfigRegistry
            mcp_configs = await self._resolve_mcp_configs(scope=scope)

            # Create the deep agent for this session with the model
            agent = await create_cognition_agent(
                project_path=project_path,
                model=model,
                store=None,
                checkpointer=checkpointer,
                settings=self.settings,
                tools=custom_tools if custom_tools else None,
                system_prompt=system_prompt,
                skills=agent_skills,
                subagents=subagents,
                mcp_configs=mcp_configs or None,
                scope=scope,
            )

            # Create runtime and register for abort tracking
            runtime = DeepAgentRuntime(
                agent=agent,
                checkpointer=checkpointer,
                thread_id=thread_id,
                recursion_limit=recursion_limit,
            )
            if manager:
                manager.register_runtime(session_id, runtime)

            # Build the input with enhanced system prompt
            # Pass None for system_prompt here because we passed it to create_cognition_agent
            # The agent factory handles embedding it into the graph state
            messages = self._build_messages(content, None)

            # Track state for the stream
            input_tokens = len(content.split())
            output_tokens = 0
            accumulated_content = ""
            _current_tool_call = None
            _planning_mode = False

            # ISSUE-011: Track plan steps for step_complete events
            _plan_todos: list[dict] = []
            _current_step_index = -1
            _completed_steps: set[int] = set()

            # Stream events from deepagents using runtime (enables abort)
            # The agent automatically handles the ReAct loop
            try:
                async for event in runtime.astream_events(
                    {"messages": messages},
                    thread_id=thread_id,
                ):
                    # Handle different event types from runtime
                    if isinstance(event, TokenEvent):
                        accumulated_content += event.content
                        output_tokens += len(event.content.split())
                        yield event

                    elif isinstance(event, ToolCallEvent):
                        _current_tool_call = event.tool_call_id
                        yield event

                    elif isinstance(event, ToolResultEvent):
                        _current_tool_call = None
                        yield event

                    elif isinstance(event, PlanningEvent):
                        _planning_mode = True
                        _plan_todos = event.todos
                        yield event

                    elif isinstance(event, DelegationEvent) or isinstance(event, StatusEvent):
                        yield event

                    elif isinstance(event, ErrorEvent):
                        yield event
                        if event.code == "ABORTED":
                            return

                    # DoneEvent from the runtime is intentionally absorbed here.
                    # The service emits its own authoritative DoneEvent below, after
                    # UsageEvent, so the caller always receives exactly one done signal.

            finally:
                # Unregister runtime when streaming completes
                if manager:
                    manager.unregister_runtime(session_id)

            # Yield final usage info
            yield UsageEvent(
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated_cost=self._estimate_cost(input_tokens, output_tokens, provider),
                provider=provider,
                model=model_id,
            )

            # Signal completion
            yield DoneEvent()

        except Exception as e:
            logger.error("DeepAgents streaming error", error=str(e), session_id=session_id)
            yield ErrorEvent(message=str(e), code="STREAMING_ERROR")

    async def _resolve_llm_config(
        self,
        session: Any,
        scope: dict[str, str] | None,
    ) -> tuple[str, str, int | None, int]:
        """Resolve provider, model, max_tokens, and recursion_limit.

        Priority (highest to lowest):
        1. session.config overrides
        2. ConfigRegistry (scope-aware)
        3. Hardcoded defaults

        Returns:
            (provider, model_id, max_tokens, recursion_limit)
        """
        # Start with ConfigRegistry defaults
        provider = "mock"
        model_id = "gpt-4o"
        max_tokens: int | None = 20000
        recursion_limit = 1000

        try:
            from server.app.storage.config_registry import get_config_registry

            reg = get_config_registry()
            prov_defaults = await reg.get_global_provider_defaults(scope)
            provider = prov_defaults.provider
            model_id = prov_defaults.model
            if prov_defaults.max_tokens is not None:
                max_tokens = prov_defaults.max_tokens
        except RuntimeError:
            pass  # Registry not initialized (tests)

        # Apply session-level overrides
        if session and session.config:
            if session.config.provider:
                provider = session.config.provider
            if session.config.model:
                model_id = session.config.model
            if session.config.max_tokens is not None:
                max_tokens = session.config.max_tokens
            if session.config.recursion_limit is not None:
                recursion_limit = session.config.recursion_limit

        return provider, model_id, max_tokens, recursion_limit

    async def _resolve_mcp_configs(self, scope: dict[str, str] | None) -> list[Any]:
        """Load MCP server registrations from ConfigRegistry.

        Returns:
            List of McpServerConfig instances ready for McpManager.
        """
        try:
            from server.app.agent.mcp_client import McpServerConfig
            from server.app.storage.config_registry import get_config_registry

            reg = get_config_registry()
            servers = await reg.list_mcp_servers(scope)
            return [
                McpServerConfig(
                    name=s.name,
                    url=s.url,
                    headers=s.headers,
                    enabled=s.enabled,
                )
                for s in servers
                if s.enabled
            ]
        except RuntimeError:
            return []

    def _build_messages(self, user_content: str, custom_system_prompt: str | None = None) -> list:
        """Build message list with system prompt.

        Args:
            user_content: User message content.
            custom_system_prompt: Optional custom system prompt. If None, no SystemMessage
                is added (the system prompt was already passed to create_cognition_agent).

        Returns:
            List of messages for the agent.
        """
        # ISSUE-014: Only add SystemMessage when explicitly provided
        # When custom_system_prompt is None, the agent was created with system_prompt
        # already embedded in the graph, so we skip adding another SystemMessage
        messages: list = []

        if custom_system_prompt is not None:
            # Enhanced system prompt with planning instructions
            base_prompt = custom_system_prompt

            # Add planning instructions if not already present
            if "write_todos" not in base_prompt:
                base_prompt += """

For complex tasks (refactoring, implementing features, debugging):
1. First call write_todos to break down the task into steps
2. Execute each step systematically
3. Mark completion when all todos are done

For simple tasks (single file edits, quick checks), execute directly."""

            messages.append(SystemMessage(content=base_prompt))

        messages.append(HumanMessage(content=user_content))
        return messages

    async def _get_model(self, llm_settings: Any) -> Any:
        """Get LLM model from specific settings."""
        from server.app.llm.provider_fallback import ProviderFallbackChain

        fallback_chain = ProviderFallbackChain.from_settings(llm_settings)
        result = await fallback_chain.get_model(llm_settings)
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


class _LlmSettings:
    """Minimal settings-like object for ProviderFallbackChain.from_settings().

    ProviderFallbackChain.from_settings() reads:
    - settings.llm_provider
    - settings.llm_model / settings.bedrock_model_id
    - settings.llm_max_tokens (for the factory)
    - Credential env vars are read by the factory, not this object.

    This shim avoids modifying provider_fallback.py's public interface while
    still providing the ConfigRegistry-resolved values.
    """

    def __init__(
        self,
        provider: str,
        model: str,
        max_tokens: int | None,
        recursion_limit: int,
        settings: Settings,
    ) -> None:
        self.llm_provider = provider
        self.llm_model = model
        self.bedrock_model_id = model  # used when provider == "bedrock"
        self.llm_max_tokens = max_tokens
        self.agent_recursion_limit = recursion_limit
        # Pass through infrastructure fields that factories need
        self.openai_api_key = getattr(settings, "openai_api_key", None)
        self.openai_api_base = getattr(settings, "openai_api_base", None)
        self.openai_compatible_base_url = getattr(settings, "openai_compatible_base_url", None)
        self.openai_compatible_api_key = getattr(settings, "openai_compatible_api_key", None)
        self.aws_region = getattr(settings, "aws_region", "us-east-1")
        self.aws_access_key_id = getattr(settings, "aws_access_key_id", None)
        self.aws_secret_access_key = getattr(settings, "aws_secret_access_key", None)
        self.aws_session_token = getattr(settings, "aws_session_token", None)
        self.bedrock_role_arn = getattr(settings, "bedrock_role_arn", None)
        self.fallback_providers: list = []


class SessionAgentManager:
    """Manages DeepAgent services per session.

    Creates and caches agent services for each session.
    Tracks active streaming operations for abort functionality.
    """

    def __init__(self, settings: Settings):
        """Initialize the session manager.

        Args:
            settings: Application settings.
        """
        self.settings = settings
        self._services: dict[str, DeepAgentStreamingService] = {}
        self._project_paths: dict[str, str] = {}
        # Track active runtimes for abort functionality
        self._active_runtimes: dict[str, Any] = {}

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

    def get_service(self, session_id: str) -> DeepAgentStreamingService | None:
        """Get the agent service for a session.

        Args:
            session_id: Session identifier.

        Returns:
            DeepAgentStreamingService if found, None otherwise.
        """
        return self._services.get(session_id)

    def get_project_path(self, session_id: str) -> str | None:
        """Get the project path for a session.

        Args:
            session_id: Session identifier.

        Returns:
            Project path if found, None otherwise.
        """
        return self._project_paths.get(session_id)

    def register_runtime(self, session_id: str, runtime: Any) -> None:
        """Register an active runtime for abort tracking.

        Args:
            session_id: Session identifier.
            runtime: The runtime instance to track.
        """
        self._active_runtimes[session_id] = runtime
        logger.debug("Runtime registered for abort tracking", session_id=session_id)

    def unregister_runtime(self, session_id: str) -> None:
        """Unregister a runtime when streaming completes.

        Args:
            session_id: Session identifier.
        """
        self._active_runtimes.pop(session_id, None)
        logger.debug("Runtime unregistered", session_id=session_id)

    async def abort_session(self, session_id: str, thread_id: str | None = None) -> bool:
        """Abort the current operation for a session.

        Args:
            session_id: Session identifier.
            thread_id: Optional thread ID to abort.

        Returns:
            True if abort was signaled, False if no active runtime.
        """
        runtime = self._active_runtimes.get(session_id)
        if runtime:
            success = bool(await runtime.abort(thread_id))
            logger.info("Session abort signaled", session_id=session_id, success=success)
            return success
        else:
            logger.warning("No active runtime to abort", session_id=session_id)
            return False

    def unregister_session(self, session_id: str) -> None:
        """Unregister a session and clean up resources.

        Args:
            session_id: Session to unregister.
        """
        self._services.pop(session_id, None)
        self._project_paths.pop(session_id, None)
        self._active_runtimes.pop(session_id, None)

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
