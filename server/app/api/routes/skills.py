"""Skill management API routes.

Skills are Markdown files that inject domain-specific instructions into an
agent's context window via progressive disclosure. They are stored in the
ConfigRegistry and loaded by the agent at runtime.
"""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, Header, HTTPException

from server.app.api.models import SkillCreate, SkillList, SkillResponse, SkillUpdate
from server.app.storage.config_models import SkillDefinition
from server.app.storage.config_registry import ConfigRegistry

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


def _get_registry() -> ConfigRegistry:
    """Resolve the ConfigRegistry or raise 503.

    Centralises the registry-not-initialised error so every route gets the
    same status code and message instead of the previous inconsistency
    (list→empty list, others→503).
    """
    from server.app.storage.config_registry import get_config_registry

    try:
        return get_config_registry()
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail="ConfigRegistry not available") from e


@router.get("", response_model=SkillList)
async def list_skills(
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> SkillList:
    """List all registered skills visible in the given scope."""
    reg = _get_registry()
    skills = await reg.list_skills(scope=scope)
    return SkillList(skills=[_to_response(s) for s in skills], count=len(skills))


@router.get("/{name}", response_model=SkillResponse)
async def get_skill(
    name: str,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> SkillResponse:
    """Get a skill by name."""
    reg = _get_registry()
    skill = await reg.get_skill(name, scope=scope)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return _to_response(skill)


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    body: SkillCreate,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> SkillResponse:
    """Create or replace a skill in the ConfigRegistry."""
    reg = _get_registry()

    # Header scope overrides body scope if provided
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
    await reg.upsert_skill(skill)
    logger.info("skill_created", name=skill.name, scope=effective_scope, enabled=skill.enabled)
    return _to_response(skill)


@router.put("/{name}", response_model=SkillResponse)
async def replace_skill(
    name: str,
    body: SkillCreate,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> SkillResponse:
    """Replace a skill definition (full update)."""
    body.name = name
    return await create_skill(body, scope=scope)


@router.patch("/{name}", response_model=SkillResponse)
async def update_skill(
    name: str,
    body: SkillUpdate,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> SkillResponse:
    """Partially update a skill definition."""
    reg = _get_registry()
    skill = await reg.get_skill(name, scope=scope)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")

    updates = body.model_dump(exclude_none=True)

    # Auto-generate path if content is being set but path is not provided
    if body.content and "path" not in updates:
        updates["path"] = f"/skills/api/{name}/SKILL.md"

    updated = skill.model_copy(update=updates)
    await reg.upsert_skill(updated)
    logger.info("skill_updated", name=name, scope=scope, fields=list(updates.keys()))
    return _to_response(updated)


@router.delete("/{name}", status_code=204)
async def delete_skill(
    name: str,
    scope: dict[str, str] | None = Depends(_scope_from_headers),  # noqa: B008
) -> None:
    """Delete a skill from the ConfigRegistry."""
    reg = _get_registry()
    deleted = await reg.delete_skill(name, scope=scope)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    logger.info("skill_deleted", name=name, scope=scope)
