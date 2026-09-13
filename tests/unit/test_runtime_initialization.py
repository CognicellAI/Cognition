"""Deployment-owned initialization transport boundary tests."""
import json
from unittest.mock import patch

import httpx
import pytest
from pydantic import SecretStr

from server.app.execution.initialization import bind_runtime_initializer


def bind(scope):
    return bind_runtime_initializer(
        url="https://initializer.example/run", token=SecretStr("bearer-canary"),
        scope=scope, profile_name="runtime", image_arn="image", image_version="1",
        maximum_duration_seconds=120,
    )


def test_initialization_sends_copied_trusted_scope_and_transient_result():
    scope = {"project": "a", "principal": "caller"}
    initializer = bind(scope)
    scope["project"] = "b"

    def handle(request):
        assert request.headers["authorization"] == "Bearer bearer-canary"
        assert json.loads(request.content) == {
            "effective_scope": {"project": "a", "principal": "caller"},
            "profile_name": "runtime", "image_arn": "image", "image_version": "1",
            "maximum_duration_seconds": 120, "microvm_id": "vm-a",
        }
        return httpx.Response(200, json={"transient": "payload-canary"})

    client = httpx.Client(transport=httpx.MockTransport(handle))
    with patch("server.app.execution.initialization.httpx.Client", return_value=client) as factory:
        assert initializer("vm-a") == {"transient": "payload-canary"}
    factory.assert_called_once_with(timeout=10, follow_redirects=False, trust_env=False, verify=True)


@pytest.mark.parametrize("status,body", [
    (403, b"payload-canary"), (302, b"payload-canary"),
    (200, b"[]"), (200, b"not-json"), (200, b"x" * 16385),
])
def test_invalid_authority_response_fails_without_raw_content(status, body, caplog):
    initializer = bind({"project": "a"})
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(status, content=body)))
    with patch("server.app.execution.initialization.httpx.Client", return_value=client):
        with pytest.raises(RuntimeError, match="authority request failed") as error:
            initializer("vm-a")
    assert "payload-canary" not in str(error.value)
    assert "bearer-canary" not in caplog.text


@pytest.mark.parametrize("url", ["http://initializer.example/run", "https://user:pass@example.com", "https://example.com/#secret"])
def test_initialization_settings_reject_insecure_destinations(url):
    from pydantic import ValidationError

    from server.app.settings import Settings

    with pytest.raises(ValidationError):
        Settings(sandbox_initialization_url=url)


def test_custom_ca_keeps_tls_verification_and_disables_environment_proxy():
    initializer = bind_runtime_initializer(
        url="https://initializer.example/run", token=SecretStr("canary"),
        scope={}, profile_name="runtime", image_arn="image", image_version="1",
        maximum_duration_seconds=120, ca_file="/deployment/authority-ca.pem",
    )
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json={})))
    with patch("server.app.execution.initialization.httpx.Client", return_value=client) as factory:
        assert initializer("vm-a") == {}
    factory.assert_called_once_with(
        timeout=10, follow_redirects=False, trust_env=False, verify="/deployment/authority-ca.pem",
    )
