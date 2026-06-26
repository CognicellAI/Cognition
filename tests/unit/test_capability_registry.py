"""Unit tests for the capability registry endpoint."""

from __future__ import annotations

import pytest

from server.app.api.models import CapabilityResponse, VersionInfo
from server.app.api.routes.capabilities import _get_version, get_capabilities
from server.app.settings import Settings


class TestCapabilityVersions:
    def test_known_package_versions_resolve(self):
        for pkg in ("deepagents", "langgraph", "langchain", "langchain-core"):
            v = _get_version(pkg)
            assert v != "unknown", f"Expected known version for {pkg}"

    def test_unknown_package_returns_unknown(self):
        assert _get_version("nonexistent-package-12345") == "unknown"


@pytest.mark.asyncio
class TestCapabilityEndpoint:
    async def test_returns_all_version_fields(self):
        settings = Settings()
        result = await get_capabilities(settings)
        assert isinstance(result, CapabilityResponse)
        v = result.versions
        assert isinstance(v, VersionInfo)
        assert v.cognition == _get_version("cognition")
        assert v.deepagents == _get_version("deepagents")
        assert v.langgraph == _get_version("langgraph")
        assert v.langchain == _get_version("langchain")
        assert v.langchain_core == _get_version("langchain-core")

    async def test_stream_protocols_includes_sse(self):
        settings = Settings()
        result = await get_capabilities(settings)
        assert "sse" in result.stream_protocols

    async def test_sandbox_backends_includes_local(self):
        settings = Settings()
        result = await get_capabilities(settings)
        assert "local" in result.sandbox_backends
        assert "aws_lambda_microvm" in result.sandbox_backends

    async def test_features_include_core_v010_flags(self):
        settings = Settings()
        result = await get_capabilities(settings)
        assert result.features["mcp"] is True
        assert result.features["hitl"] is True
        assert result.features["permissions"] is True
        assert result.features["artifacts"] is True
        assert result.features["context_policy"] is True
        assert result.features["tool_safety"] is True
        assert result.features["scope_propagation"] is True
        assert result.features["async_subagents"] is True
        assert result.features["sandbox_profile_crud"] is True
        assert result.features["aws_lambda_microvm_sandbox"] is True

    async def test_features_do_not_include_deferred_items(self):
        settings = Settings()
        result = await get_capabilities(settings)
        assert "eval_harness" not in result.features
        assert "code_interpreter" not in result.features
        assert "scoped_memory" not in result.features

    async def test_middleware_includes_required_names(self):
        settings = Settings()
        result = await get_capabilities(settings)
        assert "ToolSecurityMiddleware" in result.middleware
        assert "HumanInTheLoopMiddleware" in result.middleware
        assert "FilesystemMiddleware" in result.middleware

    async def test_scope_keys_from_settings(self):
        settings = Settings(scope_keys=["user", "project"])
        result = await get_capabilities(settings)
        assert result.scope_keys == ["user", "project"]

    async def test_deployment_includes_sandbox_backend(self):
        settings = Settings(sandbox_backend="local")
        result = await get_capabilities(settings)
        assert result.deployment["sandbox_backend"] == "local"
