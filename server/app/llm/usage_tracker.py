"""Token usage tracking and cost estimation.

Tracks input/output/cached tokens per session and project,
and estimates costs using models.dev pricing data.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional, Sequence

import structlog

from server.app.llm.model_registry import ModelCost, ModelRegistry, get_model_registry

logger = structlog.get_logger(__name__)


@dataclass
class TokenUsageEvent:
    """A single token usage event."""

    input_tokens: int = 0
    output_tokens: int = 0
    cached_tokens: int = 0
    reasoning_tokens: int = 0
    model_id: Optional[str] = None
    provider_id: Optional[str] = None
    timestamp: float = field(default_factory=time.time)
    estimated_cost: float = 0.0


@dataclass
class UsageSummary:
    """Aggregated usage for a session or project."""

    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cached_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_estimated_cost: float = 0.0
    event_count: int = 0
    first_event: Optional[float] = None
    last_event: Optional[float] = None

    @property
    def total_tokens(self) -> int:
        """Total tokens across all categories."""
        return self.total_input_tokens + self.total_output_tokens + self.total_reasoning_tokens


class UsageTracker:
    """Tracks token usage and costs per session and project.

    Example:
        tracker = UsageTracker()
        tracker.record(
            session_id="sess-123",
            project_id="proj-456",
            input_tokens=1000,
            output_tokens=500,
            provider_id="anthropic",
            model_id="claude-opus-4-6",
        )
        summary = tracker.get_session_summary("sess-123")
        print(f"Cost: ${summary.total_estimated_cost:.4f}")
    """

    def __init__(self) -> None:
        self._session_events: dict[str, list[TokenUsageEvent]] = {}
        self._project_sessions: dict[str, set[str]] = {}

    def record(
        self,
        session_id: str,
        project_id: str,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
        provider_id: Optional[str] = None,
        model_id: Optional[str] = None,
    ) -> TokenUsageEvent:
        """Record a token usage event.

        Args:
            session_id: Session that generated the usage.
            project_id: Project the session belongs to.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            cached_tokens: Number of cached input tokens.
            reasoning_tokens: Number of reasoning tokens.
            provider_id: LLM provider (e.g. 'anthropic').
            model_id: Model used (e.g. 'claude-opus-4-6').

        Returns:
            The recorded TokenUsageEvent with estimated cost.
        """
        # Estimate cost using registry
        estimated_cost = 0.0
        if provider_id and model_id:
            estimated_cost = self._estimate_cost(
                provider_id=provider_id,
                model_id=model_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_tokens=cached_tokens,
                reasoning_tokens=reasoning_tokens,
            )

        event = TokenUsageEvent(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_tokens=cached_tokens,
            reasoning_tokens=reasoning_tokens,
            model_id=model_id,
            provider_id=provider_id,
            estimated_cost=estimated_cost,
        )

        # Store by session
        if session_id not in self._session_events:
            self._session_events[session_id] = []
        self._session_events[session_id].append(event)

        # Map project -> sessions
        if project_id not in self._project_sessions:
            self._project_sessions[project_id] = set()
        self._project_sessions[project_id].add(session_id)

        logger.debug(
            "Recorded token usage",
            session_id=session_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated_cost=f"${estimated_cost:.6f}",
        )

        return event

    def _estimate_cost(
        self,
        provider_id: str,
        model_id: str,
        input_tokens: int,
        output_tokens: int,
        cached_tokens: int = 0,
        reasoning_tokens: int = 0,
    ) -> float:
        """Estimate cost using model registry pricing.

        Args:
            provider_id: LLM provider.
            model_id: Model used.
            input_tokens: Number of input tokens.
            output_tokens: Number of output tokens.
            cached_tokens: Number of cached input tokens.
            reasoning_tokens: Number of reasoning tokens.

        Returns:
            Estimated cost in USD.
        """
        registry = get_model_registry()
        model_info = registry.get_model(provider_id, model_id)

        if not model_info:
            return 0.0

        cost = model_info.cost
        total = cost.estimate(input_tokens, output_tokens, cached_tokens)

        # Add reasoning cost if applicable
        if reasoning_tokens and cost.reasoning is not None:
            total += (reasoning_tokens / 1_000_000) * cost.reasoning

        return total

    def get_session_summary(self, session_id: str) -> UsageSummary:
        """Get aggregated usage for a session.

        Args:
            session_id: Session to summarize.

        Returns:
            UsageSummary with totals.
        """
        events = self._session_events.get(session_id, [])
        return self._aggregate_events(events)

    def get_project_summary(self, project_id: str) -> UsageSummary:
        """Get aggregated usage for all sessions in a project.

        Args:
            project_id: Project to summarize.

        Returns:
            UsageSummary with totals across all project sessions.
        """
        session_ids = self._project_sessions.get(project_id, set())
        all_events: list[TokenUsageEvent] = []
        for sid in session_ids:
            all_events.extend(self._session_events.get(sid, []))
        return self._aggregate_events(all_events)

    def get_session_events(self, session_id: str) -> Sequence[TokenUsageEvent]:
        """Get all usage events for a session.

        Args:
            session_id: Session to query.

        Returns:
            Sequence of TokenUsageEvent objects.
        """
        return list(self._session_events.get(session_id, []))

    def clear_session(self, session_id: str) -> None:
        """Remove usage data for a session.

        Args:
            session_id: Session to clear.
        """
        self._session_events.pop(session_id, None)
        # Remove from project mappings
        for project_sessions in self._project_sessions.values():
            project_sessions.discard(session_id)

    def _aggregate_events(self, events: Sequence[TokenUsageEvent]) -> UsageSummary:
        """Aggregate a sequence of events into a summary."""
        if not events:
            return UsageSummary()

        summary = UsageSummary(
            event_count=len(events),
            first_event=events[0].timestamp,
            last_event=events[-1].timestamp,
        )

        for event in events:
            summary.total_input_tokens += event.input_tokens
            summary.total_output_tokens += event.output_tokens
            summary.total_cached_tokens += event.cached_tokens
            summary.total_reasoning_tokens += event.reasoning_tokens
            summary.total_estimated_cost += event.estimated_cost

        return summary


# Global tracker instance
_tracker: UsageTracker | None = None


def get_usage_tracker() -> UsageTracker:
    """Get the global usage tracker instance."""
    global _tracker
    if _tracker is None:
        _tracker = UsageTracker()
    return _tracker
