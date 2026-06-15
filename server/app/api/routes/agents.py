"""Agent management API routes."""

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from server.app.agent.definition import AgentDefinition
from server.app.api.dependencies import get_config_store, get_scope_dep
from server.app.api.models import (
    AgentConfigResponse,
    AgentCreate,
    AgentList,
    AgentResponse,
    AgentUpdate,
)
from server.app.api.scoping import SessionScope
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_to_response(agent: AgentDefinition) -> AgentResponse:
    return AgentResponse(
        name=agent.name,
        description=agent.description,
        mode=agent.mode,
        hidden=agent.hidden,
        native=agent.native,
        a2a_exposed=agent.a2a_exposed,
        provider=agent.config.provider,
        model=agent.config.model,
        temperature=agent.config.temperature,
        config=AgentConfigResponse(
            temperature=agent.config.temperature,
            max_tokens=agent.config.max_tokens,
            recursion_limit=agent.config.recursion_limit,
            tool_token_limit_before_evict=agent.config.tool_token_limit_before_evict,
            context_policy=agent.config.context_policy,
            excluded_tools=list(agent.config.excluded_tools),
            blocked_tools=list(agent.config.blocked_tools),
            provider=agent.config.provider,
            model=agent.config.model,
            timeout_seconds=agent.config.timeout_seconds,
        ),
        response_format=getattr(agent, "response_format", None),
        interrupt_on={
            name: config.model_dump(exclude_none=True)
            if hasattr(config, "model_dump")
            else dict(config)
            for name, config in (agent.interrupt_on or {}).items()
        },
        permissions=[
            p.model_dump() if hasattr(p, "model_dump") else dict(p)
            for p in (agent.permissions or [])
        ],
        tools=agent.tools or [],
        skills=agent.skills or [],
        system_prompt=agent.system_prompt,
        subagents=[
            {
                "name": s.name,
                "description": s.description,
                "system_prompt": s.system_prompt,
                "tools": s.tools,
                "permissions": [
                    p.model_dump() if hasattr(p, "model_dump") else dict(p)
                    for p in (s.permissions or [])
                ],
            }
            for s in agent.subagents or []
        ],
        async_subagents=list(agent.async_subagents or []),
    )


@router.get("", response_model=AgentList)
async def list_agents(
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentList:
    """List all available agents (excluding hidden ones)."""
    agents = await config_store.list_agent_definitions(
        include_hidden=False,
        scope=scope.get_all() or None,
    )
    return AgentList(agents=[_agent_to_response(a) for a in agents])


@router.get("/{name}", response_model=AgentResponse)
async def get_agent(
    name: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentResponse:
    """Get a specific agent by name."""
    agent = await config_store.get_agent_definition(name, scope.get_all() or None)
    if agent is None or agent.hidden:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    return _agent_to_response(agent)


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    body: AgentCreate,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentResponse:
    """Create or replace an agent definition in the ConfigStore.

    Built-in (native) agents cannot be replaced.
    """
    existing = await config_store.get_agent_definition(body.name, scope.get_all() or None)
    if existing and existing.native:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot overwrite built-in agent '{body.name}'",
        )

    try:
        definition_data: dict[str, Any] = {
            "name": body.name,
            "system_prompt": body.system_prompt,
            "description": body.description,
            "mode": body.mode,
            "hidden": body.hidden,
            "a2a_exposed": body.a2a_exposed,
            "native": False,
            "tools": body.tools,
            "skills": body.skills,
            "memory": body.memory,
            "subagents": body.subagents,
            "async_subagents": [
                spec.model_dump(exclude_none=True) for spec in body.async_subagents
            ],
            "permissions": [p.model_dump() for p in body.permissions],
            "response_format": body.response_format,
            "middleware": body.middleware,
            "config": {
                "model": body.model,
                "temperature": body.temperature,
                "max_tokens": body.max_tokens,
                "recursion_limit": body.recursion_limit,
                "tool_token_limit_before_evict": body.tool_token_limit_before_evict,
                "context_policy": (
                    body.context_policy.model_dump(exclude_none=True)
                    if body.context_policy
                    else None
                ),
                "excluded_tools": body.excluded_tools,
                "blocked_tools": body.blocked_tools,
                "provider": body.provider,
                "timeout_seconds": body.timeout_seconds,
            },
        }
        if "interrupt_on" in body.model_fields_set:
            definition_data["interrupt_on"] = {
                name: config.model_dump(exclude_none=True)
                for name, config in body.interrupt_on.items()
            }
        effective_scope = scope.get_all() or body.scope
        await config_store.upsert_agent(body.name, effective_scope, definition_data, "api")

        agent_def = await config_store.get_agent_definition(body.name, effective_scope or None)
        if agent_def is None:
            raise HTTPException(status_code=500, detail="Agent not found after creation")
        return _agent_to_response(agent_def)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/{name}", response_model=AgentResponse)
async def replace_agent(
    name: str,
    body: AgentCreate,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentResponse:
    """Replace an agent definition (full update)."""
    existing = await config_store.get_agent_definition(name, scope.get_all() or None)
    if existing and existing.native:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot overwrite built-in agent '{name}'",
        )

    body.name = name
    return await create_agent(body, scope=scope, config_store=config_store)


@router.patch("/{name}", response_model=AgentResponse)
async def update_agent(
    name: str,
    body: AgentUpdate,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentResponse:
    """Partially update an agent definition."""
    scope_dict = scope.get_all() or None
    existing = await config_store.get_agent_definition(name, scope_dict)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    if existing.native:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot modify built-in agent '{name}'",
        )

    try:
        result = await config_store.get_agent_raw_with_scope(name, scope_dict)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")
        data, agent_scope = result

        updates = body.model_dump(exclude_none=True)
        config_fields = {
            "model",
            "temperature",
            "max_tokens",
            "recursion_limit",
            "tool_token_limit_before_evict",
            "context_policy",
            "excluded_tools",
            "blocked_tools",
            "provider",
            "timeout_seconds",
        }
        config_updates = {key: updates.pop(key) for key in list(updates) if key in config_fields}
        if config_updates:
            config = data.get("config", {})
            config.update(config_updates)
            data["config"] = config
        if "response_format" in updates:
            data["response_format"] = updates.pop("response_format")
        data.update(updates)

        await config_store.upsert_agent(name, agent_scope, data, "api")

        agent_def = await config_store.get_agent_definition(name, agent_scope or None)
        if agent_def is None:
            raise HTTPException(status_code=500, detail="Agent not found after update")
        return _agent_to_response(agent_def)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{name}", status_code=204)
async def delete_agent(
    name: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> None:
    """Delete a user-defined agent definition."""
    scope_dict = scope.get_all() or None
    existing = await config_store.get_agent_definition(name, scope_dict)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    if existing.native:
        raise HTTPException(
            status_code=409,
            detail=f"Cannot delete built-in agent '{name}'",
        )

    try:
        await config_store.delete_agent(name, scope_dict)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
