"""Skill management API routes.

Skills are Markdown files that inject domain-specific instructions into an
agent's context window via progressive disclosure. They are stored in the
ConfigStore and loaded by the agent at runtime.
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException

from server.app.api.dependencies import get_config_store, get_scope_dep
from server.app.api.models import SkillCreate, SkillList, SkillResponse, SkillUpdate
from server.app.api.scoping import SessionScope
from server.app.storage.config_store import ConfigStore

router = APIRouter(prefix="/skills", tags=["skills"])

logger = structlog.get_logger(__name__)


def _to_response(skill: Any) -> SkillResponse:
    return SkillResponse(
        name=skill.name,
        path=skill.path,
        enabled=skill.enabled,
        description=skill.description,
        content=skill.content,
        scope=skill.scope,
        source=skill.source,
    )


@router.get("", response_model=SkillList)
async def list_skills(
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillList:
    """List all registered skills visible in the given scope."""
    skills = await config_store.list_skills(scope=scope.get_all() or None)
    return SkillList(skills=[_to_response(s) for s in skills], count=len(skills))


@router.get("/{name}", response_model=SkillResponse)
async def get_skill(
    name: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillResponse:
    """Get a skill by name."""
    skill = await config_store.get_skill(name, scope=scope.get_all() or None)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    return _to_response(skill)


@router.post("", response_model=SkillResponse, status_code=201)
async def create_skill(
    body: SkillCreate,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillResponse:
    """Create or replace a skill in the ConfigStore."""
    effective_scope = scope.get_all() or (body.scope or {})
    existing = await config_store.get_skill(body.name, scope=effective_scope)
    if existing is not None and existing.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{body.name}' is file-managed and cannot be modified via API",
        )

    # Auto-generate path if content is provided, otherwise use provided path
    if body.content:
        skill_path = f"/skills/api/{body.name}/SKILL.md"
    elif body.path:
        skill_path = body.path
    else:
        raise HTTPException(status_code=400, detail="path is required when content is not provided")

    skill_data: dict[str, Any] = {
        "name": body.name,
        "path": skill_path,
        "enabled": body.enabled,
        "description": body.description,
        "content": body.content,
        "scope": effective_scope,
        "source": "api",
    }
    await config_store.upsert_skill_from_dict(skill_data)
    logger.info("skill_created", name=body.name, scope=effective_scope, enabled=body.enabled)

    skill = await config_store.get_skill(body.name, scope=effective_scope)
    if skill is None:
        raise HTTPException(status_code=500, detail="Skill not found after creation")
    return _to_response(skill)


@router.put("/{name}", response_model=SkillResponse)
async def replace_skill(
    name: str,
    body: SkillCreate,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillResponse:
    """Replace a skill definition (full update)."""
    body.name = name
    return await create_skill(body, scope=scope, config_store=config_store)


@router.patch("/{name}", response_model=SkillResponse)
async def update_skill(
    name: str,
    body: SkillUpdate,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> SkillResponse:
    """Partially update a skill definition."""
    scope_dict = scope.get_all() or None
    skill = await config_store.get_skill(name, scope=scope_dict)
    if skill is None:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    if skill.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{name}' is file-managed and cannot be modified via API",
        )

    updates = body.model_dump(exclude_none=True)

    if body.content and "path" not in updates:
        updates["path"] = f"/skills/api/{name}/SKILL.md"

    updated = skill.model_copy(update=updates)
    await config_store.upsert_skill(updated)
    logger.info("skill_updated", name=name, scope=scope_dict, fields=list(updates.keys()))
    return _to_response(updated)


@router.delete("/{name}", status_code=204)
async def delete_skill(
    name: str,
    scope: SessionScope = Depends(get_scope_dep),  # noqa: B008
    config_store: ConfigStore = Depends(get_config_store),  # noqa: B008
) -> None:
    """Delete a skill from the ConfigStore."""
    scope_dict = scope.get_all() or None
    skill = await config_store.get_skill(name, scope=scope_dict)
    if skill is not None and skill.source == "file":
        raise HTTPException(
            status_code=409,
            detail=f"Skill '{name}' is file-managed and cannot be modified via API",
        )
    deleted = await config_store.delete_skill(name, scope=scope_dict)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Skill '{name}' not found")
    logger.info("skill_deleted", name=name, scope=scope_dict)
