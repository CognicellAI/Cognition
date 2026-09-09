"""Bounded, content-free A2UI telemetry using the caller's OTel provider."""

from contextlib import suppress

from opentelemetry import metrics


def record_validation(direction: str, outcome: str, reason: str) -> None:
    with suppress(Exception):
        metrics.get_meter("cognition.a2ui").create_counter("cognition.a2ui.validations").add(
            1, {"direction": direction, "outcome": outcome, "reason": reason}
        )


def record_negotiation(outcome: str, catalog: str) -> None:
    with suppress(Exception):
        metrics.get_meter("cognition.a2ui").create_counter("cognition.a2ui.negotiations").add(
            1, {"outcome": outcome, "catalog": catalog}
        )


def record_batch(messages: list[dict[str, object]]) -> None:
    with suppress(Exception):
        meter = metrics.get_meter("cognition.a2ui")
        meter.create_histogram("cognition.a2ui.batch_messages").record(len(messages))
        counter = meter.create_counter("cognition.a2ui.messages")
        for message in messages:
            kind = next(
                (
                    key
                    for key in (
                        "createSurface",
                        "updateComponents",
                        "updateDataModel",
                        "deleteSurface",
                        "callRendererFunction",
                        "agentFunctionResponse",
                    )
                    if key in message
                ),
                "unknown",
            )
            counter.add(1, {"message_type": kind})
