"""Normalize persisted Agent definitions for the native Skills runtime.

Revision ID: 007
Revises: 006
Create Date: 2026-09-02 00:00:00.000000
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "007"
down_revision: str | None = "006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _definition(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return None
        return dict(parsed) if isinstance(parsed, dict) else None
    return None


def _digest(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_agent_definition(definition: dict[str, Any]) -> bool:
    changed = False
    for field in ("skills", "tools"):
        if field in definition:
            del definition[field]
            changed = True

    subagents = definition.get("subagents")
    if isinstance(subagents, list):
        for subagent in subagents:
            if isinstance(subagent, dict) and "tools" in subagent:
                del subagent["tools"]
                changed = True
    return changed


def upgrade() -> None:
    """Remove obsolete capability fields from persisted Agent definitions."""
    connection = op.get_bind()
    config_entities = sa.table(
        "config_entities",
        sa.column("id", sa.Integer()),
        sa.column("entity_type", sa.String()),
        sa.column("definition", sa.JSON()),
        sa.column("revision", sa.Integer()),
        sa.column("definition_digest", sa.String()),
    )
    rows = connection.execute(
        sa.select(
            config_entities.c.id,
            config_entities.c.definition,
            config_entities.c.revision,
        ).where(config_entities.c.entity_type == "agent")
    ).mappings()
    for row in rows:
        definition = _definition(row["definition"])
        if definition is None or not _normalize_agent_definition(definition):
            continue
        connection.execute(
            config_entities.update()
            .where(config_entities.c.id == row["id"])
            .values(
                definition=definition,
                revision=int(row["revision"] or 1) + 1,
                definition_digest=_digest(definition),
            )
        )


def downgrade() -> None:
    """Do not recreate discarded legacy capability fields."""

