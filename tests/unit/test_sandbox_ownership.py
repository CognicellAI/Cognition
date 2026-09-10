"""Regression tests for retaining ownership of live session sandboxes."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent
from server.app.storage.config_models import LambdaMicroVmQuota
from tests.unit.test_cognition_agent_lambda_microvm_profile import _profile, _settings
from tests.unit.test_sandbox_lifecycle_quotas import FakeSandboxBackend, _manager


def test_repeated_registration_never_orphans_previous_sandbox():
    manager = _manager()
    first = FakeSandboxBackend(sandbox_id="first")
    second = FakeSandboxBackend(sandbox_id="second")
    manager.register_sandbox_backend("session", first, scope={"tenant": "one"})
    try:
        manager.register_sandbox_backend("session", second, scope={"tenant": "one"})
    except RuntimeError:
        pass  # Rejecting replacement is safe; silently dropping ownership is not.
    assert first.terminated or manager._sandbox_backends.get("session") is first, (
        "The first sandbox is still running but its ownership has been overwritten"
    )


def test_pending_teardown_retains_sandbox_ownership():
    manager = _manager()
    backend = FakeSandboxBackend(sandbox_id="first", teardown_status="pending")
    manager.register_sandbox_backend("session", backend, scope={"tenant": "one"})
    manager.release_sandbox_backend("session")
    assert manager._sandbox_backends.get("session") is backend, (
        "An unconfirmed teardown must not discard the live sandbox handle"
    )


def test_concurrent_acquisition_and_repeated_runs_share_one_backend():
    manager = _manager()
    factory = MagicMock(
        return_value=FakeSandboxBackend(
            sandbox_id="one",
            quota=LambdaMicroVmQuota(max_session_starts_per_minute=1),
        )
    )
    acquire = manager.sandbox_acquirer(
        "session",
        scope={"tenant": "a"},
        agent_name="reporter",
        run_id="run-one",
    )
    with ThreadPoolExecutor(max_workers=5) as pool:
        backends = list(pool.map(lambda _: acquire("configuration", factory), range(5)))
    assert all(backend is backends[0] for backend in backends)
    later = manager.sandbox_acquirer(
        "session",
        scope={"tenant": "a"},
        agent_name="reporter",
        run_id="run-two",
    )
    assert later("configuration", factory) is backends[0]
    factory.assert_called_once()
    assert manager._sandbox_correlations["session"]["run_id"] == "run-two"


@pytest.mark.parametrize(
    "scope,agent", [({"tenant": "b"}, "reporter"), ({}, "reporter"), ({"tenant": "a"}, "other")]
)
def test_acquisition_rejects_scope_or_agent_change_before_factory(scope, agent):
    manager = _manager()
    first = FakeSandboxBackend(sandbox_id="one")
    manager.sandbox_acquirer("session", scope={"tenant": "a"}, agent_name="reporter", run_id="1")(
        "config", lambda: first
    )
    other = MagicMock()
    with pytest.raises(RuntimeError, match="scope or agent"):
        manager.sandbox_acquirer("session", scope=scope, agent_name=agent, run_id="2")(
            "config", other
        )
    other.assert_not_called()
    assert not first.terminated


@pytest.mark.parametrize("teardown", ["complete", "pending", "failed"])
def test_config_change_waits_for_confirmed_teardown(teardown):
    manager = _manager()
    acquire = manager.sandbox_acquirer("session", scope={}, agent_name="reporter", run_id="1")
    first = FakeSandboxBackend(sandbox_id="one", teardown_status=teardown)
    acquire("first-config", lambda: first)
    second = MagicMock(return_value=FakeSandboxBackend(sandbox_id="two"))
    if teardown == "complete":
        assert acquire("second-config", second).id == "two"
        assert first.terminated
    else:
        with pytest.raises(RuntimeError, match="teardown is not confirmed"):
            acquire("second-config", second)
        assert manager._sandbox_backends["session"] is first
        with pytest.raises(RuntimeError, match="teardown is not confirmed"):
            acquire("first-config", second)
        second.assert_not_called()
        first.teardown_status = "complete"
        manager.release_sandbox_backend("session")
        assert acquire("second-config", second).id == "two"


@pytest.mark.asyncio
async def test_real_agent_factory_reuses_resolved_profile_and_replaces_changed_role(tmp_path):
    manager = _manager()
    profile = _profile()
    params = CognitionAgentParams(
        project_path=tmp_path,
        model=MagicMock(),
        store=MagicMock(),
        checkpointer=MagicMock(),
        settings=_settings(tmp_path),
        scope={"tenant": "a"},
        pinned_sandbox_profile_config=profile,
        _sandbox_acquirer=manager.sandbox_acquirer(
            "session",
            scope={"tenant": "a"},
            agent_name="reporter",
            run_id="one",
        ),
    )
    with patch("server.app.agent.cognition_agent.create_deep_agent", return_value=MagicMock()):
        first = await create_cognition_agent(params)
        second = await create_cognition_agent(params)
        assert second.sandbox_backend is first.sandbox_backend
        # Lazy construction has not launched a MicroVM.
        assert first.sandbox_backend._backend is None
        params.sandbox_execution_role_arn = "arn:aws:iam::123456789012:role/changed"
        third = await create_cognition_agent(params)
        assert third.sandbox_backend is not first.sandbox_backend
        with pytest.raises(RuntimeError, match="released"):
            first.sandbox_backend._get_backend()


async def test_shutdown_retains_unconfirmed_resources_and_releases_confirmed():
    manager = _manager()
    pending = FakeSandboxBackend(sandbox_id="pending", teardown_status="pending")
    complete = FakeSandboxBackend(sandbox_id="complete")
    manager.register_sandbox_backend("pending", pending)
    manager.register_sandbox_backend("complete", complete)
    await manager.close()
    assert manager._sandbox_backends == {"pending": pending}


def test_unregistration_retains_retryable_session_on_failed_teardown():
    manager = _manager()
    backend = FakeSandboxBackend(sandbox_id="one", teardown_status="pending")
    service = object()
    manager._services["session"] = service
    manager.register_sandbox_backend("session", backend)
    assert manager.unregister_session("session") is False
    assert manager._services["session"] is service
    backend.teardown_status = "complete"
    assert manager.unregister_session("session") is True
    assert "session" not in manager._services


def test_slow_teardown_does_not_block_unrelated_acquisition():
    manager = _manager()
    started, proceed = Event(), Event()
    first = FakeSandboxBackend(sandbox_id="one")
    original = first.terminate

    def slow_terminate():
        started.set()
        assert proceed.wait(5)
        original()

    first.terminate = slow_terminate
    manager.sandbox_acquirer("one", scope={}, agent_name="reporter", run_id="1")(
        "config", lambda: first
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        release = pool.submit(manager.release_sandbox_backend, "one")
        assert started.wait(5)
        try:
            assert manager.release_sandbox_backend("one") == "pending"
            acquire = manager.sandbox_acquirer("two", scope={}, agent_name="reporter", run_id="2")
            created = pool.submit(acquire, "config", lambda: FakeSandboxBackend(sandbox_id="two"))
            assert created.result(timeout=1).id == "two"
        finally:
            proceed.set()
        assert release.result(timeout=5) == "complete"


async def test_session_delete_does_not_delete_history_until_teardown_confirmed(monkeypatch):
    from fastapi import HTTPException

    from server.app.api.routes.sessions import delete_session

    manager = _manager()
    backend = FakeSandboxBackend(sandbox_id="one", teardown_status="pending")
    manager.register_sandbox_backend("session", backend, scope={"tenant": "one"})
    monkeypatch.setattr("server.app.api.routes.sessions._get_scoped_session", AsyncMock())
    store = AsyncMock()
    scope = MagicMock()
    scope.get_all.return_value = {"tenant": "one"}
    with pytest.raises(HTTPException) as caught:
        await delete_session("session", manager.settings, manager, scope, store)
    assert caught.value.status_code == 409
    store.delete_session.assert_not_called()
    backend.teardown_status = "complete"
    await delete_session("session", manager.settings, manager, scope, store)
    store.delete_session.assert_awaited_once_with("session", {"tenant": "one"})


def test_release_observation_distinguishes_untracked_from_confirmed():
    manager = _manager()
    assert manager.release_sandbox_backend("session") == "untracked"
    backend = FakeSandboxBackend(sandbox_id="one", teardown_status="pending")
    manager.register_sandbox_backend("session", backend, scope={"project": "one"})
    assert manager.release_sandbox_backend("session") == "pending"
    assert manager._sandbox_backends["session"] is backend
    backend.teardown_status = "complete"
    assert manager.release_sandbox_backend("session") == "complete"
    # No durable receipt is retained: a later call must not invent confirmation.
    assert manager.release_sandbox_backend("session") == "untracked"


def test_failed_release_remains_pending_for_retry():
    manager = _manager()
    backend = FakeSandboxBackend(sandbox_id="one")
    manager.register_sandbox_backend("session", backend, scope={})
    with patch.object(backend, "terminate", side_effect=RuntimeError("provider unavailable")):
        assert manager.release_sandbox_backend("session") == "pending"
    assert manager.release_sandbox_backend("session") == "complete"
