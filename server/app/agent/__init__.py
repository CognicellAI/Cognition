"""Advanced agent capabilities package."""

from __future__ import annotations

__all__ = [
    "create_cognition_agent",
    "CognitionAgentParams",
    "CognitionAgentResult",
    "CognitionLocalSandboxBackend",
    "BrowserTool",
    "InspectPackageTool",
    "SearchTool",
    "AgentConfig",
    "AgentDefinition",
    "SubagentDefinition",
    "load_agent_definition",
    "load_agent_definition_from_markdown",
    "create_default_agent_definition",
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


def __getattr__(name: str):
    if name in {"create_cognition_agent", "CognitionAgentParams", "CognitionAgentResult"}:
        from server.app.agent.cognition_agent import (
            CognitionAgentParams,
            CognitionAgentResult,
            create_cognition_agent,
        )

        return {
            "create_cognition_agent": create_cognition_agent,
            "CognitionAgentParams": CognitionAgentParams,
            "CognitionAgentResult": CognitionAgentResult,
        }[name]

    if name in {
        "AgentConfig",
        "AgentDefinition",
        "SubagentDefinition",
        "create_default_agent_definition",
        "load_agent_definition",
        "load_agent_definition_from_markdown",
    }:
        from server.app.agent.definition import (
            AgentConfig,
            AgentDefinition,
            SubagentDefinition,
            create_default_agent_definition,
            load_agent_definition,
            load_agent_definition_from_markdown,
        )

        return {
            "AgentConfig": AgentConfig,
            "AgentDefinition": AgentDefinition,
            "SubagentDefinition": SubagentDefinition,
            "create_default_agent_definition": create_default_agent_definition,
            "load_agent_definition": load_agent_definition,
            "load_agent_definition_from_markdown": load_agent_definition_from_markdown,
        }[name]

    if name in {
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
    }:
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

        return {
            "AgentRuntime": AgentRuntime,
            "DeepAgentRuntime": DeepAgentRuntime,
            "create_agent_runtime": create_agent_runtime,
            "AgentEvent": AgentEvent,
            "TokenEvent": TokenEvent,
            "ToolCallEvent": ToolCallEvent,
            "ToolResultEvent": ToolResultEvent,
            "StatusEvent": StatusEvent,
            "DoneEvent": DoneEvent,
            "ErrorEvent": ErrorEvent,
            "AgentRuntimeType": AgentRuntimeType,
        }[name]

    if name == "CognitionLocalSandboxBackend":
        from server.app.agent.sandbox_backend import CognitionLocalSandboxBackend

        return CognitionLocalSandboxBackend

    if name in {"BrowserTool", "InspectPackageTool", "SearchTool"}:
        from server.app.agent.tools import BrowserTool, InspectPackageTool, SearchTool

        return {
            "BrowserTool": BrowserTool,
            "InspectPackageTool": InspectPackageTool,
            "SearchTool": SearchTool,
        }[name]

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
