"""Provider-authoritative model usage aggregation.

Cognition does not tokenize or estimate billable usage. This module only
normalizes provider-reported LangChain ``AIMessage.usage_metadata`` values into
one run-level builder event.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal, cast

UsageStatus = Literal["complete", "partial", "unavailable"]

USAGE_SOURCE_PROVIDER_METADATA = "provider_usage_metadata"


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number >= 0 else None


def _first_int(mapping: Mapping[str, Any], *keys: str) -> int | None:
    for key in keys:
        value = _as_int(mapping.get(key))
        if value is not None:
            return value
    return None


def _nested_int(mapping: Mapping[str, Any], parent_key: str, *keys: str) -> int | None:
    nested = mapping.get(parent_key)
    if not isinstance(nested, Mapping):
        return None
    return _first_int(cast(Mapping[str, Any], nested), *keys)


@dataclass
class ProviderUsageSnapshot:
    """Usage for one observed model call."""

    call_id: str
    provider: str
    model: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None

    @classmethod
    def from_metadata(
        cls,
        *,
        call_id: str,
        usage_metadata: Mapping[str, Any],
        provider: str,
        model: str,
    ) -> ProviderUsageSnapshot | None:
        """Build a snapshot from LangChain-normalized usage metadata."""
        input_tokens = _first_int(usage_metadata, "input_tokens", "prompt_tokens")
        output_tokens = _first_int(
            usage_metadata,
            "output_tokens",
            "completion_tokens",
            "generated_token_count",
        )
        total_tokens = _first_int(usage_metadata, "total_tokens")
        if total_tokens is None and input_tokens is not None and output_tokens is not None:
            total_tokens = input_tokens + output_tokens

        cache_read_tokens = _nested_int(
            usage_metadata,
            "input_token_details",
            "cache_read",
            "cached_tokens",
        )
        cache_write_tokens = _nested_int(
            usage_metadata,
            "input_token_details",
            "cache_write",
            "cache_creation",
            "cache_creation_input_tokens",
        )
        reasoning_tokens = _nested_int(
            usage_metadata,
            "output_token_details",
            "reasoning",
            "reasoning_tokens",
        )

        if all(
            value is None
            for value in (
                input_tokens,
                output_tokens,
                total_tokens,
                cache_read_tokens,
                cache_write_tokens,
                reasoning_tokens,
            )
        ):
            return None

        return cls(
            call_id=call_id,
            provider=provider or "unknown",
            model=model or "unknown",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=total_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
            reasoning_tokens=reasoning_tokens,
        )


@dataclass
class UsageReport:
    """Final builder-facing usage payload for a terminal run."""

    status: UsageStatus
    source: str = USAGE_SOURCE_PROVIDER_METADATA
    input_tokens: int | None = None
    output_tokens: int | None = None
    total_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    model_calls: int = 0
    reported_model_calls: int = 0
    unreported_model_calls: int = 0
    provider: str | None = None
    model: str | None = None
    by_model: list[dict[str, Any]] = field(default_factory=list)

    def to_payload(self) -> dict[str, Any]:
        """Return the stable API/SSE usage payload."""
        return {
            "type": "usage",
            "source": self.source,
            "status": self.status,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cache_read_tokens": self.cache_read_tokens,
            "cache_write_tokens": self.cache_write_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "model_calls": self.model_calls,
            "reported_model_calls": self.reported_model_calls,
            "unreported_model_calls": self.unreported_model_calls,
            "provider": self.provider,
            "model": self.model,
            "by_model": self.by_model,
            "estimated_cost": None,
        }


class ProviderUsageAggregator:
    """Deduplicate streaming usage metadata and aggregate one run-level report."""

    def __init__(
        self,
        *,
        default_provider: str = "unknown",
        default_model: str = "unknown",
    ) -> None:
        self._default_provider = default_provider or "unknown"
        self._default_model = default_model or "unknown"
        self._observed_calls: dict[str, tuple[str, str]] = {}
        self._reported_calls: dict[str, ProviderUsageSnapshot] = {}

    def observe_activity(
        self,
        call_id: str,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        """Record that a model call occurred, even if usage is unavailable."""
        safe_call_id = call_id or "unknown"
        if safe_call_id not in self._observed_calls:
            self._observed_calls[safe_call_id] = (
                provider or self._default_provider,
                model or self._default_model,
            )

    def observe_usage_metadata(
        self,
        call_id: str,
        usage_metadata: Mapping[str, Any] | None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        """Record the latest cumulative provider metadata for one model call."""
        safe_call_id = call_id or "unknown"
        effective_provider = provider or self._default_provider
        effective_model = model or self._default_model
        self.observe_activity(
            safe_call_id,
            provider=effective_provider,
            model=effective_model,
        )
        if not isinstance(usage_metadata, Mapping):
            return
        snapshot = ProviderUsageSnapshot.from_metadata(
            call_id=safe_call_id,
            usage_metadata=usage_metadata,
            provider=effective_provider,
            model=effective_model,
        )
        if snapshot is not None:
            self._reported_calls[safe_call_id] = snapshot

    def mark_unreported_fallback(self) -> None:
        """Record one unreported model call when streaming showed output only."""
        if not self._observed_calls and not self._reported_calls:
            self.observe_activity(
                "unreported:0",
                provider=self._default_provider,
                model=self._default_model,
            )

    def build_report(self) -> UsageReport:
        """Return one final complete/partial/unavailable usage report."""
        model_calls = max(len(self._observed_calls), len(self._reported_calls))
        reported_model_calls = len(self._reported_calls)
        unreported_model_calls = max(0, model_calls - reported_model_calls)

        if reported_model_calls == 0:
            return UsageReport(
                status="unavailable",
                model_calls=model_calls,
                reported_model_calls=0,
                unreported_model_calls=unreported_model_calls,
                provider=self._single_provider(),
                model=self._single_model(),
            )

        totals = self._sum_reported_calls(self._reported_calls.values())
        status: UsageStatus = "complete" if unreported_model_calls == 0 else "partial"
        by_model = self._by_model() if len(self._model_keys()) > 1 else []
        return UsageReport(
            status=status,
            input_tokens=totals["input_tokens"],
            output_tokens=totals["output_tokens"],
            total_tokens=totals["total_tokens"],
            cache_read_tokens=totals["cache_read_tokens"],
            cache_write_tokens=totals["cache_write_tokens"],
            reasoning_tokens=totals["reasoning_tokens"],
            model_calls=model_calls,
            reported_model_calls=reported_model_calls,
            unreported_model_calls=unreported_model_calls,
            provider=self._single_provider(),
            model=self._single_model(),
            by_model=by_model,
        )

    def _single_provider(self) -> str | None:
        keys = self._model_keys()
        return next(iter(keys))[0] if len(keys) == 1 else None

    def _single_model(self) -> str | None:
        keys = self._model_keys()
        return next(iter(keys))[1] if len(keys) == 1 else None

    def _model_keys(self) -> set[tuple[str, str]]:
        keys = set(self._observed_calls.values())
        keys.update(
            (snapshot.provider, snapshot.model) for snapshot in self._reported_calls.values()
        )
        return keys

    def _by_model(self) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for provider, model in sorted(self._model_keys()):
            calls = [
                snapshot
                for snapshot in self._reported_calls.values()
                if snapshot.provider == provider and snapshot.model == model
            ]
            totals = self._sum_reported_calls(calls)
            observed = sum(
                1
                for observed_provider, observed_model in self._observed_calls.values()
                if observed_provider == provider and observed_model == model
            )
            rows.append(
                {
                    "provider": provider,
                    "model": model,
                    "input_tokens": totals["input_tokens"],
                    "output_tokens": totals["output_tokens"],
                    "total_tokens": totals["total_tokens"],
                    "cache_read_tokens": totals["cache_read_tokens"],
                    "cache_write_tokens": totals["cache_write_tokens"],
                    "reasoning_tokens": totals["reasoning_tokens"],
                    "model_calls": max(observed, len(calls)),
                    "reported_model_calls": len(calls),
                    "unreported_model_calls": max(0, observed - len(calls)),
                }
            )
        return rows

    @staticmethod
    def _sum_reported_calls(calls: Any) -> dict[str, int | None]:
        snapshots = list(calls)

        def total_for(field_name: str) -> int | None:
            values = [
                value
                for snapshot in snapshots
                if (value := getattr(snapshot, field_name)) is not None
            ]
            return sum(values) if values else None

        return {
            "input_tokens": total_for("input_tokens"),
            "output_tokens": total_for("output_tokens"),
            "total_tokens": total_for("total_tokens"),
            "cache_read_tokens": total_for("cache_read_tokens"),
            "cache_write_tokens": total_for("cache_write_tokens"),
            "reasoning_tokens": total_for("reasoning_tokens"),
        }
