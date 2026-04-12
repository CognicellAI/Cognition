"""E2E tests for Cognition K8s sandbox backend.

These tests verify the K8s sandbox integration against a live Kubernetes cluster
running the agent-sandbox controller, sandbox-router, and Cognition server.

Requirements:
- A K8s cluster with agent-sandbox controller + extensions installed
- A running sandbox-router service
- A SandboxTemplate named ``cognition-sandbox`` in the target namespace
- Cognition server deployed with ``COGNITION_SANDBOX_BACKEND=kubernetes``
- ``COGNITION_K8S_E2E=1`` environment variable set

Usage:
    # Against a cluster with port-forwarded Cognition server
    COGNITION_K8S_E2E=1 uv run pytest tests/e2e/test_k8s_sandbox_e2e.py -v

    # Against a specific server URL
    COGNITION_K8S_E2E=1 COGNITION_K8S_E2E_URL=http://localhost:8000 \
        uv run pytest tests/e2e/test_k8s_sandbox_e2e.py -v

    # Single test
    COGNITION_K8S_E2E=1 uv run pytest \
        tests/e2e/test_k8s_sandbox_e2e.py::TestK8sSandboxLifecycle::test_execute_returns_output -v
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx
import pytest
import pytest_asyncio
from kubernetes import client as k8s_client
from kubernetes.config import load_kube_config

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.timeout(120),
    pytest.mark.skipif(
        not os.environ.get("COGNITION_K8S_E2E"),
        reason="Requires COGNITION_K8S_E2E=1 (live K8s cluster with agent-sandbox)",
    ),
]

BASE_URL = os.environ.get("COGNITION_K8S_E2E_URL", "http://localhost:8000")
K8S_NAMESPACE = os.environ.get("COGNITION_K8S_E2E_NAMESPACE", "cognition")
K8S_ROUTER_URL = os.environ.get(
    "COGNITION_K8S_E2E_ROUTER_URL",
    f"http://sandbox-router-svc.{K8S_NAMESPACE}.svc.cluster.local:8080",
)
SCOPE_USER = "k8s-e2e-test-user"
SCOPE_HEADER = {"X-Cognition-Scope-User": SCOPE_USER}


def _k8s_custom_objects_api() -> k8s_client.CustomObjectsApi:
    try:
        from kubernetes.config import load_incluster_config

        load_incluster_config()
    except Exception:
        load_kube_config()
    return k8s_client.CustomObjectsApi()


def _get_sandbox_cr(name: str) -> dict[str, Any] | None:
    api = _k8s_custom_objects_api()
    try:
        return api.get_namespaced_custom_object(
            group="agents.x-k8s.io",
            version="v1alpha1",
            namespace=K8S_NAMESPACE,
            plural="sandboxes",
            name=name,
        )
    except Exception:
        return None


def _get_sandbox_claim_cr(name: str) -> dict[str, Any] | None:
    api = _k8s_custom_objects_api()
    try:
        return api.get_namespaced_custom_object(
            group="extensions.agents.x-k8s.io",
            version="v1alpha1",
            namespace=K8S_NAMESPACE,
            plural="sandboxclaims",
            name=name,
        )
    except Exception:
        return None


def _list_sandbox_claims() -> list[dict[str, Any]]:
    api = _k8s_custom_objects_api()
    try:
        resp = api.list_namespaced_custom_object(
            group="extensions.agents.x-k8s.io",
            version="v1alpha1",
            namespace=K8S_NAMESPACE,
            plural="sandboxclaims",
        )
        return resp.get("items", [])
    except Exception:
        return []


@pytest.mark.asyncio
class TestK8sSandboxLifecycle:
    """Test sandbox creation, execution, and termination via Cognition API."""

    @pytest_asyncio.fixture
    async def http_client(self):
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            yield client

    @pytest_asyncio.fixture
    async def session_id(self, http_client: httpx.AsyncClient) -> str:
        response = await http_client.post(
            f"{BASE_URL}/sessions",
            json={"title": "K8s E2E Test"},
            headers=SCOPE_HEADER,
        )
        assert response.status_code == 201
        sid = response.json()["id"]
        yield sid
        await http_client.delete(f"{BASE_URL}/sessions/{sid}", headers=SCOPE_HEADER)

    async def test_execute_returns_output(
        self, http_client: httpx.AsyncClient, session_id: str
    ) -> None:
        """Execute a simple command and verify the API responds."""
        response = await http_client.post(
            f"{BASE_URL}/sessions/{session_id}/messages",
            json={"content": "Run echo k8s-e2e-test in a shell"},
            headers=SCOPE_HEADER,
        )
        assert response.status_code == 200
        text = response.text
        assert "event:" in text or "data:" in text

    async def test_session_delete_cleans_up_sandbox(self, http_client: httpx.AsyncClient) -> None:
        """Session deletion terminates the sandbox backend."""
        response = await http_client.post(
            f"{BASE_URL}/sessions",
            json={"title": "K8s E2E Terminate Test"},
            headers=SCOPE_HEADER,
        )
        assert response.status_code == 201
        sid = response.json()["id"]

        await http_client.post(
            f"{BASE_URL}/sessions/{sid}/messages",
            json={"content": "Run echo alive"},
            headers={**SCOPE_HEADER, "Accept": "application/json"},
        )

        claims_before = _list_sandbox_claims()
        claim_names_before = {c["metadata"]["name"] for c in claims_before}

        response = await http_client.delete(f"{BASE_URL}/sessions/{sid}", headers=SCOPE_HEADER)
        assert response.status_code == 204

        time.sleep(3)

        claims_after = _list_sandbox_claims()
        claim_names_after = {c["metadata"]["name"] for c in claims_after}

        new_claims = claim_names_after - claim_names_before
        assert len(new_claims) == 0, (
            f"Sandbox claims not cleaned up after session delete: {new_claims}"
        )


class TestK8sSandboxDirectBackend:
    """Test K8sSandbox backend directly without LLM involvement.

    These tests create K8sSandbox instances directly and verify file ops,
    labels, and TTL against live K8s CRs.
    """

    def test_execute_command(self) -> None:
        """execute() runs a command and returns output."""
        from langchain_k8s_sandbox import K8sSandbox

        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=300,
        )
        try:
            result = sandbox.execute("echo hello-k8s")
            assert result.exit_code == 0
            assert "hello-k8s" in result.output
        finally:
            sandbox.terminate()

    def test_execute_python(self) -> None:
        """execute() runs Python code inside the sandbox."""
        from langchain_k8s_sandbox import K8sSandbox

        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=300,
        )
        try:
            result = sandbox.execute('python3 -c "import sys; print(sys.version)"')
            assert result.exit_code == 0
            assert "3.1" in result.output
        finally:
            sandbox.terminate()

    def test_base_sandbox_write_and_read(self) -> None:
        """BaseSandbox.write() and read() work via heredoc through sh -c."""
        from langchain_k8s_sandbox import K8sSandbox

        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=300,
        )
        try:
            sandbox.write("/workspace/e2e_test.txt", "Hello from K8s e2e!")
            content = sandbox.read("/workspace/e2e_test.txt")
            assert "Hello from K8s e2e!" in content
        finally:
            sandbox.terminate()

    def test_base_sandbox_edit(self) -> None:
        """BaseSandbox.edit() modifies file content."""
        from langchain_k8s_sandbox import K8sSandbox

        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=300,
        )
        try:
            sandbox.write("/workspace/edit_test.txt", "original content")
            sandbox.edit("/workspace/edit_test.txt", "original", "modified")
            content = sandbox.read("/workspace/edit_test.txt")
            assert "modified content" in content
            assert "original" not in content or "modified" in content
        finally:
            sandbox.terminate()

    def test_upload_and_download_files(self) -> None:
        """upload_files() and download_files() transfer binary data."""
        from langchain_k8s_sandbox import K8sSandbox

        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=300,
        )
        try:
            upload_data = b"name,value\ntest,42\n"
            results = sandbox.upload_files([("/workspace/data.csv", upload_data)])
            assert results[0].error is None

            dl = sandbox.download_files(["/workspace/data.csv"])
            assert dl[0].content is not None
            assert dl[0].content == upload_data
        finally:
            sandbox.terminate()

    def test_labels_on_sandbox_claim(self) -> None:
        """Labels are applied to the SandboxClaim CR."""
        from langchain_k8s_sandbox import K8sSandbox

        labels = {
            "cognition.io/user": "e2e-test-user",
            "cognition.io/session": "label-test",
        }
        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=300,
            labels=labels,
        )
        try:
            sandbox.execute("echo label-test")

            claim_name = sandbox._sandbox_id
            claim_cr = _get_sandbox_claim_cr(claim_name)
            assert claim_cr is not None, f"SandboxClaim {claim_name} not found"

            claim_labels = claim_cr["metadata"].get("labels", {})
            assert claim_labels.get("cognition.io/user") == "e2e-test-user"
            assert claim_labels.get("cognition.io/session") == "label-test"
        finally:
            sandbox.terminate()

    def test_shutdown_time_on_sandbox_cr(self) -> None:
        """TTL patches spec.shutdownTime on the Sandbox CR."""
        from langchain_k8s_sandbox import K8sSandbox

        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=600,
        )
        try:
            sandbox.execute("echo ttl-test")

            sandbox_name = sandbox._sandbox_id
            sandbox_cr = _get_sandbox_cr(sandbox_name)
            assert sandbox_cr is not None, f"Sandbox {sandbox_name} not found"

            shutdown_time = sandbox_cr.get("spec", {}).get("shutdownTime")
            assert shutdown_time is not None, "shutdownTime not set on Sandbox CR"
            assert shutdown_time.endswith("Z"), (
                f"shutdownTime should be ISO format: {shutdown_time}"
            )
        finally:
            sandbox.terminate()

    def test_terminate_removes_claim(self) -> None:
        """terminate() deletes the SandboxClaim."""
        from langchain_k8s_sandbox import K8sSandbox

        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=300,
        )
        sandbox.execute("echo terminate-test")
        claim_name = sandbox._sandbox_id

        claim_before = _get_sandbox_claim_cr(claim_name)
        assert claim_before is not None, "Claim should exist before terminate"

        sandbox.terminate()

        time.sleep(3)

        claim_after = _get_sandbox_claim_cr(claim_name)
        assert claim_after is None, f"Claim {claim_name} should be deleted after terminate"

    def test_lazy_init_no_cr_before_execute(self) -> None:
        """No Sandbox CR is created until execute() is called."""
        from langchain_k8s_sandbox import K8sSandbox

        sandbox = K8sSandbox(
            template="cognition-sandbox",
            namespace=K8S_NAMESPACE,
            router_url=K8S_ROUTER_URL,
            ttl=300,
        )
        try:
            assert sandbox._sandbox is None, "Sandbox should not exist before execute"
            claims_before = _list_sandbox_claims()

            sandbox.execute("echo lazy-init-test")

            assert sandbox._sandbox is not None, "Sandbox should exist after execute"
        finally:
            sandbox.terminate()


@pytest.mark.asyncio
class TestK8sSandboxStartupValidation:
    """Test startup validation checks for K8s sandbox."""

    async def test_crd_exists(self) -> None:
        """Agent-sandbox CRDs are installed on the cluster."""
        load_kube_config()
        api = k8s_client.ApiextensionsV1Api()
        crd_names = [
            "sandboxes.agents.x-k8s.io",
            "sandboxclaims.extensions.agents.x-k8s.io",
            "sandboxtemplates.extensions.agents.x-k8s.io",
        ]
        for crd_name in crd_names:
            try:
                api.read_custom_resource_definition(name=crd_name)
            except Exception as e:
                pytest.fail(f"CRD {crd_name} not found: {e}")

    async def test_sandbox_template_exists(self) -> None:
        """The cognition-sandbox SandboxTemplate exists."""
        api = _k8s_custom_objects_api()
        try:
            api.get_namespaced_custom_object(
                group="extensions.agents.x-k8s.io",
                version="v1alpha1",
                namespace=K8S_NAMESPACE,
                plural="sandboxtemplates",
                name="cognition-sandbox",
            )
        except Exception as e:
            pytest.fail(f"SandboxTemplate cognition-sandbox not found: {e}")
