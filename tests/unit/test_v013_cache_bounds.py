"""Bounded shared runtime cache behavior for v0.13."""

from __future__ import annotations

from pathlib import Path

import pytest

from server.app.agent.cognition_agent import (
    RuntimeContext,
    cache_agent,
    clear_agent_cache,
    get_agent_cache_stats,
    get_cached_agent,
)
from server.app.llm.deep_agent_service import SessionAgentManager
from server.app.settings import Settings


def _runtime_context(
    tmp_path: Path,
    *,
    manifest_digest: str,
    max_entries: int = 2,
    ttl_seconds: float = 60.0,
) -> RuntimeContext:
    settings = Settings()
    settings.workspace_root = tmp_path
    settings.sandbox_backend = "local"
    settings.unsafe_local_execution = True
    settings.agent_cache_max_entries = max_entries
    settings.agent_cache_ttl_seconds = ttl_seconds
    return RuntimeContext.from_params(
        project_path=tmp_path,
        model="test-model",
        store=None,
        system_prompt="prompt",
        memory=[],
        skills=[],
        subagents=[],
        async_subagents=[],
        interrupt_on={},
        permissions=[],
        response_format=None,
        tool_token_limit_before_evict=None,
        context_policy=None,
        excluded_tools=[],
        blocked_tools=[],
        middleware=[],
        tools=[],
        settings=settings,
        scope={"tenant": "cache"},
        manifest_digest=manifest_digest,
    )


def test_agent_graph_cache_evicts_lru_by_capacity(tmp_path: Path) -> None:
    clear_agent_cache()
    first = _runtime_context(tmp_path, manifest_digest="first")
    second = _runtime_context(tmp_path, manifest_digest="second")
    third = _runtime_context(tmp_path, manifest_digest="third")

    cache_agent(first, "first-agent")
    cache_agent(second, "second-agent")
    assert get_cached_agent(first) == "first-agent"
    cache_agent(third, "third-agent")

    assert get_cached_agent(first) == "first-agent"
    assert get_cached_agent(second) is None
    assert get_cached_agent(third) == "third-agent"
    assert get_agent_cache_stats()["size"] == 2
    clear_agent_cache()


def test_agent_graph_cache_evicts_expired_entry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clear_agent_cache()
    current_time = 100.0
    monkeypatch.setattr(
        "server.app.agent.cognition_agent.time.monotonic",
        lambda: current_time,
    )
    context = _runtime_context(
        tmp_path,
        manifest_digest="ttl",
        ttl_seconds=5.0,
    )
    cache_agent(context, "agent")

    current_time = 103.0
    assert get_cached_agent(context) == "agent"
    current_time = 106.0
    assert get_cached_agent(context) is None
    assert get_agent_cache_stats()["size"] == 0
    clear_agent_cache()


def test_session_service_cache_evicts_lru_idle_session(tmp_path: Path) -> None:
    settings = Settings()
    settings.workspace_root = tmp_path
    settings.session_service_cache_max_entries = 2
    settings.session_service_cache_ttl_seconds = 60.0
    manager = SessionAgentManager(settings)

    manager.register_session("first", str(tmp_path / "first"))
    manager.register_session("second", str(tmp_path / "second"))
    assert manager.get_service("first") is not None
    manager.register_session("third", str(tmp_path / "third"))

    assert manager.get_service("first") is not None
    assert manager.get_service("second") is None
    assert manager.get_service("third") is not None
    assert manager.get_service_cache_stats() == {"size": 2, "evictions": 1}


def test_session_service_cache_ttl_skips_active_runtime(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    current_time = 200.0
    monkeypatch.setattr(
        "server.app.llm.deep_agent_service.time.monotonic",
        lambda: current_time,
    )
    settings = Settings()
    settings.workspace_root = tmp_path
    settings.session_service_cache_max_entries = 10
    settings.session_service_cache_ttl_seconds = 5.0
    manager = SessionAgentManager(settings)
    manager.register_session("active", str(tmp_path / "active"))
    manager.register_runtime("active", object())

    current_time = 206.0
    assert manager.get_service("active") is not None
    assert manager.get_service_cache_stats() == {"size": 1, "evictions": 0}
