"""Per-agent A2A Agent Card generation from Cognition agent definitions.

Each A2A-exposed agent gets its own AgentCard with a dedicated JSON-RPC
endpoint at /a2a/{agent_name}. Builders control exposure via the
a2a_exposed field on AgentDefinition. No agent is exposed by default.
"""

from __future__ import annotations

import structlog
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from server.app.agent.definition import AgentDefinition

logger = structlog.get_logger(__name__)


def build_agent_card_for_agent(
    agent: AgentDefinition,
    base_url: str,
    version: str,
) -> AgentCard:
    """Build an A2A AgentCard for a single Cognition agent.

    The card's supportedInterfaces URL points to /a2a/{agent_name}.
    The card name includes the agent name for client-side identification.

    The card does NOT expose: system prompt, tool list, skill contents,
    scope values, secrets, or subagent details.
    """
    skill = AgentSkill(
        id=agent.name,
        name=agent.name,
        description=agent.description or f"Cognition agent: {agent.name}",
        tags=["cognition", agent.mode],
        input_modes=["text/plain"],
        output_modes=["text/plain", "application/json"],
        examples=[],
    )

    card = AgentCard(
        name=f"Cognition ({agent.name})",
        description=agent.description or f"Cognition agent: {agent.name}",
        version=version,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=f"{base_url}/a2a/{agent.name}",
            )
        ],
        skills=[skill],
    )

    logger.info(
        "A2A Agent Card built",
        agent_name=agent.name,
        endpoint=f"/a2a/{agent.name}",
    )
    return card
