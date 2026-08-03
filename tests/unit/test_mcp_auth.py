"""Security tests for mandatory MCP transport authentication."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from urllib.parse import parse_qs

import httpx
import pytest
from langchain_mcp_adapters.interceptors import MCPToolCallRequest
from mcp.types import CallToolResult
from pydantic import SecretStr

from server.app.agent.mcp_auth import (
    AmbientWorkloadIdentity,
    McpAuthenticationError,
    McpTrustedContextInterceptor,
    StaticBearerAuth,
    WorkloadTokenExchangeAuth,
    trusted_context_headers,
)
from server.app.agent.mcp_client import (
    McpServerConfig,
    McpTransportAuthenticationError,
    mcp_config_to_connection,
)
from server.app.settings import McpWorkloadTokenExchangeProfile, Settings


def _profile(**overrides) -> McpWorkloadTokenExchangeProfile:
    values = {
        "type": "oauth_token_exchange",
        "token_endpoint": "https://identity.internal/token",
        "subject_token_source": "workload_identity",
        "audience": "canonical_server_uri",
    }
    values.update(overrides)
    return McpWorkloadTokenExchangeProfile.model_validate(values)


def test_none_connection_adds_no_authentication_or_context_headers() -> None:
    connection = mcp_config_to_connection(
        McpServerConfig(name="docs", url="https://mcp.example.test/docs"),
        Settings(),
    )

    assert connection == {
        "transport": "streamable_http",
        "url": "https://mcp.example.test/docs",
    }


def test_static_bearer_reads_named_environment_at_transport_construction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DIRECT_MCP_TOKEN", "static-secret")
    config = McpServerConfig.model_validate(
        {
            "name": "direct",
            "url": "https://mcp.example.test/direct",
            "auth": {"type": "static_bearer", "env": "DIRECT_MCP_TOKEN"},
        }
    )

    connection = mcp_config_to_connection(config, Settings())
    auth = connection["auth"]
    assert isinstance(auth, StaticBearerAuth)
    request = httpx.Request("POST", config.url)
    authenticated = next(auth.auth_flow(request))

    assert authenticated.headers["Authorization"] == "Bearer static-secret"
    assert "static-secret" not in repr(auth)
    assert "static-secret" not in str(config.model_dump(mode="json"))


def test_static_bearer_missing_environment_fails_redacted() -> None:
    config = McpServerConfig.model_validate(
        {
            "name": "direct",
            "url": "https://mcp.example.test/direct",
            "auth": {"type": "static_bearer", "env": "MISSING_MCP_TOKEN"},
        }
    )

    with pytest.raises(
        McpTransportAuthenticationError,
        match="static_bearer_unavailable",
    ):
        mcp_config_to_connection(config, Settings())


@pytest.mark.asyncio
async def test_workload_exchange_is_exact_audience_bound_and_expiry_cached() -> None:
    exchange_requests: list[httpx.Request] = []
    mcp_authorization: list[str] = []

    async def exchange_handler(request: httpx.Request) -> httpx.Response:
        exchange_requests.append(request)
        return httpx.Response(
            200,
            json={"access_token": "route-token", "token_type": "Bearer", "expires_in": 60},
        )

    async def mcp_handler(request: httpx.Request) -> httpx.Response:
        mcp_authorization.append(request.headers["Authorization"])
        return httpx.Response(200, json={"ok": True})

    auth = WorkloadTokenExchangeAuth(
        profile=_profile(),
        audience="https://mcp-egress.internal/mcp/github",
        identity=AmbientWorkloadIdentity(token_file=None, token=SecretStr("subject-token")),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(exchange_handler)),
    )

    async with httpx.AsyncClient(
        auth=auth,
        transport=httpx.MockTransport(mcp_handler),
    ) as client:
        await client.post("https://mcp-egress.internal/mcp/github")
        await client.post("https://mcp-egress.internal/mcp/github")

    assert len(exchange_requests) == 1
    form = parse_qs(exchange_requests[0].content.decode("utf-8"))
    assert form["audience"] == ["https://mcp-egress.internal/mcp/github"]
    assert form["subject_token"] == ["subject-token"]
    assert form["grant_type"] == ["urn:ietf:params:oauth:grant-type:token-exchange"]
    assert mcp_authorization == ["Bearer route-token", "Bearer route-token"]


@pytest.mark.asyncio
async def test_workload_exchange_failure_never_exposes_token_response() -> None:
    async def denied(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="subject-token provider-secret route-token")

    auth = WorkloadTokenExchangeAuth(
        profile=_profile(),
        audience="https://mcp-egress.internal/mcp/github",
        identity=AmbientWorkloadIdentity(token_file=None, token=SecretStr("subject-token")),
        client_factory=lambda: httpx.AsyncClient(transport=httpx.MockTransport(denied)),
    )

    with pytest.raises(McpAuthenticationError) as exc_info:
        async with httpx.AsyncClient(
            auth=auth,
            transport=httpx.MockTransport(lambda request: httpx.Response(200)),
        ) as client:
            await client.post("https://mcp-egress.internal/mcp/github")

    message = str(exc_info.value)
    assert exc_info.value.category == "token_exchange_denied"
    assert "subject-token" not in message
    assert "provider-secret" not in message
    assert "route-token" not in message


@pytest.mark.asyncio
async def test_workload_exchange_supports_deployment_client_secret_basic() -> None:
    exchange_requests: list[httpx.Request] = []

    async def exchange_handler(request: httpx.Request) -> httpx.Response:
        exchange_requests.append(request)
        return httpx.Response(200, json={"access_token": "route-token"})

    profile = _profile(
        client_auth="client_secret_basic",
        client_id="cognition-exchange",
        client_secret_env="TOKEN_EXCHANGE_CLIENT_SECRET",
    )
    auth = WorkloadTokenExchangeAuth(
        profile=profile,
        audience="https://mcp-egress.internal/mcp/github",
        identity=AmbientWorkloadIdentity(token_file=None, token=SecretStr("subject-token")),
        client_secret=SecretStr("client-secret"),
        client_factory=lambda: httpx.AsyncClient(
            transport=httpx.MockTransport(exchange_handler)
        ),
    )

    async with httpx.AsyncClient(
        auth=auth,
        transport=httpx.MockTransport(lambda request: httpx.Response(200)),
    ) as client:
        await client.post("https://mcp-egress.internal/mcp/github")

    encoded = base64.b64encode(b"cognition-exchange:client-secret").decode("ascii")
    assert exchange_requests[0].headers["Authorization"] == f"Basic {encoded}"


@pytest.mark.asyncio
async def test_projected_workload_identity_is_reread_for_rotation(tmp_path) -> None:
    token_file = tmp_path / "workload-token"
    token_file.write_text("first-token", encoding="utf-8")
    identity = AmbientWorkloadIdentity(token_file=token_file, token=None)

    first = await identity.get_subject_token()
    token_file.write_text("second-token", encoding="utf-8")
    second = await identity.get_subject_token()

    assert first.get_secret_value() == "first-token"
    assert second.get_secret_value() == "second-token"


def test_workload_connection_resolves_canonical_audience_and_discovery_context() -> None:
    settings = Settings.model_validate(
        {
            "mcp_auth_profiles": {"egress": _profile().model_dump()},
            "mcp_workload_identity_token": "ambient-subject",
        }
    )
    config = McpServerConfig.model_validate(
        {
            "name": "github",
            "url": "https://mcp-egress.internal/mcp/github",
            "auth": {"type": "workload_token_exchange", "profile": "egress"},
            "agent_name": "support-agent",
            "agent_revision": 4,
            "effective_scope": {"tenant": "acme"},
            "workload_profile": settings.mcp_auth_profiles["egress"],
        }
    )

    connection = mcp_config_to_connection(config, settings)

    assert isinstance(connection["auth"], WorkloadTokenExchangeAuth)
    assert connection["headers"] == {
        "X-Cognition-Context-Version": "1",
        "X-Cognition-Agent-ID": "support-agent",
        "X-Cognition-Agent-Revision": "4",
        "X-Cognition-Effective-Scope": '{"tenant":"acme"}',
        "X-Cognition-MCP-Server-Alias": "github",
        "X-Cognition-MCP-Server-URI": "https://mcp-egress.internal/mcp/github",
    }


@pytest.mark.asyncio
async def test_trusted_context_interceptor_default_denies_incoming_headers() -> None:
    contexts = {
        "github": {
            "agent_name": "support-agent",
            "agent_revision": 7,
            "effective_scope": {"tenant": "acme"},
            "server_alias": "github",
            "canonical_server_uri": "https://mcp-egress.internal/mcp/github",
        }
    }
    runtime = SimpleNamespace(
        context=SimpleNamespace(
            session_id="session-1",
            thread_id="thread-1",
            request_deadline=1_800_000_000_000,
        ),
        config={"run_id": "run-1"},
    )
    request = MCPToolCallRequest(
        name="search",
        args={"query": "safe"},
        server_name="github",
        headers={
            "Authorization": "Bearer model-token",
            "X-Cognition-Agent-ID": "model-agent",
            "X-Arbitrary": "model-value",
        },
        runtime=runtime,
    )
    captured: MCPToolCallRequest | None = None

    async def handler(value: MCPToolCallRequest) -> CallToolResult:
        nonlocal captured
        captured = value
        return CallToolResult(content=[])

    await McpTrustedContextInterceptor(contexts)(request, handler)

    assert captured is not None
    assert captured.headers is not None
    assert "Authorization" not in captured.headers
    assert "X-Arbitrary" not in captured.headers
    assert captured.headers["X-Cognition-Agent-ID"] == "support-agent"
    assert captured.headers["X-Cognition-Session-ID"] == "session-1"
    assert captured.headers["X-Cognition-Run-ID"] == "run-1"
    assert captured.headers["X-Cognition-Request-Deadline"] == "1800000000000"


@pytest.mark.asyncio
async def test_trusted_context_interceptor_fails_closed_for_unknown_server() -> None:
    request = MCPToolCallRequest(
        name="search",
        args={},
        server_name="unexpected",
        headers={"Authorization": "Bearer untrusted"},
        runtime=None,
    )

    async def handler(value: MCPToolCallRequest) -> CallToolResult:
        pytest.fail(f"unexpected transport call: {value.server_name}")

    with pytest.raises(McpAuthenticationError) as exc_info:
        await McpTrustedContextInterceptor({})(request, handler)

    assert exc_info.value.category == "trusted_context_unavailable"


def test_two_scopes_produce_distinct_trusted_context_without_cross_use() -> None:
    first = trusted_context_headers(
        agent_name="support-agent",
        agent_revision=1,
        effective_scope={"tenant": "one"},
        server_alias="github",
        canonical_server_uri="https://mcp-egress.internal/mcp/github",
    )
    second = trusted_context_headers(
        agent_name="support-agent",
        agent_revision=1,
        effective_scope={"tenant": "two"},
        server_alias="github",
        canonical_server_uri="https://mcp-egress.internal/mcp/github",
    )

    assert first["X-Cognition-Effective-Scope"] == '{"tenant":"one"}'
    assert second["X-Cognition-Effective-Scope"] == '{"tenant":"two"}'
    assert first != second


@pytest.mark.parametrize(
    ("agent_name", "category"),
    [
        ("support-agent\r\nX-Forged: true", "trusted_context_invalid"),
        ("a" * 9000, "trusted_context_too_large"),
    ],
)
def test_trusted_context_rejects_injection_and_unbounded_values(
    agent_name: str,
    category: str,
) -> None:
    with pytest.raises(McpAuthenticationError) as exc_info:
        trusted_context_headers(
            agent_name=agent_name,
            agent_revision=1,
            effective_scope={"tenant": "acme"},
            server_alias="github",
            canonical_server_uri="https://mcp-egress.internal/mcp/github",
        )

    assert exc_info.value.category == category


def test_workload_transport_fails_when_ambient_identity_is_unavailable() -> None:
    settings = Settings.model_validate({"mcp_auth_profiles": {"egress": _profile().model_dump()}})
    config = McpServerConfig.model_validate(
        {
            "name": "github",
            "url": "https://mcp-egress.internal/mcp/github",
            "auth": {"type": "workload_token_exchange", "profile": "egress"},
            "workload_profile": settings.mcp_auth_profiles["egress"],
        }
    )

    with pytest.raises(
        McpTransportAuthenticationError,
        match="workload_identity_unavailable",
    ):
        mcp_config_to_connection(config, settings)
