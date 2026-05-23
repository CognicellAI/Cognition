"""API routes package."""

from server.app.api.routes import (
    agents,
    capabilities,
    config,
    mcp_servers,
    messages,
    models,
    sessions,
    skills,
    tools,
)

__all__ = [
    "agents",
    "capabilities",
    "config",
    "mcp_servers",
    "messages",
    "models",
    "sessions",
    "skills",
    "tools",
]
