"""Unit tests for sandbox lifecycle correlation and quotas."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic_settings import SettingsConfigDict

from server.app.llm.deep_agent_service import SandboxQuotaExceededError, SessionAgentManager
from server.app.settings import Settings
from server.app.storage.config_models import LambdaMicroVmQuota


class _TestSettings(Settings):
    """Settings that do not load local env files during unit tests."""

    model_config = SettingsConfigDict(
        env_file=None,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        populate_by_name=True,
    )


class FakeSandboxBackend:
    def __init__(
        self,
        *,
        sandbox_id: str,
        profile: str = "lambda-default",
        quota: LambdaMicroVmQuota | None = None,
        lifecycle_phases: list[str] | None = None,
        teardown_status: str = "complete",
        raise_on_terminate: Exception | None = None,
    ) -> None:
        self.id = sandbox_id
        self.profile = profile
        self.quota = quota
        self.terminated = False
        self.terminate_calls = 0
        self.lifecycle_phases = lifecycle_phases or []
        self.teardown_status = teardown_status
        self.raise_on_terminate = raise_on_terminate
        self.status = "RUNNING"

    @property
    def runtime_metadata(self) -> dict[str, Any]:
        return {
            "backend": "aws_lambda_microvm",
            "profile": self.profile,
            "microvm_id": self.id,
            "status": self.status,
            "aws_state": self.status,
            "lifecycle_phases": list(self.lifecycle_phases),
            "teardown_status": self.teardown_status if self.terminated else None,
        }

    def terminate(self) -> None:
        self.terminate_calls += 1
        if self.raise_on_terminate is not None:
            raise self.raise_on_terminate
        self.terminated = True
        if self.teardown_status == "complete":
            self.status = "TERMINATED"
        elif self.teardown_status == "pending":
            self.status = "RUNNING"


def _manager() -> SessionAgentManager:
    settings = _TestSettings.model_validate({"sandbox_backend": "aws_lambda_microvm"})
    return SessionAgentManager(settings=settings)


def test_lifecycle_event_includes_safe_correlation_metadata() -> None:
    manager = _manager()
    backend = FakeSandboxBackend(
        sandbox_id="microvm-1",
        quota=LambdaMicroVmQuota(max_concurrent_sessions=2),
    )

    manager.register_sandbox_backend(
        "session-1",
        backend,
        run_id="run-1",
        agent_name="repo-maintainer",
        scope={"tenant": "acme", "project": "docs"},
    )

    events = manager.drain_sandbox_events("session-1")
    assert len(events) == 1
    metadata = events[0].metadata
    assert metadata["microvm_id"] == "microvm-1"
    assert metadata["correlation"] == {
        "session_id": "session-1",
        "run_id": "run-1",
        "agent_name": "repo-maintainer",
        "profile": "lambda-default",
        "scope_keys": ["project", "tenant"],
        "scope_fingerprint": metadata["correlation"]["scope_fingerprint"],
    }
    assert metadata["correlation"]["scope_fingerprint"]
    assert "acme" not in str(metadata)
    assert "quota_key" not in metadata["correlation"]


def test_max_concurrent_sessions_quota_is_profile_and_scope_scoped() -> None:
    manager = _manager()
    quota = LambdaMicroVmQuota(max_concurrent_sessions=1)

    manager.register_sandbox_backend(
        "session-1",
        FakeSandboxBackend(sandbox_id="microvm-1", quota=quota),
        scope={"tenant": "acme"},
    )

    with pytest.raises(SandboxQuotaExceededError, match="max_concurrent_sessions=1"):
        manager.register_sandbox_backend(
            "session-2",
            FakeSandboxBackend(sandbox_id="microvm-2", quota=quota),
            scope={"tenant": "acme"},
        )

    manager.register_sandbox_backend(
        "session-3",
        FakeSandboxBackend(sandbox_id="microvm-3", quota=quota),
        scope={"tenant": "other"},
    )


def test_session_starts_per_minute_quota_blocks_burst_restarts() -> None:
    manager = _manager()
    quota = LambdaMicroVmQuota(max_session_starts_per_minute=1)

    manager.register_sandbox_backend(
        "session-1",
        FakeSandboxBackend(sandbox_id="microvm-1", quota=quota),
        scope={"tenant": "acme"},
    )
    manager.unregister_session("session-1")

    with pytest.raises(SandboxQuotaExceededError, match="max_session_starts_per_minute=1"):
        manager.register_sandbox_backend(
            "session-2",
            FakeSandboxBackend(sandbox_id="microvm-2", quota=quota),
            scope={"tenant": "acme"},
        )


def test_release_sandbox_backend_frees_concurrent_quota_and_is_idempotent() -> None:
    manager = _manager()
    quota = LambdaMicroVmQuota(max_concurrent_sessions=1)
    first = FakeSandboxBackend(sandbox_id="microvm-1", quota=quota)

    manager.register_sandbox_backend(
        "session-1",
        first,
        scope={"tenant": "acme"},
    )

    manager.release_sandbox_backend("session-1")
    manager.release_sandbox_backend("session-1")

    assert first.terminated is True
    assert first.terminate_calls == 1

    phases = [event.phase for event in manager.drain_sandbox_events("session-1")]
    assert phases == ["provisioned", "teardown_started", "teardown_complete"]

    second = FakeSandboxBackend(sandbox_id="microvm-2", quota=quota)
    manager.register_sandbox_backend(
        "session-2",
        second,
        scope={"tenant": "acme"},
    )

    assert second.terminated is False


def test_release_sandbox_backend_pending_teardown_frees_concurrent_quota() -> None:
    manager = _manager()
    quota = LambdaMicroVmQuota(max_concurrent_sessions=1)
    first = FakeSandboxBackend(
        sandbox_id="microvm-1",
        quota=quota,
        teardown_status="pending",
    )

    manager.register_sandbox_backend(
        "session-1",
        first,
        scope={"tenant": "acme"},
    )
    manager.release_sandbox_backend("session-1")

    phases = [event.phase for event in manager.drain_sandbox_events("session-1")]
    assert phases == ["provisioned", "teardown_started", "teardown_pending"]

    second = FakeSandboxBackend(sandbox_id="microvm-2", quota=quota)
    manager.register_sandbox_backend(
        "session-2",
        second,
        scope={"tenant": "acme"},
    )

    assert first.terminated is True
    assert second.terminated is False


def test_release_sandbox_backend_failed_teardown_emits_failure_event() -> None:
    manager = _manager()
    backend = FakeSandboxBackend(
        sandbox_id="microvm-1",
        raise_on_terminate=RuntimeError("control plane failed"),
    )

    manager.register_sandbox_backend("session-1", backend, scope={"tenant": "acme"})
    manager.release_sandbox_backend("session-1")

    events = manager.drain_sandbox_events("session-1")
    assert [event.phase for event in events] == [
        "provisioned",
        "teardown_started",
        "teardown_failed",
    ]
    assert events[-1].metadata["teardown_status"] == "failed"
    assert events[-1].metadata["teardown_error_code"] == "RuntimeError"


def test_snapshot_sandbox_backend_events_emits_new_microvm_phases_once() -> None:
    manager = _manager()
    backend = FakeSandboxBackend(
        sandbox_id="microvm-1",
        lifecycle_phases=[
            "launch_started",
            "launch_running",
            "auth_token_created",
            "runtime_healthcheck_started",
            "runtime_healthcheck_passed",
        ],
    )

    manager.register_sandbox_backend("session-1", backend, scope={"tenant": "acme"})

    first = manager.snapshot_sandbox_backend_events("session-1")
    second = manager.snapshot_sandbox_backend_events("session-1")

    assert [event.phase for event in first] == [
        "launch_started",
        "launch_running",
        "auth_token_created",
        "runtime_healthcheck_started",
        "runtime_healthcheck_passed",
        "runtime_snapshot",
    ]
    assert [event.phase for event in second] == ["runtime_snapshot"]


def test_release_sandbox_backend_preserves_start_rate_history() -> None:
    manager = _manager()
    quota = LambdaMicroVmQuota(
        max_concurrent_sessions=1,
        max_session_starts_per_minute=1,
    )

    manager.register_sandbox_backend(
        "session-1",
        FakeSandboxBackend(sandbox_id="microvm-1", quota=quota),
        scope={"tenant": "acme"},
    )
    manager.release_sandbox_backend("session-1")

    with pytest.raises(SandboxQuotaExceededError, match="max_session_starts_per_minute=1"):
        manager.register_sandbox_backend(
            "session-2",
            FakeSandboxBackend(sandbox_id="microvm-2", quota=quota),
            scope={"tenant": "acme"},
        )
