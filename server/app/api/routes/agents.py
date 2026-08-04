"""Agent management API routes."""

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Response

from server.app.agent.definition import A2AConfig, AgentDefinition
from server.app.api.dependencies import (
    get_config_store,
    get_mcp_readiness_repository,
    get_scope_dep,
)
from server.app.api.models import (
    AgentConfigResponse,
    AgentCreate,
    AgentList,
    AgentResponse,
    AgentUpdate,
    McpReadinessResponse,
    McpServerReadinessResponse,
)
from server.app.api.scoping import SessionScope
from server.app.observability import SCOPE_ACCESS_DENIED_TOTAL
from server.app.storage.common import canonical_json_digest
from server.app.storage.config_models import AgentConfigRecord
from server.app.storage.config_registry import ConfigRevisionConflictError
from server.app.storage.config_store import ConfigStore
from server.app.storage.mcp_readiness import McpReadinessRepository

router = APIRouter(prefix="/agents", tags=["agents"])


def _agent_to_response(
    agent: AgentDefinition,
    record: AgentConfigRecord | None = None,
) -> AgentResponse:
    definition = agent.model_dump(mode="json")
    return AgentResponse(
        name=agent.name,
        revision=record.revision if record else 1,
        definition_digest=(
            record.definition_digest if record else canonical_json_digest(definition)
        ),
        display_name=agent.display_name,
        description=agent.description,
        mode=agent.mode,
        hidden=agent.hidden,
        native=agent.native,
        a2a=agent.a2a,
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
            sandbox_profile=agent.config.sandbox_profile,
            sandbox_execution_role_arn=agent.config.sandbox_execution_role_arn,
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
        skills=agent.skills or [],
        mcp=agent.mcp,
        system_prompt=agent.system_prompt,
        subagents=[
            {
                "name": s.name,
                "description": s.description,
                "system_prompt": s.system_prompt,
                    "permissions": [
                    p.model_dump() if hasattr(p, "model_dump") else dict(p)
                    for p in (s.permissions or [])
                ],
            }
            for s in agent.subagents or []
        ],
        async_subagents=list(agent.async_subagents or []),
    )


def _etag(revision: int, digest: str) -> str:
    return f'"{revision}-{digest}"'


def _if_match_revision(value: str | None) -> int | None:
    if value is None:
        return None
    token = value.strip()
    if token.startswith("W/"):
        token = token[2:]
    token = token.strip('"')
    try:
        return int(token.split("-", 1)[0])
    except (TypeError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid If-Match Agent ETag") from exc


def _trusted_agent_scope(
    body_scope: dict[str, str],
    scope: SessionScope,
    response: Response,
) -> dict[str, str]:
    trusted_scope = scope.get_all()
    if body_scope and body_scope != trusted_scope:
        SCOPE_ACCESS_DENIED_TOTAL.labels(
            resource_type="agent",
            operation="scope_conflict",
        ).inc()
        raise HTTPException(
            status_code=400,
            detail="Request-body scope conflicts with authoritative scope headers",
        )
    if body_scope:
        response.headers["Warning"] = (
            '299 Cognition "Agent request-body scope is deprecated; use scope headers"'
        )
    return trusted_scope


@router.get("", response_model=AgentList)
async def list_agents(
    limit: int = 100,
    offset: int = 0,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentList:
    """List all available agents (excluding hidden ones)."""
    safe_limit = max(1, min(limit, 500))
    safe_offset = max(0, offset)
    agents = await config_store.list_agent_definitions(
        include_hidden=False,
        scope=scope.get_all() or None,
        limit=safe_limit + 1,
        offset=safe_offset,
    )
    records = {
        agent.name: await config_store.get_agent_record(
            agent.name,
            scope.get_all() or None,
        )
        for agent in agents
    }
    has_more = len(agents) > safe_limit
    page = agents[:safe_limit]
    return AgentList(
        agents=[_agent_to_response(agent, records[agent.name]) for agent in page],
        has_more=has_more,
        next_offset=safe_offset + safe_limit if has_more else None,
    )


@router.get("/{name}", response_model=AgentResponse)
async def get_agent(
    name: str,
    response: Response,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentResponse:
    """Get a specific agent by name."""
    agent = await config_store.get_agent_definition(name, scope.get_all() or None)
    if agent is None or agent.hidden:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    record = await config_store.get_agent_record(name, scope.get_all() or None)
    result = _agent_to_response(agent, record)
    response.headers["ETag"] = _etag(result.revision, result.definition_digest)
    return result


@router.get("/{name}/mcp/readiness", response_model=McpReadinessResponse)
async def get_agent_mcp_readiness(
    name: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
    repository: McpReadinessRepository = Depends(  # noqa: B008
        get_mcp_readiness_repository
    ),
) -> McpReadinessResponse:
    """Return scoped runtime observations, never live authorization truth."""
    effective_scope = scope.get_all()
    agent = await config_store.get_agent_definition(name, effective_scope or None)
    if agent is None or agent.hidden:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    record = await config_store.get_agent_record(name, effective_scope or None)
    revision = record.revision if record else 1
    observations = {
        item.server_alias: item
        for item in await repository.list_for_agent(
            agent_name=name,
            agent_revision=revision,
            effective_scope=effective_scope,
        )
    }
    servers: list[McpServerReadinessResponse] = []
    for alias, config in sorted(agent.mcp.servers.items()):
        observation = observations.get(alias)
        if observation is None:
            servers.append(
                McpServerReadinessResponse(
                    server_alias=alias,
                    required=config.required,
                    status="unknown",
                    failure_category="not_observed",
                )
            )
            continue
        status = observation.public_status()
        servers.append(
            McpServerReadinessResponse(
                server_alias=alias,
                required=config.required,
                status=status,
                observed_at=observation.observed_at,
                fresh_until=observation.fresh_until,
                tool_count=observation.tool_count,
                schema_digest=observation.schema_digest,
                failure_category=(
                    "observation_stale"
                    if status == "unknown"
                    else observation.failure_category
                ),
            )
        )
    return McpReadinessResponse(
        agent_name=name,
        agent_revision=revision,
        servers=servers,
    )


@router.post("", response_model=AgentResponse, status_code=201)
async def create_agent(
    body: AgentCreate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentResponse:
    """Create or replace an agent definition in the ConfigStore."""

    try:
        definition_data: dict[str, Any] = {
            "name": body.name,
            "display_name": body.display_name,
            "system_prompt": body.system_prompt,
            "description": body.description,
            "mode": body.mode,
            "hidden": body.hidden,
            "a2a": body.a2a.model_dump(mode="json"),
            "native": False,
            "skills": [skill.model_dump(mode="json") for skill in body.skills],
            "memory": body.memory,
            "mcp": body.mcp.model_dump(mode="json"),
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
                "sandbox_profile": body.sandbox_profile,
                "sandbox_execution_role_arn": body.sandbox_execution_role_arn,
            },
        }
        if "interrupt_on" in body.model_fields_set:
            definition_data["interrupt_on"] = {
                name: config.model_dump(exclude_none=True)
                for name, config in body.interrupt_on.items()
            }
        effective_scope = _trusted_agent_scope(body.scope, scope, response)
        record = await config_store.upsert_agent(
            body.name,
            effective_scope,
            definition_data,
            "api",
            expected_revision=_if_match_revision(if_match),
            create_only=if_none_match == "*",
        )

        agent_def = await config_store.get_agent_definition(body.name, effective_scope or None)
        if agent_def is None:
            raise HTTPException(status_code=500, detail="Agent not found after creation")
        result = _agent_to_response(agent_def, record)
        response.headers["ETag"] = _etag(result.revision, result.definition_digest)
        return result
    except ConfigRevisionConflictError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.put("/{name}", response_model=AgentResponse)
async def replace_agent(
    name: str,
    body: AgentCreate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    if_none_match: str | None = Header(default=None, alias="If-None-Match"),
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentResponse:
    """Replace an agent definition (full update)."""
    body.name = name
    return await create_agent(
        body,
        response=response,
        if_match=if_match,
        if_none_match=if_none_match,
        scope=scope,
        config_store=config_store,
    )


@router.patch("/{name}", response_model=AgentResponse)
async def update_agent(
    name: str,
    body: AgentUpdate,
    response: Response,
    if_match: str | None = Header(default=None, alias="If-Match"),
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> AgentResponse:
    """Partially update an agent definition."""
    scope_dict = scope.get_all() or None
    existing = await config_store.get_agent_definition(name, scope_dict)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    try:
        result = await config_store.get_agent_raw_with_scope(name, scope_dict)
        if result is None:
            raise HTTPException(status_code=404, detail=f"Agent '{name}' not found in registry")
        data, agent_scope = result

        updates = body.model_dump(exclude_none=True)
        if "display_name" in body.model_fields_set:
            updates["display_name"] = body.display_name
        if "a2a" in body.model_fields_set:
            updates["a2a"] = (body.a2a or A2AConfig()).model_dump(mode="json")
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
            "sandbox_profile",
            "sandbox_execution_role_arn",
        }
        config_updates = {key: updates.pop(key) for key in list(updates) if key in config_fields}
        if config_updates:
            config = data.get("config", {})
            config.update(config_updates)
            data["config"] = config
        if "response_format" in updates:
            data["response_format"] = updates.pop("response_format")
        if "mcp" in updates:
            mcp_value = updates.pop("mcp")
            data["mcp"] = (
                mcp_value.model_dump(mode="json")
                if hasattr(mcp_value, "model_dump")
                else mcp_value
            )
        data.update(updates)

        record = await config_store.upsert_agent(
            name,
            agent_scope,
            data,
            "api",
            expected_revision=_if_match_revision(if_match),
        )

        agent_def = await config_store.get_agent_definition(name, agent_scope or None)
        if agent_def is None:
            raise HTTPException(status_code=500, detail="Agent not found after update")
        result_response = _agent_to_response(agent_def, record)
        response.headers["ETag"] = _etag(
            result_response.revision,
            result_response.definition_digest,
        )
        return result_response
    except ConfigRevisionConflictError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.delete("/{name}", status_code=204)
async def delete_agent(
    name: str,
    if_match: str | None = Header(default=None, alias="If-Match"),
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> None:
    """Delete an agent definition."""
    scope_dict = scope.get_all() or None
    existing = await config_store.get_agent_record(name, scope_dict)
    if existing is None:
        raise HTTPException(status_code=404, detail=f"Agent '{name}' not found")
    try:
        await config_store.delete_agent(
            name,
            scope_dict,
            expected_revision=_if_match_revision(if_match),
        )
    except ConfigRevisionConflictError as exc:
        raise HTTPException(status_code=412, detail=str(exc)) from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
