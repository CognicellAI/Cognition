"""Phase 4: Advanced Agent Capabilities.

Enhanced tool system, context management, and agent workflows
built on top of the deepagents foundation.
"""

from server.app.agent.cognition_agent import create_cognition_agent
from server.app.agent.sandbox_backend import CognitionSandboxBackend
from server.app.agent.tools import (
    ToolRegistry,
    ToolCategory,
    register_enhanced_tools,
)
from server.app.agent.context import (
    ContextManager,
    ProjectIndex,
    FileRelevanceScorer,
)
from server.app.agent.workflows import (
    WorkflowOrchestrator,
    TaskPlanner,
    ApprovalManager,
    ChangeTracker,
)
from server.app.agent.output import (
    OutputFormatter,
    DiffFormatter,
    SyntaxHighlighter,
)

__all__ = [
    "create_cognition_agent",
    "CognitionSandboxBackend",
    "ToolRegistry",
    "ToolCategory",
    "register_enhanced_tools",
    "ContextManager",
    "ProjectIndex",
    "FileRelevanceScorer",
    "WorkflowOrchestrator",
    "TaskPlanner",
    "ApprovalManager",
    "ChangeTracker",
    "OutputFormatter",
    "DiffFormatter",
    "SyntaxHighlighter",
]
