"""Phase 4: Advanced Agent Capabilities.

Enhanced tool system, context management, and agent workflows
built on top of the deepagents foundation.
"""

from server.app.agent.cognition_agent import create_cognition_agent
from server.app.agent.sandbox_backend import CognitionLocalSandboxBackend
from server.app.agent.context import (
    ContextManager,
    ProjectIndex,
    FileRelevanceScorer,
)
from server.app.agent.definition import (
    AgentConfig,
    AgentDefinition,
    SubagentDefinition,
    load_agent_definition,
    create_default_agent_definition,
)
from server.app.agent.runtime import (
    AgentRuntime,
    DeepAgentRuntime,
    create_agent_runtime,
    AgentEvent,
    TokenEvent,
    ToolCallEvent,
    ToolResultEvent,
    StatusEvent,
    DoneEvent,
    ErrorEvent,
    AgentRuntimeType,
)

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
    "create_default_agent_definition",
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
