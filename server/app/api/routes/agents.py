"""Agent management API routes."""

from fastapi import APIRouter, HTTPException

from server.app.agent.agent_definition_registry import get_agent_definition_registry
from server.app.api.models import AgentList, AgentResponse

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("", response_model=AgentList)
async def list_agents() -> AgentList:
    """List all available agents (excluding hidden ones)."""
    registry = get_agent_definition_registry()
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not initialized")

    agents = registry.list(include_hidden=False)
    return AgentList(
        agents=[
            AgentResponse(
                name=a.name,
                description=a.description,
                mode=a.mode,
                hidden=a.hidden,
                native=a.native,
                model=a.config.model,
                temperature=a.config.temperature,
            )
            for a in agents
        ]
    )


@router.get("/{name}", response_model=AgentResponse)
async def get_agent(name: str) -> AgentResponse:
    """Get a specific agent by name."""
    registry = get_agent_definition_registry()
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not initialized")

    agent = registry.get(name)
    if agent is None or agent.hidden:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")

    return AgentResponse(
        name=agent.name,
        description=agent.description,
        mode=agent.mode,
        hidden=agent.hidden,
        native=agent.native,
        model=agent.config.model,
        temperature=agent.config.temperature,
    )
