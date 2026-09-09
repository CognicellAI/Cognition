"""Mirror existing latency observations to OTel without changing callers."""

from __future__ import annotations

from typing import Any

from opentelemetry import metrics


class LatencyHistogram:
    """Keep the existing Prometheus interface and one corresponding OTLP series."""

    def __init__(self, legacy: Any, name: str, labels: dict[str, str] | None = None) -> None:
        self._legacy = legacy
        self._name = name
        self._labels = labels or {}

    def labels(self, **labels: str) -> LatencyHistogram:
        return LatencyHistogram(self._legacy.labels(**labels), self._name, labels)

    def observe(self, value: float) -> None:
        self._legacy.observe(value)
        try:
            metrics.get_meter("cognition.latency").create_histogram(self._name, unit="s").record(
                value, self._labels
            )
        except Exception:
            pass  # Export failure cannot change application behavior.
