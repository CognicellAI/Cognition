"""AWS Lambda MicroVM sandbox backend for Deep Agents.

The backend owns the Lambda MicroVM lifecycle and talks to a command server
inside the MicroVM through the AWS proxy endpoint. Runtime auth tokens are kept
in memory only and are never returned in metadata.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import shlex
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from typing import Any, Literal

import httpx
from deepagents.backends.protocol import (
    ExecuteResponse,
    FileDownloadResponse,
    FileUploadResponse,
)
from deepagents.backends.sandbox import BaseSandbox

logger = logging.getLogger(__name__)

DEFAULT_COMMAND_TIMEOUT_SECONDS = 300
DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS = 60
DEFAULT_TEARDOWN_TIMEOUT_SECONDS = 5.0
DEFAULT_TEARDOWN_POLL_INTERVAL_SECONDS = 1.0
RUNNING_STATE = "RUNNING"
SUSPENDED_STATE = "SUSPENDED"
TERMINATED_STATE = "TERMINATED"
TERMINAL_STATES = {TERMINATED_STATE}
AUTH_HEADER_NAME = "X-aws-proxy-auth"
PORT_HEADER_NAME = "X-aws-proxy-port"
FileTransferError = Literal[
    "file_not_found",
    "permission_denied",
    "is_directory",
    "invalid_path",
]


def _role_fingerprint(role_arn: str | None) -> str | None:
    if not role_arn:
        return None
    return hashlib.sha256(role_arn.encode("utf-8")).hexdigest()[:16]


def _response_text(stdout: str, stderr: str) -> str:
    if stdout and stderr:
        return f"{stdout}\n{stderr}"
    return stdout or stderr


def _file_error_from_payload(payload: Mapping[str, Any]) -> FileTransferError:
    error = str(payload.get("error", "")).lower()
    message = str(payload.get("message", "")).lower()
    detail = f"{error} {message}"
    if "notfound" in detail or "no such file" in detail:
        return "file_not_found"
    if "permission" in detail or "access" in detail:
        return "permission_denied"
    if "directory" in detail:
        return "is_directory"
    return "invalid_path"


class LambdaMicroVmSandbox(BaseSandbox):
    """Deep Agents sandbox backend backed by AWS Lambda MicroVMs.

    Args:
        image_identifier: Lambda MicroVM image ARN or ID.
        image_version: Optional image version.
        region_name: AWS region used for the Lambda MicroVM client.
        execution_role_arn: Optional IAM role assigned to the MicroVM runtime.
        ingress_network_connector_arns: Ingress network connector ARNs.
        egress_network_connector_arns: Egress network connector ARNs.
        idle_policy: Optional Lambda MicroVM idle policy dict.
        logging_config: Optional Lambda MicroVM logging union dict.
        run_hook_payload: Optional payload for the MicroVM /run lifecycle hook.
        maximum_duration_seconds: Maximum MicroVM lifetime.
        port: Command server port inside the MicroVM.
        token_expiration_minutes: Proxy auth token lifetime.
        workspace_root: Runtime workspace root used by the command server.
        sandbox_id: Stable local identifier before AWS returns a MicroVM id.
        launch_timeout_seconds: Maximum time to wait for RUNNING state.
        healthcheck_timeout_seconds: Maximum time to wait for /healthz.
        teardown_timeout_seconds: Maximum time to wait for TERMINATED state.
        teardown_poll_interval_seconds: Time between GetMicrovm teardown polls.
        client: Optional prebuilt boto3 lambda-microvms client, for tests.
        client_factory: Optional boto3 client factory, for tests.
        http_client: Optional sync httpx-compatible client, for tests.
    """

    def __init__(
        self,
        *,
        image_identifier: str,
        image_version: str | None = None,
        region_name: str | None = None,
        execution_role_arn: str | None = None,
        ingress_network_connector_arns: list[str] | None = None,
        egress_network_connector_arns: list[str] | None = None,
        idle_policy: dict[str, Any] | None = None,
        logging_config: dict[str, Any] | None = None,
        run_hook_payload: str | None = None,
        maximum_duration_seconds: int = 3600,
        port: int = 8080,
        token_expiration_minutes: int = 30,
        workspace_root: str = "/workspace",
        sandbox_id: str | None = None,
        launch_timeout_seconds: int = 120,
        healthcheck_timeout_seconds: int = DEFAULT_HEALTHCHECK_TIMEOUT_SECONDS,
        teardown_timeout_seconds: float = DEFAULT_TEARDOWN_TIMEOUT_SECONDS,
        teardown_poll_interval_seconds: float = DEFAULT_TEARDOWN_POLL_INTERVAL_SECONDS,
        client: Any | None = None,
        client_factory: Callable[..., Any] | None = None,
        http_client: Any | None = None,
    ) -> None:
        self._image_identifier = image_identifier
        self._image_version = image_version
        self._region_name = region_name
        self._execution_role_arn = execution_role_arn
        self._ingress_network_connector_arns = list(ingress_network_connector_arns or [])
        self._egress_network_connector_arns = list(egress_network_connector_arns or [])
        self._idle_policy = dict(idle_policy or {}) or None
        self._logging_config = dict(logging_config or {}) or None
        self._run_hook_payload = run_hook_payload
        self._maximum_duration_seconds = maximum_duration_seconds
        self._port = port
        self._token_expiration_minutes = token_expiration_minutes
        self._workspace_root = workspace_root.rstrip("/") or "/workspace"
        self._sandbox_id = sandbox_id or f"lambda-microvm-{id(self):x}"
        self._launch_timeout_seconds = launch_timeout_seconds
        self._healthcheck_timeout_seconds = healthcheck_timeout_seconds
        self._teardown_timeout_seconds = teardown_timeout_seconds
        self._teardown_poll_interval_seconds = teardown_poll_interval_seconds
        self._client = client
        self._client_factory = client_factory
        self._http_client: Any | None = http_client or httpx.Client()
        self._owns_http_client = http_client is None

        self._lock = threading.Lock()
        self._microvm_id: str | None = None
        self._endpoint: str | None = None
        self._state: str | None = None
        self._auth_headers: dict[str, str] | None = None
        self._created_client_token: str | None = None
        self._image_arn: str | None = None
        self._resolved_image_version: str | None = image_version
        self._launch_duration_ms: float | None = None
        self._healthcheck_duration_ms: float | None = None
        self._teardown_duration_ms: float | None = None
        self._teardown_status: str | None = None
        self._teardown_attempt = 0
        self._teardown_error_code: str | None = None
        self._teardown_error_message: str | None = None
        self._lifecycle_phases: list[str] = []

    @property
    def id(self) -> str:
        """Return the AWS MicroVM id if provisioned, otherwise the local id."""
        return self._microvm_id or self._sandbox_id

    @property
    def runtime_metadata(self) -> dict[str, Any]:
        """Return token-free runtime metadata suitable for logs or persistence."""
        metadata: dict[str, Any] = {
            "microvm_id": self._microvm_id,
            "endpoint": self._endpoint,
            "image": self._image_arn or self._image_identifier,
            "image_version": self._resolved_image_version,
            "status": self._state,
            "aws_state": self._state,
            "execution_role_fingerprint": _role_fingerprint(self._execution_role_arn),
            "region": self._region_name,
            "port": self._port,
            "maximum_duration_seconds": self._maximum_duration_seconds,
            "token_expiration_minutes": self._token_expiration_minutes,
            "ingress_network_connector_count": len(self._ingress_network_connector_arns),
            "egress_network_connector_count": len(self._egress_network_connector_arns),
            "logging_mode": self._logging_mode(),
            "lifecycle_phases": list(self._lifecycle_phases),
        }
        optional = {
            "launch_duration_ms": self._launch_duration_ms,
            "healthcheck_duration_ms": self._healthcheck_duration_ms,
            "teardown_duration_ms": self._teardown_duration_ms,
            "teardown_status": self._teardown_status,
            "teardown_attempt": self._teardown_attempt or None,
            "teardown_error_code": self._teardown_error_code,
            "teardown_error_message": self._teardown_error_message,
        }
        metadata.update({key: value for key, value in optional.items() if value is not None})
        return metadata

    def _record_lifecycle_phase(self, phase: str) -> None:
        if phase not in self._lifecycle_phases:
            self._lifecycle_phases.append(phase)

    def _logging_mode(self) -> str | None:
        if self._logging_config is None:
            return None
        if "disabled" in self._logging_config:
            return "disabled"
        if "cloudWatch" in self._logging_config or "cloud_watch" in self._logging_config:
            return "cloud_watch"
        return "custom"

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        try:
            import boto3
        except ImportError as e:
            raise RuntimeError(
                "boto3>=1.43.36 is required for LambdaMicroVmSandbox. "
                "Install cognition[aws-lambda-microvms] or "
                "langchain-aws-lambda-microvms[aws]."
            ) from e
        factory = self._client_factory or boto3.client
        try:
            self._client = factory("lambda-microvms", region_name=self._region_name)
        except Exception as e:
            raise RuntimeError(
                "Could not create AWS lambda-microvms client. Ensure boto3/botocore "
                "support the Lambda MicroVM APIs and AWS credentials are configured."
            ) from e
        return self._client

    def _run_request(self) -> dict[str, Any]:
        request: dict[str, Any] = {
            "imageIdentifier": self._image_identifier,
            "maximumDurationInSeconds": self._maximum_duration_seconds,
            "clientToken": self._created_client_token or f"{self._sandbox_id}-{uuid.uuid4()}",
        }
        self._created_client_token = str(request["clientToken"])
        if self._image_version:
            request["imageVersion"] = self._image_version
        if self._execution_role_arn:
            request["executionRoleArn"] = self._execution_role_arn
        if self._ingress_network_connector_arns:
            request["ingressNetworkConnectors"] = self._ingress_network_connector_arns
        if self._egress_network_connector_arns:
            request["egressNetworkConnectors"] = self._egress_network_connector_arns
        if self._idle_policy:
            request["idlePolicy"] = self._idle_policy
        if self._logging_config:
            request["logging"] = self._logging_config
        if self._run_hook_payload:
            request["runHookPayload"] = self._run_hook_payload
        return request

    def _update_state_from_response(self, response: Mapping[str, Any]) -> None:
        self._microvm_id = str(response.get("microvmId") or self._microvm_id or self._sandbox_id)
        endpoint = response.get("endpoint")
        if endpoint:
            resolved_endpoint = str(endpoint).rstrip("/")
            if not resolved_endpoint.startswith(("http://", "https://")):
                resolved_endpoint = f"https://{resolved_endpoint}"
            self._endpoint = resolved_endpoint
        state = response.get("state")
        if state:
            self._state = str(state)
        image_arn = response.get("imageArn")
        if image_arn:
            self._image_arn = str(image_arn)
        image_version = response.get("imageVersion")
        if image_version:
            self._resolved_image_version = str(image_version)

    def _wait_for_running(self) -> None:
        if self._state == RUNNING_STATE:
            return

        client = self._get_client()
        deadline = time.monotonic() + self._launch_timeout_seconds
        while time.monotonic() < deadline:
            if not self._microvm_id:
                break
            response = client.get_microvm(microvmIdentifier=self._microvm_id)
            self._update_state_from_response(response)
            if self._state == RUNNING_STATE:
                return
            if self._state in TERMINAL_STATES:
                raise RuntimeError(
                    f"Lambda MicroVM {self._microvm_id} entered terminal state {self._state}"
                )
            time.sleep(1)

        raise TimeoutError(
            f"Timed out waiting for Lambda MicroVM {self._microvm_id} to enter RUNNING"
        )

    def _wait_for_terminated(self) -> bool:
        if self._state == TERMINATED_STATE:
            return True

        if not self._microvm_id:
            return False

        client = self._get_client()
        deadline = time.monotonic() + self._teardown_timeout_seconds
        while True:
            response = client.get_microvm(microvmIdentifier=self._microvm_id)
            self._update_state_from_response(response)
            if self._state == TERMINATED_STATE:
                return True
            remaining_seconds = deadline - time.monotonic()
            if remaining_seconds <= 0:
                break
            time.sleep(min(self._teardown_poll_interval_seconds, remaining_seconds))
        return False

    def _create_auth_headers(self) -> None:
        if not self._microvm_id:
            raise RuntimeError("Cannot create MicroVM auth token before launch")
        response = self._get_client().create_microvm_auth_token(
            microvmIdentifier=self._microvm_id,
            expirationInMinutes=self._token_expiration_minutes,
            allowedPorts=[{"port": self._port}],
        )
        token_parts = response.get("authToken") or {}
        if AUTH_HEADER_NAME not in token_parts:
            raise RuntimeError("Lambda MicroVM auth token response did not include X-aws-proxy-auth")
        self._auth_headers = {
            str(key): str(value)
            for key, value in token_parts.items()
        }
        self._auth_headers[PORT_HEADER_NAME] = str(self._port)
        self._record_lifecycle_phase("auth_token_created")
        logger.info(
            "Lambda MicroVM auth token created microvm_id=%s port=%s expiration_minutes=%s",
            self._microvm_id,
            self._port,
            self._token_expiration_minutes,
        )

    def _runtime_url(self, path: str) -> str:
        if not self._endpoint:
            raise RuntimeError("Lambda MicroVM endpoint is not available")
        if path.startswith("/"):
            return f"{self._endpoint}{path}"
        return f"{self._endpoint}/{path}"

    def _runtime_request(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if self._auth_headers is None:
            raise RuntimeError("Lambda MicroVM auth token is not available")
        if self._http_client is None:
            self._http_client = httpx.Client()
        response = self._http_client.request(
            method,
            self._runtime_url(path),
            headers=self._auth_headers,
            json=json_body,
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise RuntimeError("Lambda MicroVM runtime returned a non-object JSON response")
        return data

    def _healthcheck(self) -> None:
        started = time.monotonic()
        self._record_lifecycle_phase("runtime_healthcheck_started")
        logger.info(
            "Lambda MicroVM runtime healthcheck started microvm_id=%s port=%s",
            self._microvm_id,
            self._port,
        )
        deadline = time.monotonic() + self._healthcheck_timeout_seconds
        last_error: Exception | None = None
        while time.monotonic() < deadline:
            try:
                data = self._runtime_request("GET", "/healthz", timeout=5)
                workspace_root = data.get("workspace_root")
                if isinstance(workspace_root, str) and workspace_root.strip():
                    self._workspace_root = workspace_root.rstrip("/") or self._workspace_root
                self._healthcheck_duration_ms = (time.monotonic() - started) * 1000
                self._record_lifecycle_phase("runtime_healthcheck_passed")
                logger.info(
                    "Lambda MicroVM runtime healthcheck passed microvm_id=%s duration_ms=%.2f",
                    self._microvm_id,
                    self._healthcheck_duration_ms,
                )
                return
            except Exception as e:
                last_error = e
                time.sleep(1)
        raise TimeoutError("Timed out waiting for Lambda MicroVM command server /healthz") from last_error

    def _ensure_microvm(self) -> None:
        if self._microvm_id and self._state == RUNNING_STATE and self._auth_headers:
            return

        with self._lock:
            if self._microvm_id and self._state == RUNNING_STATE and self._auth_headers:
                return

            client = self._get_client()

            if self._microvm_id and self._state == SUSPENDED_STATE:
                client.resume_microvm(microvmIdentifier=self._microvm_id)
                self._wait_for_running()
            else:
                launch_started = time.monotonic()
                self._record_lifecycle_phase("launch_started")
                logger.info(
                    "Lambda MicroVM launch started sandbox_id=%s image=%s "
                    "image_version=%s region=%s role_fingerprint=%s",
                    self._sandbox_id,
                    self._image_identifier,
                    self._image_version,
                    self._region_name,
                    _role_fingerprint(self._execution_role_arn),
                )
                response = client.run_microvm(**self._run_request())
                self._update_state_from_response(response)
                self._wait_for_running()
                self._launch_duration_ms = (time.monotonic() - launch_started) * 1000
                self._record_lifecycle_phase("launch_running")
                logger.info(
                    "Lambda MicroVM launch running microvm_id=%s image=%s "
                    "image_version=%s region=%s aws_state=%s duration_ms=%.2f",
                    self._microvm_id,
                    self._image_arn or self._image_identifier,
                    self._resolved_image_version,
                    self._region_name,
                    self._state,
                    self._launch_duration_ms,
                )

            self._create_auth_headers()
            self._healthcheck()
            logger.info(
                "Lambda MicroVM sandbox ready microvm_id=%s image=%s image_version=%s role_fingerprint=%s",
                self._microvm_id,
                self._image_arn or self._image_identifier,
                self._resolved_image_version,
                _role_fingerprint(self._execution_role_arn),
            )

    def _exception_code(self, exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if isinstance(response, Mapping):
            error = response.get("Error")
            if isinstance(error, Mapping):
                code = error.get("Code")
                if code:
                    return str(code)
        return exc.__class__.__name__

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Execute a shell command through the MicroVM command server."""
        self._ensure_microvm()
        effective_timeout = timeout or DEFAULT_COMMAND_TIMEOUT_SECONDS
        try:
            data = self._runtime_request(
                "POST",
                "/execute",
                json_body={
                    "command": ["sh", "-c", command],
                    "cwd": ".",
                    "timeout_seconds": effective_timeout,
                },
                timeout=effective_timeout + 5,
            )
        except Exception as e:
            logger.error("Lambda MicroVM execute failed", exc_info=True)
            return ExecuteResponse(output=f"Error: {e}", exit_code=-1, truncated=False)

        stdout = str(data.get("stdout") or "")
        stderr = str(data.get("stderr") or "")
        output = _response_text(stdout, stderr)
        return ExecuteResponse(
            output=output,
            exit_code=int(data.get("exit_code", -1)),
            truncated="[truncated]" in output,
        )

    def _workspace_upload_path(self, file_path: str) -> str | None:
        if not file_path.startswith("/"):
            return file_path
        root = self._workspace_root.rstrip("/")
        if file_path == root:
            return "."
        prefix = f"{root}/"
        if file_path.startswith(prefix):
            return file_path.removeprefix(prefix)
        return None

    def _upload_via_execute(self, file_path: str, content: bytes) -> FileUploadResponse:
        encoded = base64.b64encode(content).decode("ascii")
        quoted_path = shlex.quote(file_path)
        quoted_content = shlex.quote(encoded)
        result = self.execute(
            f"mkdir -p $(dirname {quoted_path}) && printf %s {quoted_content} | base64 -d > {quoted_path}"
        )
        if result.exit_code == 0:
            return FileUploadResponse(path=file_path)
        return FileUploadResponse(path=file_path, error="permission_denied")

    def upload_files(self, files: list[tuple[str, bytes]]) -> list[FileUploadResponse]:
        """Upload files to the MicroVM runtime."""
        self._ensure_microvm()
        results: list[FileUploadResponse] = []
        for file_path, content in files:
            runtime_path = self._workspace_upload_path(file_path)
            if runtime_path is None:
                results.append(self._upload_via_execute(file_path, content))
                continue

            try:
                self._runtime_request(
                    "POST",
                    "/upload",
                    json_body={
                        "path": runtime_path,
                        "content_base64": base64.b64encode(content).decode("ascii"),
                    },
                    timeout=60,
                )
                results.append(FileUploadResponse(path=file_path))
            except httpx.HTTPStatusError as e:
                try:
                    payload = e.response.json()
                except Exception:
                    payload = {}
                results.append(FileUploadResponse(path=file_path, error=_file_error_from_payload(payload)))
            except Exception:
                logger.warning("Lambda MicroVM upload failed path=%s", file_path, exc_info=True)
                results.append(FileUploadResponse(path=file_path, error="invalid_path"))
        return results

    def _download_via_execute(self, file_path: str) -> FileDownloadResponse:
        result = self.execute(f"base64 {shlex.quote(file_path)}")
        if result.exit_code != 0 or not result.output.strip():
            return FileDownloadResponse(path=file_path, error="file_not_found")
        try:
            content = base64.b64decode(result.output.strip())
        except Exception:
            return FileDownloadResponse(path=file_path, error="invalid_path")
        return FileDownloadResponse(path=file_path, content=content)

    def download_files(self, paths: list[str]) -> list[FileDownloadResponse]:
        """Download files from the MicroVM runtime."""
        self._ensure_microvm()
        results: list[FileDownloadResponse] = []
        for file_path in paths:
            runtime_path = self._workspace_upload_path(file_path)
            if runtime_path is None:
                results.append(self._download_via_execute(file_path))
                continue

            try:
                data = self._runtime_request(
                    "POST",
                    "/download",
                    json_body={"path": runtime_path},
                    timeout=60,
                )
                raw = base64.b64decode(str(data.get("content_base64") or ""))
                results.append(FileDownloadResponse(path=file_path, content=raw))
            except httpx.HTTPStatusError as e:
                try:
                    payload = e.response.json()
                except Exception:
                    payload = {}
                results.append(FileDownloadResponse(path=file_path, error=_file_error_from_payload(payload)))
            except Exception:
                logger.warning("Lambda MicroVM download failed path=%s", file_path, exc_info=True)
                results.append(FileDownloadResponse(path=file_path, error="invalid_path"))
        return results

    def terminate(self) -> None:
        """Terminate the Lambda MicroVM and clear in-memory auth material."""
        started = time.monotonic()
        self._teardown_attempt += 1
        self._teardown_error_code = None
        self._teardown_error_message = None
        try:
            if not self._microvm_id:
                self._teardown_status = "skipped"
                return

            if self._state == TERMINATED_STATE:
                self._teardown_status = "complete"
                self._record_lifecycle_phase("teardown_complete")
                return

            self._record_lifecycle_phase("teardown_started")
            logger.info(
                "Lambda MicroVM teardown started microvm_id=%s image=%s "
                "image_version=%s region=%s aws_state=%s role_fingerprint=%s attempt=%s",
                self._microvm_id,
                self._image_arn or self._image_identifier,
                self._resolved_image_version,
                self._region_name,
                self._state,
                _role_fingerprint(self._execution_role_arn),
                self._teardown_attempt,
            )
            self._get_client().terminate_microvm(microvmIdentifier=self._microvm_id)
            if self._wait_for_terminated():
                self._teardown_status = "complete"
                self._record_lifecycle_phase("teardown_complete")
                logger.info(
                    "Lambda MicroVM teardown complete microvm_id=%s aws_state=%s",
                    self._microvm_id,
                    self._state,
                )
            else:
                self._teardown_status = "pending"
                self._record_lifecycle_phase("teardown_pending")
                logger.warning(
                    "Lambda MicroVM teardown pending microvm_id=%s aws_state=%s",
                    self._microvm_id,
                    self._state,
                )
        except Exception as exc:
            self._teardown_status = "failed"
            self._teardown_error_code = self._exception_code(exc)
            self._teardown_error_message = str(exc)
            self._record_lifecycle_phase("teardown_failed")
            logger.warning(
                "Lambda MicroVM terminate failed microvm_id=%s",
                self._microvm_id,
                exc_info=True,
            )
        finally:
            self._teardown_duration_ms = (time.monotonic() - started) * 1000
            self._auth_headers = None
            self._endpoint = None
            self._created_client_token = None
            if self._owns_http_client and self._http_client is not None:
                self._http_client.close()
                self._http_client = None
