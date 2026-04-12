"""Skill management API routes.

Skills are Markdown files that inject domain-specific instructions into an
agent's context window via progressive disclosure. They are stored in the
ConfigRegistry and loaded by the agent at runtime.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException

from server.app.api.dependencies import get_config_store
from server.app.api.models import SkillCreate, SkillList, SkillResponse, SkillUpdate
from server.app.storage.config_models import SkillDefinition
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/skills", tags=["skills"])

logger = structlog.get_logger(__name__)


def _to_response(skill: SkillDefinition) -> SkillResponse:
    return SkillResponse(
        name=skill.name,
        path=skill.path,
        enabled=skill.enabled,
        description=skill.description,
        content=skill.content,
        scope=skill.scope,
        source=skill.source,
    )


def _scope_from_headers(
    user: str | None = Header(None, alias="x-cognition-scope-user"),
    project: str | None = Header(None, alias="x-cognition-scope-project"),
) -> dict[str, str] | None:
    """Extract optional scope dict from request headers."""
    scope: dict[str, str] = {}
    if user:
        scope["user"] = user
    if project:
        scope["project"] = project
    return scope if scope else None


def _get_store(config_store: ConfigStore = Depends(get_config_store)) -> ConfigStore:  # noqa: B008
    return config_store


@router.get("", response_model=SkillList)
async def list_skills(
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillList:
    """List all registered skills visible in the given scope."""
    skills = await config_store.list_skills(scope=scope)
    return SkillList(skills=[_to_response(s) for s in skills], count=len(skills))


@router.get("/{name}", response_model=SkillResponse)
async def get_skill(
    name: str,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillResponse:
    """Get a skill by name."""
    skill = await config_store.get_skill(name, scope=scope)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return _to_response(skill)


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    body: SkillCreate,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillResponse:
    """Create or replace a skill in the ConfigStore."""
    effective_scope = scope if scope is not None else (body.scope or {})

    # Auto-generate path if content is provided, otherwise use provided path
    if body.content:
        skill_path = f"/skills/api/{body.name}/SKILL.md"
    elif body.path:
        skill_path = body.path
    else:
        raise HTTPException(status_code=400, detail="path is required when content is not provided")

    skill = SkillDefinition(
        name=body.name,
        path=skill_path,
        enabled=body.enabled,
        description=body.description,
        content=body.content,
        scope=effective_scope,
        source="api",
    )
    await config_store.upsert_skill(skill)
    logger.info("skill_created", name=skill.name, scope=effective_scope, enabled=skill.enabled)
    return _to_response(skill)


@router.put("/{name}", response_model=SkillResponse)
async def replace_skill(
    name: str,
    body: SkillCreate,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillResponse:
    """Replace a skill definition (full update)."""
    body.name = name
    return await create_skill(body, scope=scope, config_store=config_store)


@router.patch("/{name}", response_model=SkillResponse)
async def update_skill(
    name: str,
    body: SkillUpdate,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillResponse:
    """Partially update a skill definition."""
    skill = await config_store.get_skill(name, scope=scope)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    updates = body.model_dump(exclude_none=True)

    if body.content and "path" not in updates:
        updates["path"] = f"/skills/api/{name}/SKILL.md"

    updated = skill.model_copy(update=updates)
    await config_store.upsert_skill(updated)
    logger.info("skill_updated", name=name, scope=scope, fields=list(updates.keys()))
    return _to_response(updated)


@router.delete("/{name}", status_code=204)
async def delete_skill(
    name: str,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> None:
    """Delete a skill from the ConfigStore."""
    deleted = await config_store.delete_skill(name, scope=scope)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    logger.info("skill_deleted", name=name, scope=scope)
