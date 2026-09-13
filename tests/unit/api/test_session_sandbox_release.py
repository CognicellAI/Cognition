"""Scoped release is independent of session/history deletion."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from server.app.api.dependencies import (
    get_scope_dep,
    get_session_agent_manager_dep,
    get_storage_backend_dep,
)
from server.app.api.routes.sessions import router
from server.app.api.scoping import SessionScope
from server.app.models import SessionConfig, SessionStatus
from server.app.storage.memory import MemoryStorageBackend


@pytest.fixture
def release_fixture(tmp_path):
    store = MemoryStorageBackend(workspace_path=tmp_path)
    scope = {"project": "one"}
    session = asyncio.run(
        store.create_session("session", "thread", SessionConfig(), "agent", scopes=scope)
    )
    session.status = SessionStatus.IDLE
    asyncio.run(
        store.create_message("message", "session", "user", "retained", effective_scope=scope)
    )
    manager = MagicMock()
    app = FastAPI()
    app.include_router(router)

    def scoped(request: Request):
        return SessionScope({"project": request.headers.get("x-project", "one")})

    app.dependency_overrides[get_scope_dep] = scoped
    app.dependency_overrides[get_storage_backend_dep] = lambda: store
    app.dependency_overrides[get_session_agent_manager_dep] = lambda: manager
    return TestClient(app), store, session, manager, scope


@pytest.mark.parametrize("observed", ["complete", "pending", "untracked", "busy"])
def test_release_preserves_observation_and_history(release_fixture, observed):
    client, store, session, manager, scope = release_fixture
    before = deepcopy(session)
    manager.release_sandbox_backend.return_value = observed
    response = client.post("/sessions/session/sandbox/release")
    assert response.status_code == 200
    assert response.json() == {"status": observed}
    manager.release_sandbox_backend.assert_called_once_with("session", only_if_idle=True)
    assert asyncio.run(store.get_session("session", scope)) == before
    assert asyncio.run(store.get_message("message", scope)).content == "retained"


@pytest.mark.parametrize(
    "state",
    [
        SessionStatus.ACTIVE,
        SessionStatus.WAITING_FOR_APPROVAL,
        SessionStatus.STALLED,
        SessionStatus.QUEUED,
    ],
)
def test_protected_session_does_not_release(release_fixture, state):
    client, _, session, manager, _ = release_fixture
    session.status = state
    assert client.post("/sessions/session/sandbox/release").json() == {"status": "busy"}
    manager.release_sandbox_backend.assert_not_called()


def test_persisted_run_and_foreign_scope_do_not_release(release_fixture):
    client, store, _, manager, _ = release_fixture
    store.get_active_run = AsyncMock(return_value=object())
    assert client.post("/sessions/session/sandbox/release").json() == {"status": "busy"}
    assert (
        client.post("/sessions/session/sandbox/release", headers={"x-project": "other"}).status_code
        == 404
    )
    manager.release_sandbox_backend.assert_not_called()
