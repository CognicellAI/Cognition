"""Agent Card generation from Cognition agent definitions.

Derives A2A AgentCard, AgentSkill, and AgentCapabilities from the agents
registered in Cognition's ConfigRegistry. The card is auto-generated and
does not require manual maintenance.
"""

from __future__ import annotations

import structlog
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from server.app.agent.definition import AgentDefinition

logger = structlog.get_logger(__name__)


def build_agent_card(
    agents: list[AgentDefinition],
    base_url: str,
    version: str,
) -> AgentCard:
    """Build an A2A AgentCard from configured Cognition agents.

    Only primary, non-hidden agents become A2A skills. Subagent-only
    and hidden agents are excluded from the public card.
    """
    skills: list[AgentSkill] = []
    for agent in agents:
        if agent.mode == "subagent" or agent.hidden:
            continue
        skill = AgentSkill(
            id=agent.name,
            name=agent.name,
            description=agent.description or f"Cognition agent: {agent.name}",
            tags=["cognition", agent.mode],
            input_modes=["text/plain"],
            output_modes=["text/plain", "application/json"],
            examples=[],
        )
        skills.append(skill)

    if not skills:
        skills.append(
            AgentSkill(
                id="default",
                name="default",
                description="Cognition default coding assistant",
                tags=["cognition", "primary"],
                input_modes=["text/plain"],
                output_modes=["text/plain", "application/json"],
                examples=[],
            )
        )

    card = AgentCard(
        name="Cognition",
        description=(
            "AI-powered coding assistant with tools, sandbox execution, "
            "and long-running task support"
        ),
        version=version,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        capabilities=AgentCapabilities(streaming=True),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=f"{base_url}/a2a",
            )
        ],
        skills=skills,
    )

    logger.info(
        "A2A Agent Card built",
        skill_count=len(skills),
        skills=[s.id for s in skills],
    )
    return card
