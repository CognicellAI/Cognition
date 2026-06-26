"""Unit tests for /sandbox/profiles API endpoints."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from server.app.api.dependencies import set_config_store
from server.app.main import app
from server.app.storage.config_store import DefaultConfigStore

client = TestClient(app)


def _profile_payload(name: str = "lambda-default") -> dict[str, object]:
    return {
        "name": name,
        "image_arn": f"arn:aws:lambda:us-east-1:123456789012:microvm-image:{name}",
        "region": "us-east-1",
        "default_execution_role_arn": ("arn:aws:iam::123456789012:role/cognition-agent-runtime"),
    }


@pytest.fixture(scope="module", autouse=True)
def setup_registry(tmp_path_factory):
    """Initialize sandbox profile registry and ConfigStore for the test module."""
    from server.app.storage.config_registry import MemoryConfigRegistry

    tmpdir = tmp_path_factory.mktemp("workspace")
    config_registry = MemoryConfigRegistry()
    config_store = DefaultConfigStore(
        config_registry=config_registry,
        workspace_path=tmpdir,
    )
    set_config_store(config_store)
    yield


class TestSandboxProfilesCrud:
    def test_create_and_get_profile(self):
        payload = _profile_payload()
        payload["logging"] = {"disabled": {}}
        payload["quota"] = {
            "max_concurrent_sessions": 2,
            "max_session_starts_per_minute": 10,
        }
        payload["run_hook_payload"] = '{"mode":"test"}'
        payload["maximum_duration_seconds"] = 900
        response = client.post("/sandbox/profiles", json=payload)
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "lambda-default"
        assert data["backend"] == "aws_lambda_microvm"
        assert data["egress_mode"] == "internet"
        assert data["logging"] == {"disabled": {}, "cloud_watch": None}
        assert data["quota"] == {
            "max_concurrent_sessions": 2,
            "max_session_starts_per_minute": 10,
        }
        assert data["run_hook_payload"] == '{"mode":"test"}'
        assert data["maximum_duration_seconds"] == 900
        assert data["source"] == "api"

        get_response = client.get("/sandbox/profiles/lambda-default")
        assert get_response.status_code == 200
        assert get_response.json()["image_arn"] == payload["image_arn"]

    def test_list_profiles(self):
        response = client.post("/sandbox/profiles", json=_profile_payload("list-me"))
        assert response.status_code == 201

        response = client.get("/sandbox/profiles")
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1
        assert any(profile["name"] == "list-me" for profile in data["profiles"])

    def test_patch_profile_to_vpc_requires_connector(self):
        response = client.post("/sandbox/profiles", json=_profile_payload("vpc-invalid"))
        assert response.status_code == 201

        response = client.patch(
            "/sandbox/profiles/vpc-invalid",
            json={"egress_mode": "vpc", "egress_network_connector_arns": []},
        )
        assert response.status_code == 422

    def test_patch_profile_to_vpc_with_connector(self):
        response = client.post("/sandbox/profiles", json=_profile_payload("vpc-valid"))
        assert response.status_code == 201

        response = client.patch(
            "/sandbox/profiles/vpc-valid",
            json={
                "egress_mode": "vpc",
                "egress_network_connector_arns": [
                    "arn:aws:lambda:us-east-1:123456789012:network-connector/private-egress"
                ],
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["egress_mode"] == "vpc"
        assert data["egress_network_connector_arns"]

    def test_patch_profile_logging_to_cloudwatch(self):
        response = client.post("/sandbox/profiles", json=_profile_payload("cw-valid"))
        assert response.status_code == 201

        response = client.patch(
            "/sandbox/profiles/cw-valid",
            json={
                "logging": {
                    "cloud_watch": {
                        "log_group": "/aws/lambda-microvms/cognition",
                        "log_stream": "agent-session",
                    }
                }
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["logging"] == {
            "disabled": None,
            "cloud_watch": {
                "log_group": "/aws/lambda-microvms/cognition",
                "log_stream": "agent-session",
            },
        }

    def test_reject_logging_with_multiple_destinations(self):
        response = client.post(
            "/sandbox/profiles",
            json={
                **_profile_payload("logging-invalid"),
                "logging": {
                    "disabled": {},
                    "cloud_watch": {"log_group": "/aws/lambda-microvms/cognition"},
                },
            },
        )

        assert response.status_code == 422

    def test_reject_empty_quota_policy(self):
        response = client.post(
            "/sandbox/profiles",
            json={
                **_profile_payload("quota-invalid"),
                "quota": {},
            },
        )

        assert response.status_code == 422

    def test_delete_profile(self):
        response = client.post(
            "/sandbox/profiles",
            json=_profile_payload("delete-me"),
        )
        assert response.status_code == 201

        delete_response = client.delete("/sandbox/profiles/delete-me")
        assert delete_response.status_code == 204
        assert client.get("/sandbox/profiles/delete-me").status_code == 404
