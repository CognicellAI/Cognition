"""Effective publication limits, independent of builder product policy."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from server.app.agent.definition import AgentDefinition, PublicationPolicy

if TYPE_CHECKING:
    from server.app.storage.config_store import ConfigStore


@dataclass(frozen=True)
class PublicationLimits:
    """Validated deployment or effective publication limits."""

    enabled: bool
    max_bytes: int
    inline_limit: int

    def __post_init__(self) -> None:
        if not 0 <= self.inline_limit <= self.max_bytes <= 10 * 1024 * 1024:
            raise ValueError("Invalid artifact publication limits")

    def narrow(self, policy: PublicationPolicy | None) -> PublicationLimits:
        """Apply optional Agent policy without widening deployment authority."""
        if policy is None:
            return self
        maximum = min(
            self.max_bytes,
            self.max_bytes if policy.max_bytes is None else policy.max_bytes,
        )
        return PublicationLimits(
            enabled=self.enabled and policy.enabled,
            max_bytes=maximum,
            inline_limit=(
                0 if policy.delivery_mode == "url" else min(self.inline_limit, maximum)
            ),
        )


@dataclass(frozen=True)
class CurrentPublicationPolicy:
    """Read current policy without a pinned definition or inherited API fallback.

    API-owned definitions must continue to exist at their exact scope. Explicit
    shared file definitions retain their documented configuration fallback.
    Lookup failures propagate to prevent publication under stale permission.
    """

    store: ConfigStore
    agent_name: str
    scope_items: tuple[tuple[str, str], ...]
    source: Literal["api", "file"]
    deployment: PublicationLimits

    async def __call__(self) -> PublicationLimits:
        scope = dict(self.scope_items)
        definition: AgentDefinition | None
        record = await self.store.get_agent_record(self.agent_name, scope)
        if record is not None:
            if record.scope != scope or record.name != self.agent_name:
                raise ValueError("Publication policy scope mismatch")
            definition = AgentDefinition.model_validate(record.definition)
        elif self.source == "file":
            definition = await self.store.get_agent_definition(self.agent_name, scope)
        else:
            definition = None
        if definition is None or definition.name != self.agent_name:
            raise ValueError("Publication Agent is unavailable")
        return self.deployment.narrow(definition.publication)
