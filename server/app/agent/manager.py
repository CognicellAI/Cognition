"""Agent manager for in-process agents."""

from __future__ import annotations

from typing import Any

import structlog
from server.app.agent.runtime import InProcessAgent
from server.app.settings import Settings

logger = structlog.get_logger()


class AgentManager:
    """Manage in-process agents for sessions."""

    def __init__(self, settings: Settings):
        """Initialize agent manager.

        Args:
            settings: Server settings
        """
        self.settings = settings
        self.agents: dict[str, InProcessAgent] = {}

    def create_agent(self, session_id: str) -> InProcessAgent:
        """Create a new agent for a session.

        Args:
            session_id: Session ID

        Returns:
            New InProcessAgent instance
        """
        logger.info("Creating agent", session_id=session_id)
        agent = InProcessAgent(self.settings, session_id)
        self.agents[session_id] = agent
        return agent

    def get_agent(self, session_id: str) -> InProcessAgent | None:
        """Get existing agent for session.

        Args:
            session_id: Session ID

        Returns:
            Agent or None if not found
        """
        return self.agents.get(session_id)

    def delete_agent(self, session_id: str) -> None:
        """Delete agent for session.

        Args:
            session_id: Session ID
        """
        if session_id in self.agents:
            logger.info("Deleting agent", session_id=session_id)
            del self.agents[session_id]

    async def process_message(
        self,
        session_id: str,
        message: str,
        context: dict[str, Any] | None = None,
    ) -> str:
        """Process message with agent.

        Args:
            session_id: Session ID
            message: User message
            context: Optional context

        Returns:
            Agent response
        """
        agent = self.get_agent(session_id)
        if not agent:
            raise ValueError(f"No agent found for session {session_id}")

        return await agent.process_message(message, context)
