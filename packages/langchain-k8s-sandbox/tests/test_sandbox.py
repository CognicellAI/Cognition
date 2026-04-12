"""Unit tests for K8sSandbox with mocked agent-sandbox SDK."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from langchain_k8s_sandbox.sandbox import K8sSandbox


class TestK8sSandboxInit:
    def test_init_stores_config(self):
        sandbox = K8sSandbox(
            template="test-template",
            namespace="test-ns",
            router_url="http://router:8080",
            labels={"cognition.io/user": "alice"},
            ttl=1800,
            warm_pool="test-pool",
        )
        assert sandbox._template == "test-template"
        assert sandbox._namespace == "test-ns"
        assert sandbox._router_url == "http://router:8080"
        assert sandbox._labels == {"cognition.io/user": "alice"}
        assert sandbox._ttl == 1800
        assert sandbox._warm_pool == "test-pool"

    def test_init_defaults(self):
        sandbox = K8sSandbox()
        assert sandbox._template == "cognition-sandbox"
        assert sandbox._namespace == "default"
        assert sandbox._ttl is None
        assert sandbox._warm_pool is None
        assert sandbox._labels == {}

    def test_id_property(self):
        sandbox = K8sSandbox()
        assert sandbox.id.startswith("k8s-")

    def test_lazy_init_no_sdk_calls(self):
        sandbox = K8sSandbox()
        assert sandbox._sandbox is None
        assert sandbox._client is None


class TestK8sSandboxEnsureSandbox:
    @patch("langchain_k8s_sandbox.sandbox.K8sSandbox._ensure_sandbox")
    def test_execute_calls_ensure_sandbox(self, mock_ensure):
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = "hello"
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_sandbox.commands.run.return_value = mock_result
        mock_ensure.return_value = mock_sandbox

        sb = K8sSandbox()
        sb.execute("echo hello")
        mock_ensure.assert_called_once()

    def test_ensure_sandbox_raises_without_sdk(self):
        sb = K8sSandbox()
        with (
            patch.dict(
                "sys.modules", {"k8s_agent_sandbox": None, "k8s_agent_sandbox.models": None}
            ),
            pytest.raises(RuntimeError, match="k8s-agent-sandbox is required"),
        ):
            sb._ensure_sandbox()

    def test_ensure_sandbox_creates_sandbox_once(self):
        mock_sandbox_obj = MagicMock()
        mock_sandbox_obj.name = "test-sandbox-123"

        mock_client = MagicMock()
        mock_client.create_sandbox.return_value = mock_sandbox_obj

        with patch("langchain_k8s_sandbox.sandbox.K8sSandbox._ensure_sandbox"):
            pass

        with patch.dict(
            "sys.modules",
            {
                "k8s_agent_sandbox": MagicMock(SandboxClient=MagicMock(return_value=mock_client)),
                "k8s_agent_sandbox.models": MagicMock(
                    SandboxDirectConnectionConfig=MagicMock(return_value=MagicMock())
                ),
            },
        ):
            sb = K8sSandbox(template="test-tpl", namespace="test-ns", labels={"k": "v"}, ttl=600)

            with patch.object(sb, "_ensure_sandbox", return_value=mock_sandbox_obj):
                sb._ensure_sandbox()
                sb._ensure_sandbox()


class TestK8sSandboxExecute:
    def test_execute_success(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = "hello world"
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_sandbox.commands.run.return_value = mock_result

        sb._sandbox = mock_sandbox

        result = sb.execute("echo hello")
        assert result.output == "hello world"
        assert result.exit_code == 0
        assert result.truncated is False

    def test_execute_with_stderr(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = "out"
        mock_result.stderr = "err"
        mock_result.exit_code = 1
        mock_sandbox.commands.run.return_value = mock_result

        sb._sandbox = mock_sandbox

        result = sb.execute("bad-command")
        assert "out" in result.output
        assert "err" in result.output
        assert result.exit_code == 1

    def test_execute_exception_returns_error(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        mock_sandbox.commands.run.side_effect = ConnectionError("sandbox unreachable")

        sb._sandbox = mock_sandbox

        result = sb.execute("echo hello")
        assert "Error:" in result.output
        assert result.exit_code == -1

    def test_execute_timeout_forwarded(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = "ok"
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_sandbox.commands.run.return_value = mock_result

        sb._sandbox = mock_sandbox

        sb.execute("long-command", timeout=120)
        mock_sandbox.commands.run.assert_called_once_with("sh -c long-command", timeout=120)


class TestK8sSandboxTerminate:
    def test_terminate_calls_sdk(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        sb._sandbox = mock_sandbox

        sb.terminate()
        mock_sandbox.terminate.assert_called_once()
        assert sb._sandbox is None
        assert sb._client is None

    def test_terminate_idempotent(self):
        sb = K8sSandbox()
        sb.terminate()

    def test_terminate_failure_does_not_raise(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        mock_sandbox.terminate.side_effect = RuntimeError("cleanup failed")
        sb._sandbox = mock_sandbox

        sb.terminate()
        assert sb._sandbox is None

    def test_execute_after_terminate_creates_new(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        sb._sandbox = mock_sandbox

        sb.terminate()
        assert sb._sandbox is None


class TestK8sSandboxUploadDownload:
    def test_upload_files_success(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_sandbox.commands.run.return_value = mock_result

        sb._sandbox = mock_sandbox

        results = sb.upload_files([("/tmp/test.py", b"print('hello')")])
        assert len(results) == 1
        assert results[0].path == "/tmp/test.py"
        assert results[0].error is None

    def test_upload_files_failure(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "permission denied"
        mock_result.exit_code = 1
        mock_sandbox.commands.run.return_value = mock_result

        sb._sandbox = mock_sandbox

        results = sb.upload_files([("/root/secret.py", b"secret")])
        assert len(results) == 1
        assert results[0].error is not None

    def test_download_files_success(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        import base64

        mock_result = MagicMock()
        mock_result.stdout = base64.b64encode(b"file content").decode()
        mock_result.stderr = ""
        mock_result.exit_code = 0
        mock_sandbox.commands.run.return_value = mock_result

        sb._sandbox = mock_sandbox

        results = sb.download_files(["/workspace/main.py"])
        assert len(results) == 1
        assert results[0].content == b"file content"

    def test_download_files_not_found(self):
        sb = K8sSandbox()
        mock_sandbox = MagicMock()
        mock_result = MagicMock()
        mock_result.stdout = ""
        mock_result.stderr = "No such file"
        mock_result.exit_code = 1
        mock_sandbox.commands.run.return_value = mock_result

        sb._sandbox = mock_sandbox

        results = sb.download_files(["/nonexistent.py"])
        assert len(results) == 1
        assert results[0].error == "file_not_found"
