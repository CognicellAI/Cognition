"""Phase 4: Advanced Agent Capabilities.

Enhanced tool system, context management, and agent workflows
built on top of the deepagents foundation.
"""

from server.app.agent.agent_definition_registry import (
    AgentDefinitionRegistry,
    get_agent_definition_registry,
    initialize_agent_definition_registry,
)
from server.app.agent.cognition_agent import create_cognition_agent
from server.app.agent.context import (
    ContextManager,
    FileRelevanceScorer,
    ProjectIndex,
)
from server.app.agent.definition import (
    AgentConfig,
    AgentDefinition,
    SubagentDefinition,
    create_default_agent_definition,
    load_agent_definition,
    load_agent_definition_from_markdown,
)
from server.app.agent.runtime import (
    AgentEvent,
    AgentRuntime,
    AgentRuntimeType,
    DeepAgentRuntime,
    DoneEvent,
    ErrorEvent,
    StatusEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    create_agent_runtime,
)
from server.app.agent.sandbox_backend import CognitionLocalSandboxBackend

__all__ = [
    # Agent creation
    "create_cognition_agent",
    "CognitionLocalSandboxBackend",
    "ContextManager",
    "ProjectIndex",
    "FileRelevanceScorer",
    # Definition
    "AgentConfig",
    "AgentDefinition",
    "SubagentDefinition",
    "load_agent_definition",
    "load_agent_definition_from_markdown",
    "create_default_agent_definition",
    # Registry
    "AgentDefinitionRegistry",
    "get_agent_definition_registry",
    "initialize_agent_definition_registry",
    # Runtime
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
