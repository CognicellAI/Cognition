"""Agent management API routes."""

from typing import Any

from fastapi import APIRouter, HTTPException

from server.app.agent.agent_definition_registry import get_agent_definition_registry
from server.app.api.models import (
    AgentCreate,
    AgentList,
    AgentResponse,
    AgentUpdate,
)

router = APIRouter(prefix="/agents", tags=["agents"])


def _registry_or_503() -> Any:
    registry = get_agent_definition_registry()
    if registry is None:
        raise HTTPException(status_code=503, detail="Agent registry not initialized")
    return registry


def _agent_to_response(agent: Any) -> AgentResponse:
    return AgentResponse(
        name=agent.name,
        description=agent.description,
        mode=agent.mode,
        hidden=agent.hidden,
        native=agent.native,
        model=agent.config.model,
        temperature=agent.config.temperature,
        tools=agent.tools or [],
        skills=agent.skills or [],
        system_prompt=agent.system_prompt[:500] + "..."
        if agent.system_prompt and len(agent.system_prompt) > 500
        else agent.system_prompt,
    )


@router.get("", response_model=AgentList)
async def list_agents() -> AgentList:
    """List all available agents (excluding hidden ones)."""
    registry = _registry_or_503()
    agents = registry.get_all(include_hidden=False)
    return AgentList(agents=[_agent_to_response(a) for a in agents])


@router.get("/{name}", response_model=AgentResponse)
async def get_agent(name: str) -> AgentResponse:
    """Get a specific agent by name."""
    registry = _registry_or_503()
    agent = registry.get(name)
    if agent is None or agent.hidden:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return _agent_to_response(agent)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(body: AgentCreate) -> AgentResponse:
    """Create or replace an agent definition in the ConfigRegistry.

    Built-in (native) agents cannot be replaced.
    """
    registry = _registry_or_503()

    existing = registry.get(body.name)
    if existing and existing.native:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot overwrite built-in agent '{body.name}'",
        )

    try:
        from server.app.agent.definition import AgentDefinition
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        definition_data: dict[str, Any] = {
            "name": body.name,
            "system_prompt": body.system_prompt,
            "description": body.description,
            "mode": body.mode,
            "hidden": body.hidden,
            "native": False,
            "tools": body.tools,
            "skills": body.skills,
            "memory": body.memory,
            "subagents": [],
            "interrupt_on": body.interrupt_on,
            "middleware": [],
            "config": {
                "model": body.model,
                "temperature": body.temperature,
            },
        }
        await reg.upsert_agent(body.name, body.scope, definition_data, "api")

        # Reload into in-memory registry
        agent_def = AgentDefinition.model_validate(definition_data)
        agent_def.native = False
        registry._agents[body.name] = agent_def

        return _agent_to_response(agent_def)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/{name}", response_model=AgentResponse)
async def replace_agent(name: str, body: AgentCreate) -> AgentResponse:
    """Replace an agent definition (full update)."""
    registry = _registry_or_503()

    existing = registry.get(name)
    if existing and existing.native:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot overwrite built-in agent '{name}'",
        )

    body.name = name  # Ensure name matches path param
    return await create_agent(body)


@router.patch("/{name}", response_model=AgentResponse)
async def update_agent(name: str, body: AgentUpdate) -> AgentResponse:
    """Partially update an agent definition."""
    registry = _registry_or_503()

    existing = registry.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    if existing.native:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot modify built-in agent '{name}'",
        )

    try:
        from server.app.agent.definition import AgentDefinition
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        data = await reg.get_agent_raw(name, None)
        if data is None:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")

        # Apply partial update
        updates = body.model_dump(exclude_none=True)
        if "model" in updates or "temperature" in updates:
            config = data.get("config", {})
            if "model" in updates:
                config["model"] = updates.pop("model")
            if "temperature" in updates:
                config["temperature"] = updates.pop("temperature")
            data["config"] = config
        data.update(updates)

        scope: dict[str, Any] = data.get("scope", {})
        await reg.upsert_agent(name, scope, data, "api")

        agent_def = AgentDefinition.model_validate(data)
        agent_def.native = False
        registry._agents[name] = agent_def

        return _agent_to_response(agent_def)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{name}", status_code=204)
async def delete_agent(name: str) -> None:
    """Delete a user-defined agent definition."""
    registry = _registry_or_503()

    existing = registry.get(name)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    if existing.native:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete built-in agent '{name}'",
        )

    try:
        from server.app.storage.config_registry import get_config_registry

        reg = get_config_registry()
        await reg.delete_agent(name, None)
        registry._agents.pop(name, None)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
