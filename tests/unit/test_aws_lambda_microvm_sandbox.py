"""Unit tests for the AWS Lambda MicroVM sandbox backend."""

from __future__ import annotations

import base64
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from langchain_aws_lambda_microvms import LambdaMicroVmSandbox, MicroVmQuotaExceededError
from server.app.agent.sandbox_backend import (
    CognitionAwsLambdaMicroVmSandboxBackend,
    create_sandbox_backend,
)
from server.app.storage.config_models import (
    LambdaMicroVmIdlePolicy,
    LambdaMicroVmLogging,
    LambdaMicroVmQuota,
    SandboxProfile,
)

IMAGE_ARN = "arn:aws:lambda:us-west-2:123456789012:microvm-image:cognition-runtime"
DEFAULT_ROLE_ARN = "arn:aws:iam::123456789012:role/default-agent-runtime"
EXPLICIT_ROLE_ARN = "arn:aws:iam::123456789012:role/explicit-agent-runtime"
ALL_INGRESS_ARN = "arn:aws:lambda:us-west-2:aws:network-connector:aws-network-connector:ALL_INGRESS"
INTERNET_EGRESS_ARN = (
    "arn:aws:lambda:us-west-2:aws:network-connector:aws-network-connector:INTERNET_EGRESS"
)
VPC_EGRESS_ARN = "arn:aws:lambda:us-west-2:123456789012:network-connector:nc-123"


def test_failed_readiness_retries_same_allocation():
    client = FakeLambdaMicroVmsClient()
    sandbox = LambdaMicroVmSandbox(image_identifier=IMAGE_ARN, client=client, http_client=FakeHttpClient())
    with (
        patch.object(client, "run_microvm", wraps=client.run_microvm) as launch,
        patch.object(sandbox, "_healthcheck", side_effect=[TimeoutError("not ready"), None]) as ready,
    ):
        with pytest.raises(TimeoutError):
            sandbox.execute("true")
        sandbox.execute("true")
    launch.assert_called_once()
    assert ready.call_count == 2


@pytest.mark.parametrize("launched", [False, True])
def test_released_sdk_cannot_launch_or_execute_again(launched):
    client = FakeLambdaMicroVmsClient()
    sandbox = LambdaMicroVmSandbox(image_identifier=IMAGE_ARN, client=client, http_client=FakeHttpClient())
    with patch.object(client, "run_microvm", wraps=client.run_microvm) as launch:
        if launched:
            sandbox.execute("true")
        sandbox.terminate()
        with pytest.raises(RuntimeError, match="released"):
            sandbox.execute("true")
        assert launch.call_count == int(launched)


def test_wrapper_retains_failed_sdk_for_teardown_retry():
    client = FakeLambdaMicroVmsClient()
    sdk = LambdaMicroVmSandbox(image_identifier=IMAGE_ARN, client=client, http_client=FakeHttpClient())
    wrapper = CognitionAwsLambdaMicroVmSandboxBackend("/tmp", profile_config=_profile())
    with patch("langchain_aws_lambda_microvms.LambdaMicroVmSandbox", return_value=sdk):
        wrapper.execute("true")
    client.terminate_error = RuntimeError("unavailable")
    wrapper.terminate()
    assert wrapper._backend is sdk
    with pytest.raises(RuntimeError, match="released"):
        wrapper.execute("true")
    client.terminate_error = None
    wrapper.terminate()
    assert wrapper._backend is None
    assert wrapper.runtime_metadata["teardown_status"] == "complete"


def test_parallel_first_operations_launch_only_one_microvm() -> None:
    """Parallel Deep Agents tools must share one lazily initialized SDK backend."""
    client = FakeLambdaMicroVmsClient()
    http = FakeHttpClient()
    backend = CognitionAwsLambdaMicroVmSandboxBackend("/tmp", profile_config=_profile())
    start = threading.Barrier(3)

    def construct(**kwargs: Any) -> LambdaMicroVmSandbox:
        # HTTP client initialization releases the GIL in a real SDK constructor.
        time.sleep(0.1)
        return LambdaMicroVmSandbox(**kwargs, client=client, http_client=http)

    def execute(_: int) -> int | None:
        start.wait(timeout=5)
        return backend.execute("printf ready").exit_code

    with (
        patch(
            "langchain_aws_lambda_microvms.LambdaMicroVmSandbox", side_effect=construct
        ) as factory,
        patch.object(client, "run_microvm", wraps=client.run_microvm) as launch,
        ThreadPoolExecutor(max_workers=3) as pool,
    ):
        assert list(pool.map(execute, range(3))) == [0, 0, 0]
        assert factory.call_count == 1
        assert launch.call_count == 1


class FakeLambdaMicroVmsClient:
    def __init__(self) -> None:
        self.run_kwargs: dict[str, Any] | None = None
        self.auth_kwargs: dict[str, Any] | None = None
        self.terminated: list[str] = []
        self.get_states: list[str] = []
        self.get_error: Exception | None = None
        self.terminate_error: Exception | None = None

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
        if self.get_error is not None:
            raise self.get_error
        if self.get_states:
            state = self.get_states.pop(0)
        elif microvm_identifier in self.terminated:
            state = "TERMINATED"
        else:
            state = "RUNNING"
        return {
            "microvmId": microvm_identifier,
            "state": state,
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
        if self.terminate_error is not None:
            raise self.terminate_error
        self.terminated.append(str(kwargs["microvmIdentifier"]))


class FakeAwsError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


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
        if path == "/run":
            return FakeHttpResponse({"status": "ok"})
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
        logging=LambdaMicroVmLogging(disabled={}),
        quota=LambdaMicroVmQuota(
            max_concurrent_sessions=2,
            max_session_starts_per_minute=10,
        ),
        run_hook_payload='{"workspace":"/workspace"}',
        maximum_duration_seconds=3600,
        port=8080,
        token_expiration_minutes=17,
        default_execution_role_arn=DEFAULT_ROLE_ARN,
    )


class TestLambdaMicroVmSandboxAdapter:
    def test_throttled_launch_retries_with_backoff(self) -> None:
        client = FakeLambdaMicroVmsClient()
        original = client.run_microvm
        attempts = 0

        def throttled_once(**kwargs):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise FakeAwsError("ThrottlingException")
            return original(**kwargs)

        client.run_microvm = MagicMock(side_effect=throttled_once)
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            client=client,
            http_client=FakeHttpClient(),
        )

        with patch("langchain_aws_lambda_microvms.sandbox.time.sleep") as sleep:
            assert sandbox.execute("true").exit_code == 0

        assert client.run_microvm.call_count == 2
        sleep.assert_called_once()
        assert "launch_backoff" in sandbox.runtime_metadata["lifecycle_phases"]

    def test_quota_rejection_is_typed_and_not_retried(self) -> None:
        client = FakeLambdaMicroVmsClient()
        client.run_microvm = MagicMock(side_effect=FakeAwsError("ServiceQuotaExceededException"))
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            client=client,
            http_client=FakeHttpClient(),
        )

        with pytest.raises(MicroVmQuotaExceededError):
            sandbox.execute("true")

        assert client.run_microvm.call_count == 1
        assert "launch_quota_rejected" in sandbox.runtime_metadata["lifecycle_phases"]

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
            logging_config={
                "cloudWatch": {
                    "logGroup": "/aws/lambda-microvms/cognition",
                    "logStream": "test-stream",
                }
            },
            run_hook_payload='{"workspace":"/workspace"}',
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
        assert client.run_kwargs["logging"] == {
            "cloudWatch": {
                "logGroup": "/aws/lambda-microvms/cognition",
                "logStream": "test-stream",
            }
        }
        assert client.run_kwargs["runHookPayload"] == '{"workspace":"/workspace"}'
        assert client.auth_kwargs == {
            "microvmIdentifier": "mv-123",
            "expirationInMinutes": 17,
            "allowedPorts": [{"port": 8080}],
        }
        metadata = sandbox.runtime_metadata
        assert metadata["maximum_duration_seconds"] == 3600
        assert metadata["token_expiration_minutes"] == 17
        assert metadata["logging_mode"] == "cloud_watch"
        assert metadata["aws_state"] == "RUNNING"
        assert metadata["launch_duration_ms"] >= 0
        assert metadata["healthcheck_duration_ms"] >= 0
        assert metadata["lifecycle_phases"] == [
            "launch_started",
            "launch_running",
            "auth_token_created",
            "runtime_healthcheck_started",
            "runtime_healthcheck_passed",
        ]

        execute_request = http_client.requests[-1]
        assert execute_request["url"] == "https://mv-123.lambda-url.aws/execute"
        assert execute_request["headers"]["X-aws-proxy-auth"] == "secret-token"
        assert execute_request["headers"]["X-aws-proxy-port"] == "8080"
        assert execute_request["json"]["command"] == ["sh", "-c", "echo hello"]
        assert execute_request["json"]["timeout_seconds"] == 12
        assert "secret-token" not in str(sandbox.runtime_metadata)

    def test_execute_omits_execution_role_when_not_configured(self) -> None:
        client = FakeLambdaMicroVmsClient()
        http_client = FakeHttpClient()
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            client=client,
            http_client=http_client,
        )

        sandbox.execute("echo hello")

        assert client.run_kwargs is not None
        assert "executionRoleArn" not in client.run_kwargs

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
        assert sandbox.runtime_metadata["aws_state"] == "TERMINATED"
        assert sandbox.runtime_metadata["teardown_status"] == "complete"
        assert sandbox.runtime_metadata["teardown_attempt"] == 1
        assert sandbox.runtime_metadata["teardown_duration_ms"] >= 0
        assert "teardown_error_code" not in sandbox.runtime_metadata
        assert "secret-token" not in str(sandbox.runtime_metadata)

    def test_terminate_reports_pending_when_aws_does_not_confirm_terminal(self) -> None:
        client = FakeLambdaMicroVmsClient()
        client.get_states = ["RUNNING"]
        http_client = FakeHttpClient()
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            client=client,
            http_client=http_client,
            teardown_timeout_seconds=0,
            teardown_poll_interval_seconds=0,
        )

        sandbox.execute("true")
        sandbox.terminate()

        assert client.terminated == ["mv-123"]
        assert sandbox.runtime_metadata["status"] == "RUNNING"
        assert sandbox.runtime_metadata["aws_state"] == "RUNNING"
        assert sandbox.runtime_metadata["teardown_status"] == "pending"
        assert "teardown_pending" in sandbox.runtime_metadata["lifecycle_phases"]
        assert "secret-token" not in str(sandbox.runtime_metadata)

    def test_terminate_reports_failed_on_control_plane_error(self) -> None:
        client = FakeLambdaMicroVmsClient()
        client.terminate_error = RuntimeError("boom")
        http_client = FakeHttpClient()
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            client=client,
            http_client=http_client,
        )

        sandbox.execute("true")
        sandbox.terminate()

        assert client.terminated == []
        assert sandbox.runtime_metadata["status"] == "RUNNING"
        assert sandbox.runtime_metadata["teardown_status"] == "failed"
        assert sandbox.runtime_metadata["teardown_error_code"] == "RuntimeError"
        assert sandbox.runtime_metadata["teardown_error_message"] == "boom"
        assert "teardown_failed" in sandbox.runtime_metadata["lifecycle_phases"]
        assert "secret-token" not in str(sandbox.runtime_metadata)

    def test_terminate_is_idempotent_after_verified_terminal_state(self) -> None:
        client = FakeLambdaMicroVmsClient()
        http_client = FakeHttpClient()
        sandbox = LambdaMicroVmSandbox(
            image_identifier=IMAGE_ARN,
            client=client,
            http_client=http_client,
        )

        sandbox.execute("true")
        sandbox.terminate()
        sandbox.terminate()

        assert client.terminated == ["mv-123"]
        assert sandbox.runtime_metadata["status"] == "TERMINATED"
        assert sandbox.runtime_metadata["teardown_status"] == "complete"
        assert sandbox.runtime_metadata["teardown_attempt"] == 2


class TestCognitionAwsLambdaMicroVmSandboxBackend:
    def test_wrapper_maps_profile_to_reusable_adapter(self, tmp_path) -> None:
        profile = _profile()
        adapter = MagicMock()
        adapter.id = "mv-123"
        adapter.execute.return_value = MagicMock(output="ok", exit_code=0, truncated=False)

        with patch(
            "langchain_aws_lambda_microvms.LambdaMicroVmSandbox", return_value=adapter
        ) as cls:
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
        assert kwargs["logging_config"] == {"disabled": {}}
        assert kwargs["run_hook_payload"] == '{"workspace":"/workspace"}'

    def test_wrapper_uses_profile_default_role_when_agent_role_absent(self, tmp_path) -> None:
        profile = _profile(egress_mode="vpc", egress=[VPC_EGRESS_ARN])
        adapter = MagicMock()
        adapter.id = "mv-123"
        adapter.execute.return_value = MagicMock(output="ok", exit_code=0, truncated=False)

        with patch(
            "langchain_aws_lambda_microvms.LambdaMicroVmSandbox", return_value=adapter
        ) as cls:
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
        assert backend.quota == profile.quota
        assert backend.runtime_metadata["quota"] == {
            "max_concurrent_sessions": 2,
            "max_session_starts_per_minute": 10,
        }

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


def test_transient_initializer_precedes_commands_and_is_not_provider_metadata(caplog):
    client = FakeLambdaMicroVmsClient()
    http = FakeHttpClient()
    initializer = MagicMock(return_value={"bootstrap": "transient-canary"})
    sandbox = LambdaMicroVmSandbox(
        image_identifier=IMAGE_ARN, client=client, http_client=http,
        run_hook_payload='{"mode":"waiting"}', runtime_initializer=initializer,
    )
    original_request = http.request

    def request(method, url, **kwargs):
        if url.endswith("/run"):
            http.requests.append({"method": method, "url": url, **kwargs})
            return FakeHttpResponse({"status": "ok"})
        return original_request(method, url, **kwargs)

    with patch.object(http, "request", side_effect=request):
        sandbox.execute("true")
        sandbox.execute("true")
    initializer.assert_called_once_with(sandbox.id)
    assert [r["url"].rsplit("/", 1)[-1] for r in http.requests] == [
        "run", "healthz", "execute", "execute",
    ]
    assert json.loads(http.requests[0]["json"]["runHookPayload"]) == {
        "bootstrap": "transient-canary",
    }
    assert "transient-canary" not in repr(client.run_kwargs)
    assert "transient-canary" not in repr(sandbox.runtime_metadata)
    assert "transient-canary" not in caplog.text
    assert sandbox._runtime_initializer is not None


@pytest.mark.parametrize("failure", ["callback", "oversized", "transport"])
def test_initialization_failure_closes_allocation_and_redacts_payload(failure, caplog):
    client = FakeLambdaMicroVmsClient()
    http = FakeHttpClient()
    initializer = MagicMock(return_value={"bootstrap": "transient-canary"})
    if failure == "callback":
        initializer.side_effect = RuntimeError("transient-canary")
    elif failure == "oversized":
        initializer.return_value = {"bootstrap": "transient-canary" * 2000}
    sandbox = LambdaMicroVmSandbox(
        image_identifier=IMAGE_ARN, client=client, http_client=http,
        runtime_initializer=initializer,
    )
    with patch.object(http, "request", side_effect=RuntimeError("transient-canary")) as request:
        with pytest.raises(RuntimeError, match="runtime initialization failed") as error:
            sandbox.execute("true")
        assert "transient-canary" not in str(error.value)
        with pytest.raises(RuntimeError, match="released"):
            sandbox.execute("true")
        assert request.call_count == int(failure == "transport")
    initializer.assert_called_once()
    assert client.terminated == [sandbox.id]
    assert sandbox.runtime_metadata["teardown_status"] == "complete"
    assert "transient-canary" not in repr(sandbox.runtime_metadata)
    assert "transient-canary" not in caplog.text


def test_initializer_error_does_not_leak_when_teardown_also_fails(caplog):
    client = FakeLambdaMicroVmsClient()
    client.terminate_error = RuntimeError("provider unavailable")
    sandbox = LambdaMicroVmSandbox(
        image_identifier=IMAGE_ARN, client=client, http_client=FakeHttpClient(),
        runtime_initializer=MagicMock(side_effect=RuntimeError("transient-canary")),
    )
    with pytest.raises(RuntimeError, match="runtime initialization failed"):
        sandbox.execute("true")
    assert "transient-canary" not in caplog.text
    assert sandbox.runtime_metadata["teardown_status"] == "failed"
    client.terminate_error = None
    sandbox.terminate()
    assert sandbox.runtime_metadata["teardown_status"] == "complete"


def test_successful_initialization_is_not_repeated_after_readiness_timeout():
    client = FakeLambdaMicroVmsClient()
    initializer = MagicMock(return_value={"setup": "one-time"})
    sandbox = LambdaMicroVmSandbox(
        image_identifier=IMAGE_ARN, client=client, http_client=FakeHttpClient(),
        runtime_initializer=initializer,
    )
    with (
        patch.object(sandbox, "_runtime_request", return_value={"exit_code": 0}) as request,
        patch.object(sandbox, "_healthcheck", side_effect=[TimeoutError("not ready"), None]),
    ):
        with pytest.raises(TimeoutError):
            sandbox.execute("true")
        sandbox.execute("true")
    initializer.assert_called_once()
    assert [call.args[1] for call in request.call_args_list] == ["/run", "/execute"]


def test_lost_endpoint_keeps_live_lease_and_does_not_duplicate_workspace_writer():
    client = FakeLambdaMicroVmsClient()
    http = FakeHttpClient()
    sandbox = LambdaMicroVmSandbox(image_identifier=IMAGE_ARN, client=client, http_client=http)
    original_request = http.request
    failed = False

    def request(method, url, **kwargs):
        nonlocal failed
        if url.endswith("/execute") and not failed:
            failed = True
            response = httpx.Response(502, request=httpx.Request(method, url))
            raise httpx.HTTPStatusError("stale endpoint", request=response.request, response=response)
        return original_request(method, url, **kwargs)

    with patch.object(http, "request", side_effect=request), patch.object(
        client, "run_microvm", wraps=client.run_microvm
    ) as launch:
        first = sandbox.execute("true")
        second = sandbox.execute("true")

    assert first.exit_code == -1
    assert second.exit_code == 0
    assert launch.call_count == 1
    assert sandbox.runtime_metadata["recovery_generation"] == 0
    assert "sandbox_suspect" in sandbox.runtime_metadata["lifecycle_phases"]


def test_lost_endpoint_replaces_only_after_provider_confirms_absence():
    client = FakeLambdaMicroVmsClient()
    http = FakeHttpClient()
    sandbox = LambdaMicroVmSandbox(image_identifier=IMAGE_ARN, client=client, http_client=http)
    original_request = http.request
    failed = False

    def request(method, url, **kwargs):
        nonlocal failed
        if url.endswith("/execute") and not failed:
            failed = True
            response = httpx.Response(502, request=httpx.Request(method, url))
            raise httpx.HTTPStatusError("stale endpoint", request=response.request, response=response)
        return original_request(method, url, **kwargs)

    with patch.object(http, "request", side_effect=request), patch.object(
        client, "run_microvm", wraps=client.run_microvm
    ) as launch:
        first = sandbox.execute("true")
        client.get_error = FakeAwsError("ResourceNotFoundException")
        second = sandbox.execute("true")

    assert first.exit_code == -1
    assert second.exit_code == 0
    assert launch.call_count == 2
    assert sandbox.runtime_metadata["recovery_generation"] == 1
    assert "sandbox_lost" in sandbox.runtime_metadata["lifecycle_phases"]


def test_resume_reinitializes_external_runtime_credentials():
    client = FakeLambdaMicroVmsClient()
    http = FakeHttpClient()
    initializer = MagicMock(return_value={"credential": "fresh"})
    sandbox = LambdaMicroVmSandbox(
        image_identifier=IMAGE_ARN,
        client=client,
        http_client=http,
        runtime_initializer=initializer,
    )
    sandbox.execute("true")
    sandbox._state = "SUSPENDED"
    sandbox._ready = False

    sandbox.execute("true")

    assert initializer.call_count == 2
    assert "resume_started" in sandbox.runtime_metadata["lifecycle_phases"]
    assert "resume_running" in sandbox.runtime_metadata["lifecycle_phases"]
