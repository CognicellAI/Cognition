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
    ) -> None:
        self.id = sandbox_id
        self.profile = profile
        self.quota = quota
        self.terminated = False

    @property
    def runtime_metadata(self) -> dict[str, Any]:
        return {
            "backend": "aws_lambda_microvm",
            "profile": self.profile,
            "microvm_id": self.id,
            "status": "RUNNING",
        }

    def terminate(self) -> None:
        self.terminated = True


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
