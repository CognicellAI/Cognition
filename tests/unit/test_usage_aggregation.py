"""Provider-authoritative usage aggregation tests."""

from __future__ import annotations

from server.app.agent.runtime import UsageEvent
from server.app.agent.usage import ProviderUsageAggregator
from server.app.api.routes.messages import _usage_trace_attributes


def test_complete_usage_uses_latest_streaming_metadata() -> None:
    aggregator = ProviderUsageAggregator(default_provider="openai", default_model="gpt-4.1")

    aggregator.observe_usage_metadata(
        "call-1",
        {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
    )
    aggregator.observe_usage_metadata(
        "call-1",
        {
            "input_tokens": 10,
            "output_tokens": 3,
            "total_tokens": 13,
            "input_token_details": {"cache_read": 8, "cache_creation": 2},
            "output_token_details": {"reasoning": 1},
        },
    )

    report = aggregator.build_report()

    assert report.status == "complete"
    assert report.input_tokens == 10
    assert report.output_tokens == 3
    assert report.total_tokens == 13
    assert report.cache_read_tokens == 8
    assert report.cache_write_tokens == 2
    assert report.reasoning_tokens == 1
    assert report.model_calls == 1
    assert report.reported_model_calls == 1
    assert report.unreported_model_calls == 0


def test_partial_usage_tracks_unreported_call() -> None:
    aggregator = ProviderUsageAggregator(default_provider="openai", default_model="gpt-4.1")
    aggregator.observe_activity("call-1")
    aggregator.observe_activity("call-2")
    aggregator.observe_usage_metadata(
        "call-1",
        {"input_tokens": 4, "output_tokens": 6},
    )

    report = aggregator.build_report()

    assert report.status == "partial"
    assert report.input_tokens == 4
    assert report.output_tokens == 6
    assert report.model_calls == 2
    assert report.reported_model_calls == 1
    assert report.unreported_model_calls == 1


def test_unavailable_usage_has_null_token_fields() -> None:
    aggregator = ProviderUsageAggregator(default_provider="mock", default_model="mock-model")
    aggregator.mark_unreported_fallback()

    report = aggregator.build_report()
    payload = report.to_payload()

    assert report.status == "unavailable"
    assert payload["input_tokens"] is None
    assert payload["output_tokens"] is None
    assert payload["total_tokens"] is None
    assert payload["estimated_cost"] is None
    assert payload["model_calls"] == 1
    assert payload["reported_model_calls"] == 0
    assert payload["unreported_model_calls"] == 1


def test_unavailable_usage_counts_multiple_unreported_model_calls() -> None:
    aggregator = ProviderUsageAggregator(default_provider="openai", default_model="gpt-4.1")
    aggregator.observe_usage_metadata("call-1", None)
    aggregator.observe_usage_metadata("call-2", {})

    report = aggregator.build_report()

    assert report.status == "unavailable"
    assert report.input_tokens is None
    assert report.output_tokens is None
    assert report.model_calls == 2
    assert report.reported_model_calls == 0
    assert report.unreported_model_calls == 2


def test_run_usage_summary_does_not_duplicate_standard_model_usage() -> None:
    attributes = _usage_trace_attributes(
        UsageEvent(
            status="complete",
            input_tokens=17,
            output_tokens=3,
            total_tokens=20,
            model_calls=1,
            reported_model_calls=1,
        )
    )

    assert attributes["cognition.usage.input_tokens"] == 17
    assert attributes["cognition.usage.output_tokens"] == 3
    assert attributes["cognition.usage.total_tokens"] == 20
    assert not any(key.startswith("gen_ai.usage.") for key in attributes)
