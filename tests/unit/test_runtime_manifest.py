"""Pinned Agent/dependency manifest behavior for v0.13 runs."""

from __future__ import annotations

import pytest

from server.app.agent.resolver import RuntimeResolver
from server.app.agent.skills_backend import ConfigRegistrySkillsBackend
from server.app.agent.task_runtime import AgentTaskRuntime, SubmitTask
from server.app.llm.deep_agent_service import (
    DeepAgentStreamingService,
    _pinned_skills,
)
from server.app.settings import Settings
from server.app.storage.config_models import ProviderConfig, SkillDefinition
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore
from server.app.storage.memory import MemoryStorageBackend


@pytest.mark.asyncio
async def test_active_run_keeps_agent_and_skill_snapshot_while_next_run_advances(
    tmp_path,
) -> None:
    scope = {"tenant": "manifest-tenant", "project": "manifest-project"}
    registry = MemoryConfigRegistry()
    config_store = DefaultConfigStore(registry, workspace_path=tmp_path)
    await config_store.upsert_skill(
        SkillDefinition(
            name="runtime-skill",
            path="registry://runtime-skill",
            content="# Revision one\nUse the first behavior.",
            scope=scope,
            source="api",
        )
    )
    first_record = await config_store.upsert_agent(
        "manifest-agent",
        scope,
        {
            "name": "manifest-agent",
            "mode": "primary",
            "system_prompt": "Agent revision one.",
            "skills": ["runtime-skill"],
        },
    )

    storage = MemoryStorageBackend(str(tmp_path))
    await storage.initialize()
    runtime = AgentTaskRuntime(
        storage,
        default_workspace_path=str(tmp_path),
        config_store=config_store,
    )
    first = await runtime.submit(
        SubmitTask(
            context_id="manifest-context-one",
            agent_name="manifest-agent",
            effective_scope=scope,
            content="first run",
        )
    )
    first_manifest = first.run.runtime_manifest
    first_manifest_digest = first.run.manifest_digest
    first_skill_digest = first_manifest["dependencies"]["skills"][
        "runtime-skill"
    ]["digest"]

    await config_store.upsert_skill(
        SkillDefinition(
            name="runtime-skill",
            path="registry://runtime-skill",
            content="# Revision two\nUse the second behavior.",
            scope=scope,
            source="api",
        )
    )
    second_record = await config_store.upsert_agent(
        "manifest-agent",
        scope,
        {
            "name": "manifest-agent",
            "mode": "primary",
            "system_prompt": "Agent revision two.",
            "skills": ["runtime-skill"],
        },
        expected_revision=first_record.revision,
    )

    # Mutable registry state has advanced, but the run's durable snapshot has
    # not changed.
    assert first.run.agent_revision == first_record.revision == 1
    assert (
        first.run.runtime_manifest["agent"]["definition"]["system_prompt"]
        == "Agent revision one."
    )
    assert first.run.manifest_digest == first_manifest_digest

    settings = Settings()
    settings.workspace_root = tmp_path
    settings.unsafe_local_execution = True
    service = DeepAgentStreamingService(settings, config_store=config_store)
    pinned_config, _ = await service._resolve_agent_config(
        session=first.session,
        project_path=str(tmp_path),
        scope=scope,
        runtime_manifest=first_manifest,
    )
    current_config, _ = await service._resolve_agent_config(
        session=first.session,
        project_path=str(tmp_path),
        scope=scope,
    )
    assert pinned_config.system_prompt == "Agent revision one."
    assert current_config.system_prompt == "Agent revision two."

    pinned_skills = _pinned_skills(first_manifest)
    assert pinned_skills is not None
    skill_backend = ConfigRegistrySkillsBackend(
        registry,
        scope,
        allowed_skill_names=["runtime-skill"],
        pinned_skills=pinned_skills,
    )
    downloaded = await skill_backend.adownload_files(
        ["/runtime-skill/SKILL.md"]
    )
    assert downloaded[0].content is not None
    assert b"Revision one" in downloaded[0].content

    second = await runtime.submit(
        SubmitTask(
            context_id="manifest-context-two",
            agent_name="manifest-agent",
            effective_scope=scope,
            content="second run",
        )
    )
    second_skill_digest = second.run.runtime_manifest["dependencies"]["skills"][
        "runtime-skill"
    ]["digest"]
    assert second.run.agent_revision == second_record.revision == 2
    assert second.run.manifest_digest != first_manifest_digest
    assert second_skill_digest != first_skill_digest
    assert (
        second.run.runtime_manifest["agent"]["definition"]["system_prompt"]
        == "Agent revision two."
    )

    await storage.close()


@pytest.mark.asyncio
async def test_explicit_empty_skill_attachment_exposes_no_registry_skill(
    tmp_path,
) -> None:
    registry = MemoryConfigRegistry()
    await registry.upsert_skill(
        SkillDefinition(
            name="unattached",
            path="registry://unattached",
            content="# Must stay hidden",
        )
    )
    backend = ConfigRegistrySkillsBackend(
        registry,
        allowed_skill_names=[],
    )
    assert await backend.als_info("/") == []
    response = await backend.adownload_files(["/unattached/SKILL.md"])
    # Direct reads are also denied even when a model guesses the virtual path.
    assert response[0].error == "file_not_found"


@pytest.mark.asyncio
async def test_runtime_manifest_pins_provider_selection_for_active_run(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scope = {"tenant": "provider-pin"}
    registry = MemoryConfigRegistry()
    config_store = DefaultConfigStore(registry, workspace_path=tmp_path)
    await config_store.upsert_provider(
        ProviderConfig(
            id="primary-provider",
            provider="openai_compatible",
            model="first-model",
            base_url="https://first.example/v1",
            api_key_env="PINNED_PROVIDER_API_KEY",
            scope=scope,
            priority=1,
            source="api",
        )
    )
    first_record = await config_store.upsert_agent(
        "provider-agent",
        scope,
        {
            "name": "provider-agent",
            "mode": "primary",
            "system_prompt": "Provider pinning.",
        },
    )
    storage = MemoryStorageBackend(str(tmp_path))
    await storage.initialize()
    runtime = AgentTaskRuntime(
        storage,
        default_workspace_path=str(tmp_path),
        config_store=config_store,
    )
    first = await runtime.submit(
        SubmitTask(
            context_id="provider-pin-context-one",
            agent_name="provider-agent",
            effective_scope=scope,
            content="first run",
        )
    )
    first_manifest = first.run.runtime_manifest

    await config_store.upsert_provider(
        ProviderConfig(
            id="primary-provider",
            provider="openai_compatible",
            model="second-model",
            base_url="https://second.example/v1",
            api_key_env="PINNED_PROVIDER_API_KEY",
            scope=scope,
            priority=1,
            source="api",
        )
    )
    second_record = await config_store.upsert_agent(
        "provider-agent",
        scope,
        {
            "name": "provider-agent",
            "mode": "primary",
            "system_prompt": "Provider pinning v2.",
        },
        expected_revision=first_record.revision,
    )

    monkeypatch.setenv("PINNED_PROVIDER_API_KEY", "resolved-key")
    settings = Settings()
    settings.workspace_root = tmp_path
    resolver = RuntimeResolver(config_store, settings)
    build_calls: list[dict[str, object]] = []

    def _fake_build_model(**kwargs: object) -> object:
        build_calls.append(dict(kwargs))
        return object()

    monkeypatch.setattr(resolver, "build_model", _fake_build_model)
    async def _ignore_tool_support(provider: str, model_id: str) -> None:
        return None

    monkeypatch.setattr(resolver, "_warn_if_no_tool_call_support", _ignore_tool_support)
    resolved = await resolver.resolve_runtime_model_from_manifest(
        first_manifest,
        session=first.session,
    )
    assert resolved.model_id == "first-model"
    assert build_calls[-1]["model_id"] == "first-model"
    assert build_calls[-1]["base_url"] == "https://first.example/v1"
    assert build_calls[-1]["api_key"] == "resolved-key"

    second = await runtime.submit(
        SubmitTask(
            context_id="provider-pin-context-two",
            agent_name="provider-agent",
            effective_scope=scope,
            content="second run",
        )
    )
    assert second.run.agent_revision == second_record.revision
    assert (
        second.run.runtime_manifest["dependencies"]["provider"]["target"]["model_id"]
        == "second-model"
    )
    assert second.run.manifest_digest != first.run.manifest_digest

    await storage.close()
