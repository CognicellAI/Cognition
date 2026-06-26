"""Agent factory tests for Lambda MicroVM SandboxProfile resolution."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from server.app.agent.cognition_agent import CognitionAgentParams, create_cognition_agent
from server.app.agent.sandbox_backend import CognitionAwsLambdaMicroVmSandboxBackend
from server.app.settings import Settings
from server.app.storage.config_models import SandboxProfile
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore

IMAGE_ARN = "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime"
DEFAULT_ROLE_ARN = "arn:aws:iam::123456789012:role/default-agent-runtime"
EXPLICIT_ROLE_ARN = "arn:aws:iam::123456789012:role/explicit-agent-runtime"


def _settings(tmp_path: Path) -> Settings:
    return Settings(
        workspace_path=tmp_path,
        sandbox_backend="aws_lambda_microvm",
        aws_lambda_microvm_default_profile="lambda-default",
    )


def _profile(name: str = "lambda-default") -> SandboxProfile:
    return SandboxProfile(
        name=name,
        image_arn=IMAGE_ARN,
        image_version="1.0",
        region="us-west-2",
        default_execution_role_arn=DEFAULT_ROLE_ARN,
    )


@pytest.mark.asyncio
async def test_create_agent_resolves_lambda_microvm_profile_from_config_store(
    tmp_path: Path,
) -> None:
    store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)
    await store.upsert_sandbox_profile(_profile())

    with patch("server.app.agent.cognition_agent.create_deep_agent", return_value=MagicMock()):
        result = await create_cognition_agent(
            CognitionAgentParams(
                project_path=tmp_path,
                model=MagicMock(),
                store=MagicMock(),
                checkpointer=MagicMock(),
                settings=_settings(tmp_path),
                config_store=store,
                sandbox_profile="lambda-default",
                sandbox_execution_role_arn=EXPLICIT_ROLE_ARN,
            )
        )

    backend = result.sandbox_backend
    assert isinstance(backend, CognitionAwsLambdaMicroVmSandboxBackend)
    assert backend.profile == "lambda-default"
    assert backend.execution_role_arn == EXPLICIT_ROLE_ARN
    assert backend.runtime_metadata["image"] == IMAGE_ARN
    assert backend.runtime_metadata["image_version"] == "1.0"


@pytest.mark.asyncio
async def test_create_agent_fails_when_lambda_microvm_profile_is_missing(
    tmp_path: Path,
) -> None:
    store = DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path)

    with pytest.raises(RuntimeError, match="SandboxProfile 'missing-profile' was not found"):
        await create_cognition_agent(
            CognitionAgentParams(
                project_path=tmp_path,
                model=MagicMock(),
                store=MagicMock(),
                checkpointer=MagicMock(),
                settings=_settings(tmp_path),
                config_store=store,
                sandbox_profile="missing-profile",
            )
        )
