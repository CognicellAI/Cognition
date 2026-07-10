"""Capability discovery and compatibility reporting.

Exposes the deployment's runtime feature set so builders and clients can
adapt their behaviour without guessing or parsing opaque error messages.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter, Depends

from server.app.api.dependencies import get_settings_dep
from server.app.api.models import CapabilityResponse, VersionInfo
from server.app.settings import Settings

router = APIRouter(prefix="/capabilities", tags=["capabilities"])


def _get_version(name: str) -> str:
    try:
        return version(name)
    except PackageNotFoundError:
        return "unknown"


@router.get("", response_model=CapabilityResponse)
async def get_capabilities(
    settings: Settings = Depends(get_settings_dep),  # noqa: B008
) -> CapabilityResponse:
    """Return deployment capability and version report.

    Includes installed runtime package versions, supported stream protocols,
    available sandbox backends, feature flags, middleware types, and scope
    configuration.
    """
    stream_protocols = ["sse"]

    sandbox_backends = ["local"]
    if settings.docker_image:
        sandbox_backends.append("docker")
    sandbox_backends.append("kubernetes")
    sandbox_backends.append("aws_lambda_microvm")

    a2a_enabled = settings.a2a_enabled

    features: dict[str, bool] = {
        "async_subagents": True,
        "mcp": True,
        "mcp_tool_name_prefix": True,
        "hitl": True,
        "permissions": True,
        "artifacts": True,
        "context_policy": True,
        "context_controls": True,
        "tool_safety": True,
        "tool_argument_validation": True,
        "trusted_runtime_context": True,
        "checkpoint_apis": True,
        "scope_propagation": True,
        "provider_config_crud": True,
        "agent_config_crud": True,
        "sandbox_profile_crud": True,
        "aws_lambda_microvm_sandbox": True,
        "model_catalog": True,
        "a2a": a2a_enabled,
        "a2a_jsonrpc": a2a_enabled,
        "a2a_streaming": a2a_enabled,
        "a2a_per_agent_cards": a2a_enabled,
        "a2a_push_notifications": False,
        "a2a_grpc": False,
    }

    middleware = [
        "ToolSecurityMiddleware",
        "ToolArgumentValidationMiddleware",
        "TrustedRuntimeContextMiddleware",
        "CognitionObservabilityMiddleware",
        "CognitionStreamingMiddleware",
        "HumanInTheLoopMiddleware",
        "FilesystemMiddleware",
        "MemoryMiddleware",
        "SummarizationToolMiddleware",
    ]

    return CapabilityResponse(
        versions=VersionInfo(
            cognition=_get_version("cognition"),
            deepagents=_get_version("deepagents"),
            langgraph=_get_version("langgraph"),
            langchain=_get_version("langchain"),
            langchain_core=_get_version("langchain-core"),
        ),
        stream_protocols=stream_protocols,
        sandbox_backends=sandbox_backends,
        features=features,
        middleware=middleware,
        scope_keys=list(settings.scope_keys),
        deployment={
            "sandbox_backend": settings.sandbox_backend,
        },
    )
