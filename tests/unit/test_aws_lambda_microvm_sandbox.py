"""Unit tests for the AWS Lambda MicroVM sandbox backend."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock, patch

from langchain_aws_lambda_microvms import LambdaMicroVmSandbox
from server.app.agent.sandbox_backend import (
    CognitionAwsLambdaMicroVmSandboxBackend,
    create_sandbox_backend,
)
from server.app.storage.config_models import LambdaMicroVmIdlePolicy, SandboxProfile

IMAGE_ARN = "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime"
DEFAULT_ROLE_ARN = "arn:aws:iam::123456789012:role/default-agent-runtime"
EXPLICIT_ROLE_ARN = "arn:aws:iam::123456789012:role/explicit-agent-runtime"
ALL_INGRESS_ARN = (
    "arn:aws:lambda:us-west-2:aws:network-connector:aws-network-connector:ALL_INGRESS"
)
INTERNET_EGRESS_ARN = (
    "arn:aws:lambda:us-west-2:aws:network-connector:aws-network-connector:INTERNET_EGRESS"
)
VPC_EGRESS_ARN = "arn:aws:lambda:us-west-2:123456789012:network-connector:nc-123"


class FakeLambdaMicroVmsClient:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] | None = None
        self.auth_kwargs: dict[str, Any] | None = None
        self.terminated: list[str] = []

    def run_microvm(self, **kwargs: Any) -> dict[str, Any]:
        self.run_kwargs = kwargs
        return {
            "microvmId": "mv-123",
            "state": "RUNNING",
            "endpoint": "mv-123.lambda-url.aws",
            "imageArn": kwargs["imageIdentifier"],
            "imageVersion": kwargs.get("imageVersion", "1.0"),
            "maximumDurationInSeconds": kwargs["maximumDurationInSeconds"],
            "startedAt": "2026-06-26T00:00:00Z",
        }

    def get_microvm(self, **kwargs: Any) -> dict[str, Any]:
        microvm_identifier = str(kwargs["microvmIdentifier"])
        return {
            "microvmId": microvm_identifier,
            "state": "RUNNING",
            "endpoint": "mv-123.lambda-url.aws",
            "imageArn": IMAGE_ARN,
            "imageVersion": "1.0",
            "maximumDurationInSeconds": 3600,
            "startedAt": "2026-06-26T00:00:00Z",
        }

    def create_microvm_auth_token(self, **kwargs: Any) -> dict[str, Any]:
        self.auth_kwargs = kwargs
        return {"authToken": {"X-aws-proxy-auth": "secret-token"}}

    def resume_microvm(self, **kwargs: Any) -> None:
        del kwargs

    def terminate_microvm(self, **kwargs: Any) -> None:
        self.terminated.append(str(kwargs["microvmIdentifier"]))


class FakeHttpResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeHttpClient:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.files: dict[str, bytes] = {}
        self.closed = False

    def request(self, method: str, url: str, **kwargs: Any) -> FakeHttpResponse:
        self.requests.append({"method": method, "url": url, **kwargs})
        path = url.removeprefix("https://mv-123.lambda-url.aws")
        if path == "/healthz":
            return FakeHttpResponse({"status": "ok", "workspace_root": "/workspace"})
        if path == "/execute":
            body = kwargs["json"]
            return FakeHttpResponse(
                {
                    "stdout": f"ran: {body['command'][-1]}",
                    "stderr": "",
                    "exit_code": 0,
                }
            )
        if path == "/upload":
            body = kwargs["json"]
            self.files[str(body["path"])] = base64.b64decode(body["content_base64"])
            return FakeHttpResponse({"status": "ok", "path": body["path"]})
        if path == "/download":
            body = kwargs["json"]
            content = self.files[str(body["path"])]
            return FakeHttpResponse(
                {"path": body["path"], "content_base64": base64.b64encode(content).decode()}
            )
        raise AssertionError(f"unexpected URL: {url}")

    def close(self) -> None:
        self.closed = True


def _profile(
    *,
    egress_mode: str = "internet",
    ingress: list[str] | None = None,
    egress: list[str] | None = None,
) -> SandboxProfile:
    return SandboxProfile(
        name="lambda-default",
        image_arn=IMAGE_ARN,
        image_version="1.0",
        region="us-west-2",
        ingress_network_connector_arns=ingress or [],
        egress_mode=egress_mode,  # type: ignore[arg-type]
        egress_network_connector_arns=egress or [],
        idle_policy=LambdaMicroVmIdlePolicy(
            max_idle_duration_seconds=900,
            suspended_duration_seconds=300,
            auto_resume_enabled=True,
        ),
        maximum_duration_seconds=3600,
        port=8080,
        token_expiration_minutes=17,
        default_execution_role_arn=DEFAULT_ROLE_ARN,
    )


class TestLambdaMicroVmSandboxAdapter:
    def test_execute_launches_microvm_and_calls_runtime_command_server(self) -> None:
        client = FakeLambdaMicroVmsClient()
        http_client = FakeHttpClient()
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            image_version="1.0",
            region_name="us-west-2",
            execution_role_arn=EXPLICIT_ROLE_ARN,
            ingress_network_connector_arns=[ALL_INGRESS_ARN],
            egress_network_connector_arns=[INTERNET_EGRESS_ARN],
            idle_policy={
                "maxIdleDurationSeconds": 900,
                "suspendedDurationSeconds": 300,
                "autoResumeEnabled": True,
            },
            maximum_duration_seconds=3600,
            port=8080,
            token_expiration_minutes=17,
            client=client,
            http_client=http_client,
        )

        result = sandbox.execute("echo hello", timeout=12)

        assert result.output == "ran: echo hello"
        assert result.exit_code == 0
        assert client.run_kwargs is not None
        assert client.run_kwargs["imageIdentifier"] == IMAGE_ARN
        assert client.run_kwargs["imageVersion"] == "1.0"
        assert client.run_kwargs["executionRoleArn"] == EXPLICIT_ROLE_ARN
        assert client.run_kwargs["ingressNetworkConnectors"] == [ALL_INGRESS_ARN]
        assert client.run_kwargs["egressNetworkConnectors"] == [INTERNET_EGRESS_ARN]
        assert client.run_kwargs["idlePolicy"]["autoResumeEnabled"] is True
        assert client.auth_kwargs == {
            "microvmIdentifier": "mv-123",
            "expirationInMinutes": 17,
            "allowedPorts": [{"port": 8080}],
        }

        execute_request = http_client.requests[-1]
        assert execute_request["url"] == "https://mv-123.lambda-url.aws/execute"
        assert execute_request["headers"]["X-aws-proxy-auth"] == "secret-token"
        assert execute_request["headers"]["X-aws-proxy-port"] == "8080"
        assert execute_request["json"]["command"] == ["sh", "-c", "echo hello"]
        assert execute_request["json"]["timeout_seconds"] == 12
        assert "secret-token" not in str(sandbox.runtime_metadata)

    def test_upload_and_download_workspace_paths_use_runtime_file_routes(self) -> None:
        client = FakeLambdaMicroVmsClient()
        http_client = FakeHttpClient()
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            client=client,
            http_client=http_client,
        )

        upload = sandbox.upload_files([("/workspace/src/main.py", b"print(42)")])
        download = sandbox.download_files(["/workspace/src/main.py"])

        assert upload[0].error is None
        assert download[0].content == b"print(42)"
        assert http_client.files == {"src/main.py": b"print(42)"}

    def test_terminate_calls_control_plane_and_clears_runtime_token(self) -> None:
        client = FakeLambdaMicroVmsClient()
        http_client = FakeHttpClient()
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            client=client,
            http_client=http_client,
        )

        sandbox.execute("true")
        sandbox.terminate()

        assert client.terminated == ["mv-123"]
        assert sandbox.runtime_metadata["status"] == "TERMINATED"
        assert "secret-token" not in str(sandbox.runtime_metadata)


class TestCognitionAwsLambdaMicroVmSandboxBackend:
    def test_wrapper_maps_profile_to_reusable_adapter(self, tmp_path) -> None:
        profile = _profile()
        adapter = MagicMock()
        adapter.id = "mv-123"
        adapter.execute.return_value = MagicMock(output="ok", exit_code=0, truncated=False)

        with patch("langchain_aws_lambda_microvms.LambdaMicroVmSandbox", return_value=adapter) as cls:
            backend = CognitionAwsLambdaMicroVmSandboxBackend(
                root_dir=tmp_path,
                sandbox_id="lambda-test",
                profile="lambda-default",
                execution_role_arn=EXPLICIT_ROLE_ARN,
                profile_config=profile,
            )
            result = backend.execute("pwd")

        assert result.output == "ok"
        kwargs = cls.call_args.kwargs
        assert kwargs["image_identifier"] == IMAGE_ARN
        assert kwargs["image_version"] == "1.0"
        assert kwargs["region_name"] == "us-west-2"
        assert kwargs["execution_role_arn"] == EXPLICIT_ROLE_ARN
        assert kwargs["ingress_network_connector_arns"] == [ALL_INGRESS_ARN]
        assert kwargs["egress_network_connector_arns"] == [INTERNET_EGRESS_ARN]
        assert kwargs["idle_policy"] == {
            "maxIdleDurationSeconds": 900,
            "suspendedDurationSeconds": 300,
            "autoResumeEnabled": True,
        }

    def test_wrapper_uses_profile_default_role_when_agent_role_absent(self, tmp_path) -> None:
        profile = _profile(egress_mode="vpc", egress=[VPC_EGRESS_ARN])
        adapter = MagicMock()
        adapter.id = "mv-123"
        adapter.execute.return_value = MagicMock(output="ok", exit_code=0, truncated=False)

        with patch("langchain_aws_lambda_microvms.LambdaMicroVmSandbox", return_value=adapter) as cls:
            backend = CognitionAwsLambdaMicroVmSandboxBackend(
                root_dir=tmp_path,
                profile="lambda-default",
                profile_config=profile,
            )
            backend.execute("pwd")

        kwargs = cls.call_args.kwargs
        assert kwargs["execution_role_arn"] == DEFAULT_ROLE_ARN
        assert kwargs["egress_network_connector_arns"] == [VPC_EGRESS_ARN]

    def test_factory_passes_resolved_profile_config(self, tmp_path) -> None:
        profile = _profile()
        backend = create_sandbox_backend(
            root_dir=tmp_path,
            sandbox_backend="aws_lambda_microvm",
            aws_lambda_microvm_profile="lambda-default",
            aws_lambda_microvm_profile_config=profile,
        )

        assert isinstance(backend, CognitionAwsLambdaMicroVmSandboxBackend)
        assert backend.profile == "lambda-default"
        assert backend.execution_role_arn == DEFAULT_ROLE_ARN

    def test_wrapper_requires_resolved_profile(self, tmp_path) -> None:
        backend = CognitionAwsLambdaMicroVmSandboxBackend(
            root_dir=tmp_path,
            profile="missing-profile",
        )

        try:
            backend.execute("echo hello")
        except RuntimeError as exc:
            assert "SandboxProfile 'missing-profile' was not resolved" in str(exc)
        else:
            raise AssertionError("expected missing SandboxProfile error")
