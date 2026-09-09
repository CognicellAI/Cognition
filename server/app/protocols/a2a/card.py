"""Per-agent A2A Agent Card generation from Cognition agent definitions.

Each A2A-exposed agent gets its own AgentCard. Builders configure exposure,
the public JSON-RPC endpoint, MIME modes, and public skills under ``agent.a2a``.
No agent is exposed by default.
"""

from __future__ import annotations

import structlog
from a2a.types import AgentCapabilities, AgentCard, AgentExtension, AgentInterface, AgentSkill

from server.app.agent.definition import AgentDefinition
from server.app.protocols.a2a.a2ui import (
    A2UI_EXTENSION_URI,
    A2UI_MEDIA_TYPE,
    build_agent_card_extension_params,
)
from server.app.protocols.a2a.security import A2ACardSecurity

logger = structlog.get_logger(__name__)


def _with_a2ui_mode(modes: list[str], agent: AgentDefinition) -> list[str]:
    """Append the A2UI media type for A2UI-enabled agents."""
    if agent.a2a.a2ui is None or A2UI_MEDIA_TYPE in modes:
        return modes
    return [*modes, A2UI_MEDIA_TYPE]


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
    interface_url = agent.a2a.public_interface_url or f"{base_url}/a2a/{agent.name}"
    security = security or A2ACardSecurity(schemes={}, requirements=())
    default_input_modes = _with_a2ui_mode(agent.a2a.default_input_modes, agent)
    default_output_modes = _with_a2ui_mode(agent.a2a.default_output_modes, agent)

    if agent.a2a.skills:
        skills = [
            AgentSkill(
                id=skill.id,
                name=skill.name,
                description=skill.description,
                tags=skill.tags,
                examples=skill.examples,
                input_modes=_with_a2ui_mode(skill.input_modes, agent)
                if skill.input_modes
                else [],
                output_modes=_with_a2ui_mode(skill.output_modes, agent)
                if skill.output_modes
                else [],
            )
            for skill in agent.a2a.skills
        ]
    else:
        skills = [
            AgentSkill(
                id="primary" if has_public_name else agent.name,
                name=public_name,
                description=skill_description,
                tags=["primary"] if has_public_name else ["cognition", agent.mode],
                input_modes=default_input_modes,
                output_modes=default_output_modes,
                examples=[],
            )
        ]

    extensions = []
    if agent.a2a.a2ui is not None:
        extensions.append(
            AgentExtension(
                uri=A2UI_EXTENSION_URI,
                description="Generates interactive UI using A2UI v1.0.",
                required=False,
                params=build_agent_card_extension_params(),
            )
        )

    card = AgentCard(
        name=public_name,
        description=card_description,
        version=version,
        default_input_modes=default_input_modes,
        default_output_modes=default_output_modes,
        # Explicitly publish every capability flag.  The protocol uses
        # presence-sensitive fields, so omitting ``pushNotifications`` or
        # ``extendedAgentCard`` would leave clients unable to distinguish an
        # unsupported capability from an unknown one.
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extensions=extensions,
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
        skills=skills,
    )

    logger.info(
        "A2A Agent Card built",
        agent_name=agent.name,
        interface_url=interface_url,
    )
    return card
