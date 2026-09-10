"""Definition compatibility and deployment ceiling tests."""

import pytest
from pydantic import ValidationError

from server.app.agent.definition import AgentDefinition, PublicationPolicy
from server.app.agent.publication_policy import PublicationLimits


def test_omitted_policy_preserves_deployment_behavior():
    definition = AgentDefinition(name="worker", system_prompt="Work")
    limits = PublicationLimits(enabled=True, max_bytes=1024, inline_limit=256)
    assert "publication" not in definition.model_dump(mode="json")
    assert limits.narrow(definition.publication) == limits
    assert AgentDefinition.model_validate_json(definition.model_dump_json()) == definition


@pytest.mark.parametrize("enabled", [False, True])
def test_agent_cannot_exceed_deployment_ceiling(enabled):
    limits = PublicationLimits(enabled=enabled, max_bytes=1024, inline_limit=256)
    result = limits.narrow(PublicationPolicy(enabled=True, max_bytes=2048))
    assert result == limits


def test_disable_and_url_delivery_roundtrip():
    definition = AgentDefinition(
        name="worker", system_prompt="Work",
        publication=PublicationPolicy(enabled=False, max_bytes=32, delivery_mode="url"),
    )
    restored = AgentDefinition.model_validate_json(definition.model_dump_json())
    assert PublicationLimits(True, 1024, 256).narrow(restored.publication) == (
        PublicationLimits(False, 32, 0)
    )




@pytest.mark.parametrize("data", [
    {"enabled": "false"}, {"max_bytes": True}, {"max_bytes": -1}, {"max_bytes": 0},
    {"max_bytes": 10 * 1024 * 1024 + 1}, {"delivery_mode": "raw"},
    {"tenant_id": "customer"},
])
def test_policy_rejects_invalid_or_product_specific_fields(data):
    with pytest.raises(ValidationError):
        PublicationPolicy.model_validate(data)


@pytest.mark.asyncio
async def test_current_policy_reads_exact_record_and_rejects_deleted_api_fallback(tmp_path):
    from server.app.agent.publication_policy import CurrentPublicationPolicy
    from server.app.storage.config_registry import MemoryConfigRegistry
    from server.app.storage.config_store import DefaultConfigStore

    store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
    a, b = {"project": "a"}, {"project": "b"}
    definition = AgentDefinition(name="worker", system_prompt="Work")
    await store.upsert_agent("worker", a, definition.model_dump())
    await store.upsert_agent("worker", b, definition.model_dump())
    resolver = CurrentPublicationPolicy(store, "worker", tuple(a.items()), "api",
                                        PublicationLimits(True, 1024, 256))
    assert (await resolver()).enabled
    definition.publication = PublicationPolicy(enabled=False)
    await store.upsert_agent("worker", b, definition.model_dump())
    assert (await resolver()).enabled  # sibling scope cannot change this policy
    await store.upsert_agent("worker", a, definition.model_dump())
    assert not (await resolver()).enabled
    # Even a shared file definition cannot revive a deleted API-owned Agent.
    store._agent_definitions["worker"] = AgentDefinition(name="worker", system_prompt="Shared")
    store._file_agent_names.add("worker")
    await store.delete_agent("worker", a)
    with pytest.raises(ValueError, match="unavailable"):
        await resolver()


@pytest.mark.asyncio
async def test_factory_uses_current_policy_instead_of_pinned_permission(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent
    from server.app.agent.publication import PublicationMiddleware
    from server.app.settings import Settings
    from server.app.storage.artifact_store import MemoryArtifactStore, S3ArtifactStore
    from server.app.storage.config_registry import MemoryConfigRegistry
    from server.app.storage.config_store import DefaultConfigStore

    store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
    scope = {"project": "a"}
    definition = AgentDefinition(name="worker", system_prompt="Work",
                                 publication=PublicationPolicy(enabled=True))
    await store.upsert_agent("worker", scope, definition.model_dump())
    sandbox = MagicMock(workspace_root="/workspace", skills_root="/workspace/skills")
    monkeypatch.setattr("server.app.agent.cognition_agent._create_sandbox", lambda *a, **k: sandbox)
    factory = MagicMock()
    monkeypatch.setattr("server.app.agent.cognition_agent.create_deep_agent", factory)
    params = CognitionAgentParams(
        project_path=tmp_path, model=MagicMock(), scope=scope, config_store=store,
        publication_agent_name="worker", publication_agent_source="api",
        settings=Settings(artifact_publication_enabled=True, unsafe_local_execution=True),
        artifact_store=S3ArtifactStore(MemoryArtifactStore(), bucket="test", base_prefix="test", hmac_key="test"),
    )
    await create_cognition_agent(params)
    assert any(isinstance(m, PublicationMiddleware) for m in factory.call_args.kwargs["middleware"])
    definition.publication = PublicationPolicy(enabled=False)
    await store.upsert_agent("worker", scope, definition.model_dump())
    # Simulates reconstruction from the same run manifest after interruption.
    await create_cognition_agent(params)
    assert not any(isinstance(m, PublicationMiddleware) for m in factory.call_args.kwargs["middleware"])


@pytest.mark.asyncio
async def test_interrupted_graph_rechecks_current_registry_before_publication(tmp_path, monkeypatch):
    from unittest.mock import MagicMock

    from langchain.agents import create_agent
    from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.memory import InMemorySaver

    from server.app.agent.cognition_agent import CognitionContext
    from server.app.agent.publication import PublicationMiddleware
    from server.app.agent.publication_policy import CurrentPublicationPolicy
    from server.app.storage.artifact_store import MemoryArtifactStore, S3ArtifactStore
    from server.app.storage.config_registry import MemoryConfigRegistry
    from server.app.storage.config_store import DefaultConfigStore

    class Model(GenericFakeChatModel):
        def bind_tools(self, tools, **kwargs):
            return self

    scope = {"project": "a"}
    store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
    definition = AgentDefinition(name="worker", system_prompt="Work")
    await store.upsert_agent("worker", scope, definition.model_dump())
    resolver = CurrentPublicationPolicy(store, "worker", tuple(scope.items()), "api",
                                        PublicationLimits(True, 1024, 0))
    artifacts = S3ArtifactStore(MemoryArtifactStore(), bucket="test", base_prefix="test", hmac_key="test")
    objects = MagicMock()
    monkeypatch.setattr(artifacts, "_object_store", lambda: objects)
    sandbox = MagicMock(workspace_root="/workspace")
    sandbox.download_file_bounded.return_value = b"body"
    model = Model(messages=iter([
        AIMessage(content="", tool_calls=[{"name": "publish_artifact", "args": {
            "path": "/workspace/report.txt"}, "id": "publish-1", "type": "tool_call"}]),
        AIMessage(content="Done"),
    ]))
    graph = create_agent(model, middleware=[PublicationMiddleware(
        sandbox, artifacts, scope, policy_resolver=resolver, agent_name="worker"
    )], context_schema=CognitionContext, checkpointer=InMemorySaver(), interrupt_before=["tools"])
    config = {"configurable": {"thread_id": "publication-resume"}}
    context = CognitionContext(effective_scope=scope, agent_name="worker")
    await graph.ainvoke({"messages": [{"role": "user", "content": "Publish"}]}, config, context=context)
    assert (await graph.aget_state(config)).next == ("tools",)
    definition.publication = PublicationPolicy(enabled=False)
    await store.upsert_agent("worker", scope, definition.model_dump())
    with pytest.raises(ValueError, match="publication is disabled"):
        await graph.ainvoke(None, config, context=context)
    sandbox.download_file_bounded.assert_not_called()
    objects.put.assert_not_called()
