"""Per-agent A2A Agent Card generation from Cognition agent definitions.

Each A2A-exposed agent gets its own AgentCard. Builders can advertise an
externally routed JSON-RPC endpoint with ``a2a_public_interface_url``; otherwise
Cognition falls back to its dedicated ``/a2a/{agent_name}`` route. Builders
control exposure via the ``a2a_exposed`` field. No agent is exposed by default.
"""

from __future__ import annotations

import structlog
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill

from server.app.agent.definition import AgentDefinition
from server.app.protocols.a2a.security import A2ACardSecurity

logger = structlog.get_logger(__name__)


def build_agent_card_for_agent(
    agent: AgentDefinition,
    base_url: str,
    version: str,
    security: A2ACardSecurity | None = None,
) -> AgentCard:
    """Build an A2A AgentCard for a single Cognition agent.

    The card's supportedInterfaces URL uses the configured public interface URL
    when present and otherwise points to /a2a/{agent_name}.
    The card name uses the public display name when configured and otherwise
    falls back to the runtime agent name.

    The card does NOT expose: system prompt, tool list, skill contents,
    scope values, secrets, or subagent details.
    """
    public_name = agent.display_name or agent.name
    has_public_name = agent.display_name is not None
    card_description = agent.description or (
        f"Agent: {public_name}" if has_public_name else f"Cognition agent: {public_name}"
    )
    skill_description = agent.description or (
        f"Primary capability for {public_name}"
        if has_public_name
        else f"Cognition agent: {public_name}"
    )
    interface_url = agent.a2a_public_interface_url or f"{base_url}/a2a/{agent.name}"
    security = security or A2ACardSecurity(schemes={}, requirements=())

    skill = AgentSkill(
        id="primary" if has_public_name else agent.name,
        name=public_name,
        description=skill_description,
        tags=["primary"] if has_public_name else ["cognition", agent.mode],
        input_modes=["text/plain"],
        output_modes=["text/plain", "application/json"],
        examples=[],
    )

    card = AgentCard(
        name=public_name,
        description=card_description,
        version=version,
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain", "application/json"],
        # Explicitly publish every capability flag.  The protocol uses
        # presence-sensitive fields, so omitting ``pushNotifications`` or
        # ``extendedAgentCard`` would leave clients unable to distinguish an
        # unsupported capability from an unknown one.
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extended_agent_card=False,
        ),
        supported_interfaces=[
            AgentInterface(
                protocol_binding="JSONRPC",
                url=interface_url,
                protocol_version="1.0",
            )
        ],
        security_schemes=security.schemes,
        security_requirements=list(security.requirements),
        skills=[skill],
    )

    logger.info(
        "A2A Agent Card built",
        agent_name=agent.name,
        interface_url=interface_url,
    )
    return card
