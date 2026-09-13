"""Shared retention eligibility; no transport or product policy."""

from __future__ import annotations

from datetime import datetime

from server.app.models import Session

RETAINABLE_SESSION_STATUSES = ("idle", "done", "failed", "aborted", "inactive", "error", "expired")
RETAINABLE_RUN_STATUSES = ("done", "failed", "aborted", "rejected")
RETAINABLE_TASK_STATUSES = ("completed", "failed", "canceled", "rejected")


def validate_retention_scan(before: str, limit: int) -> None:
    """Require an explicit timezone and a bounded maintenance page."""
    if datetime.fromisoformat(before).tzinfo is None:
        raise ValueError("Retention cutoff requires a timezone")
    if not 1 <= limit <= 1000:
        raise ValueError("Retention page limit must be between 1 and 1000")


def session_retention_eligible(session: Session, before: str) -> bool:
    """Check context age and status; callers must also check run/task dependencies."""
    return (
        session.status in RETAINABLE_SESSION_STATUSES
        and datetime.fromisoformat(session.updated_at) <= datetime.fromisoformat(before)
    )
