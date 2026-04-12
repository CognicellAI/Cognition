"""Unit tests for CognitionKubernetesSandboxBackend."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from server.app.agent.sandbox_backend import (
    CognitionKubernetesSandboxBackend,
    create_sandbox_backend,
)


class TestCognitionKubernetesSandboxBackendInit:
    def test_init_stores_config(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(
            root_dir=tmp_path,
            sandbox_id="test-sandbox",
            template="gpu-sandbox",
            namespace="ml-team",
            router_url="http://router:8080",
            labels={"cognition.io/user": "alice"},
            ttl=7200,
            warm_pool="gpu-pool",
        )
        assert backend.id == "test-sandbox"
        assert backend._template == "gpu-sandbox"
        assert backend._namespace == "ml-team"
        assert backend._labels == {"cognition.io/user": "alice"}
        assert backend._ttl == 7200
        assert backend._warm_pool == "gpu-pool"
        assert backend._backend is None

    def test_init_defaults(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        assert backend.id.startswith("cognition-k8s-")
        assert backend._template == "cognition-sandbox"
        assert backend._namespace == "default"
        assert backend._ttl == 3600
        assert backend._labels == {}
        assert backend._protected_paths == [".cognition"]


class TestCognitionKubernetesSandboxBackendProtectedPaths:
    def test_write_to_protected_path_raises(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        with pytest.raises(PermissionError, match="protected path"):
            backend.write(".cognition/config.yaml", "forbidden")

    def test_edit_to_protected_path_raises(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        with pytest.raises(PermissionError, match="protected path"):
            backend.edit(".cognition/config.yaml", "old", "new")

    def test_write_to_normal_path_delegates(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        mock_inner = MagicMock()
        mock_inner.write.return_value = MagicMock(error=None, path="main.py")
        backend._backend = mock_inner

        result = backend.write("main.py", "print('hello')")
        mock_inner.write.assert_called_once_with("main.py", "print('hello')")


class TestCognitionKubernetesSandboxBackendExecute:
    def test_execute_delegates_to_inner(self, tmp_path):
        from deepagents.backends.protocol import ExecuteResponse

        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        mock_inner = MagicMock()
        mock_inner.execute.return_value = ExecuteResponse(
            output="hello", exit_code=0, truncated=False
        )
        backend._backend = mock_inner

        result = backend.execute("echo hello")
        mock_inner.execute.assert_called_once_with("echo hello", timeout=None)
        assert result.output == "hello"
        assert result.exit_code == 0

    def test_execute_with_timeout(self, tmp_path):
        from deepagents.backends.protocol import ExecuteResponse

        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        mock_inner = MagicMock()
        mock_inner.execute.return_value = ExecuteResponse(output="", exit_code=0, truncated=False)
        backend._backend = mock_inner

        backend.execute("long-cmd", timeout=120)
        mock_inner.execute.assert_called_once_with("long-cmd", timeout=120)


class TestCognitionKubernetesSandboxBackendTerminate:
    def test_terminate_delegates(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        mock_inner = MagicMock()
        backend._backend = mock_inner

        backend.terminate()
        mock_inner.terminate.assert_called_once()
        assert backend._backend is None

    def test_terminate_when_not_initialized(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        backend.terminate()


class TestCognitionKubernetesSandboxBackendLazyInit:
    def test_get_backend_raises_without_package(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        with patch(
            "builtins.__import__",
            side_effect=ImportError("No module named 'langchain_k8s_sandbox'"),
        ):
            with pytest.raises(RuntimeError, match="langchain-k8s-sandbox is required"):
                backend._get_backend()

    def test_get_backend_creates_k8s_sandbox(self, tmp_path):
        mock_k8s_sandbox = MagicMock()
        mock_k8s_class = MagicMock(return_value=mock_k8s_sandbox)
        mock_module = MagicMock(K8sSandbox=mock_k8s_class)

        backend = CognitionKubernetesSandboxBackend(
            root_dir=tmp_path,
            template="test-tpl",
            namespace="test-ns",
            labels={"k": "v"},
            ttl=600,
        )

        with patch.dict("sys.modules", {"langchain_k8s_sandbox": mock_module}):
            result = backend._get_backend()
            assert result is mock_k8s_sandbox
            mock_k8s_class.assert_called_once_with(
                template="test-tpl",
                namespace="test-ns",
                router_url="http://sandbox-router-svc.default.svc.cluster.local:8080",
                labels={"k": "v"},
                ttl=600,
                warm_pool=None,
            )

    def test_get_backend_cached(self, tmp_path):
        backend = CognitionKubernetesSandboxBackend(root_dir=tmp_path)
        mock_k8s_sandbox = MagicMock()
        mock_k8s_class = MagicMock(return_value=mock_k8s_sandbox)
        mock_module = MagicMock(K8sSandbox=mock_k8s_class)

        with patch.dict("sys.modules", {"langchain_k8s_sandbox": mock_module}):
            backend._get_backend()
            backend._get_backend()
            assert mock_k8s_class.call_count == 1


class TestCreateSandboxBackendFactory:
    def test_kubernetes_branch(self, tmp_path):
        backend = create_sandbox_backend(
            root_dir=tmp_path,
            sandbox_id="test",
            sandbox_backend="kubernetes",
            k8s_template="test-tpl",
            k8s_namespace="test-ns",
            k8s_router_url="http://router:8080",
            k8s_ttl=1800,
            labels={"cognition.io/user": "bob"},
        )
        assert isinstance(backend, CognitionKubernetesSandboxBackend)
        assert backend._template == "test-tpl"
        assert backend._namespace == "test-ns"
        assert backend._labels == {"cognition.io/user": "bob"}

    def test_unknown_backend_raises(self, tmp_path):
        with pytest.raises(ValueError, match="Unknown sandbox_backend"):
            create_sandbox_backend(
                root_dir=tmp_path,
                sandbox_backend="invalid",
            )

    def test_local_backend_still_works(self, tmp_path):
        backend = create_sandbox_backend(
            root_dir=tmp_path,
            sandbox_backend="local",
        )
        assert backend.id.startswith("cognition-local-")

    def test_docker_backend_still_works(self, tmp_path):
        backend = create_sandbox_backend(
            root_dir=tmp_path,
            sandbox_backend="docker",
        )
        assert backend.id.startswith("cognition-docker-")
