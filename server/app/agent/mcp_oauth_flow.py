"""Short-lived MCP OAuth authorization transaction coordination."""

from __future__ import annotations

import asyncio
import secrets
import time
from dataclasses import dataclass, field
from typing import Literal
from urllib.parse import parse_qs, urlsplit

import httpx
from mcp.client.auth import OAuthClientProvider

from server.app.agent.mcp_auth import McpAuthenticationError, create_mcp_oauth_auth
from server.app.settings import Settings
from server.app.storage.mcp_oauth import (
    EncryptedMcpOAuthTokenStorage,
    McpOAuthStateRepository,
    McpOAuthStorageError,
)

McpOAuthFlowStatus = Literal["pending", "authorization_required", "authorized", "failed"]


class McpOAuthFlowError(RuntimeError):
    """Typed, redacted authorization transaction failure."""

    def __init__(self, category: str) -> None:
        self.category = category
        super().__init__(f"MCP OAuth authorization failed: {category}")


@dataclass
class McpOAuthFlowView:
    """Credential-free projection returned to API callers."""

    flow_id: str | None
    status: McpOAuthFlowStatus
    authorization_url: str | None = None
    expires_in_seconds: int | None = None
    failure_category: str | None = None


@dataclass
class _PendingFlow:
    flow_id: str
    agent_name: str
    server_alias: str
    effective_scope: dict[str, str]
    server_url: str
    storage: EncryptedMcpOAuthTokenStorage
    expires_at: float
    authorization_url: asyncio.Future[str]
    callback: asyncio.Future[tuple[str, str | None]]
    completion: asyncio.Future[None]
    status: McpOAuthFlowStatus = "pending"
    state: str | None = None
    failure_category: str | None = None
    callback_received: bool = False
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class McpOAuthFlowCoordinator:
    """Coordinate SDK PKCE callbacks without persisting transaction secrets."""

    def __init__(
        self,
        *,
        settings: Settings,
        repository: McpOAuthStateRepository,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._repository = repository
        self._transport = transport
        self._flows: dict[str, _PendingFlow] = {}
        self._states: dict[str, str] = {}

    async def begin(
        self,
        *,
        agent_name: str,
        server_alias: str,
        server_url: str,
        effective_scope: dict[str, str],
    ) -> McpOAuthFlowView:
        """Start authorization or report an existing exact-partition token."""
        storage = self._storage(agent_name, effective_scope, server_url)
        try:
            if await storage.get_tokens() is not None:
                return McpOAuthFlowView(flow_id=None, status="authorized")
        except McpOAuthStorageError as exc:
            raise McpOAuthFlowError(exc.category) from exc

        self._cleanup()
        self._reject_duplicate(agent_name, server_alias, effective_scope, server_url)
        loop = asyncio.get_running_loop()
        flow_id = secrets.token_urlsafe(24)
        flow = _PendingFlow(
            flow_id=flow_id,
            agent_name=agent_name,
            server_alias=server_alias,
            effective_scope=dict(effective_scope),
            server_url=server_url,
            storage=storage,
            expires_at=time.monotonic() + self._settings.mcp_oauth_timeout_seconds,
            authorization_url=loop.create_future(),
            callback=loop.create_future(),
            completion=loop.create_future(),
        )
        self._flows[flow_id] = flow

        async def redirect_handler(url: str) -> None:
            state_values = parse_qs(urlsplit(url).query).get("state", [])
            if len(state_values) != 1 or not state_values[0]:
                raise McpOAuthFlowError("authorization_state_invalid")
            state = state_values[0]
            if state in self._states:
                raise McpOAuthFlowError("authorization_state_duplicate")
            flow.state = state
            flow.status = "authorization_required"
            self._states[state] = flow.flow_id
            if not flow.authorization_url.done():
                flow.authorization_url.set_result(url)

        async def callback_handler() -> tuple[str, str | None]:
            return await flow.callback

        try:
            auth = create_mcp_oauth_auth(
                settings=self._settings,
                repository=self._repository,
                agent_name=agent_name,
                effective_scope=effective_scope,
                canonical_server_uri=server_url,
                redirect_handler=redirect_handler,
                callback_handler=callback_handler,
            )
        except McpAuthenticationError as exc:
            self._fail(flow, exc.category)
            raise McpOAuthFlowError(exc.category) from exc
        flow.task = asyncio.create_task(self._drive(flow, auth))

        try:
            authorization_url = await asyncio.wait_for(
                asyncio.shield(flow.authorization_url),
                timeout=min(30.0, self._settings.mcp_oauth_timeout_seconds),
            )
        except TimeoutError as exc:
            category = flow.failure_category or "authorization_start_failed"
            self._fail(flow, category)
            raise McpOAuthFlowError(category) from exc
        if not authorization_url:
            raise McpOAuthFlowError(flow.failure_category or "authorization_start_failed")
        return self._view(flow, authorization_url=authorization_url)

    async def complete(
        self,
        *,
        code: str,
        state: str,
        effective_scope: dict[str, str],
    ) -> McpOAuthFlowView:
        """Deliver an authorization callback and await encrypted token storage."""
        self._cleanup()
        flow_id = self._states.get(state)
        flow = self._flows.get(flow_id) if flow_id is not None else None
        if (
            flow is None
            or flow.state is None
            or flow.effective_scope != effective_scope
        ):
            raise McpOAuthFlowError("authorization_state_unknown")
        if flow.callback_received:
            raise McpOAuthFlowError("authorization_callback_replayed")
        flow.callback_received = True
        flow.callback.set_result((code, state))
        try:
            await asyncio.wait_for(
                asyncio.shield(flow.completion),
                timeout=max(0.1, flow.expires_at - time.monotonic()),
            )
        except TimeoutError as exc:
            self._fail(flow, "authorization_timeout")
            raise McpOAuthFlowError("authorization_timeout") from exc
        if flow.status != "authorized":
            raise McpOAuthFlowError(flow.failure_category or "authorization_failed")
        return self._view(flow)

    def get(
        self,
        *,
        flow_id: str,
        effective_scope: dict[str, str],
    ) -> McpOAuthFlowView:
        """Return a scope-bound, credential-free transaction observation."""
        self._cleanup()
        flow = self._flows.get(flow_id)
        if flow is None or flow.effective_scope != effective_scope:
            raise McpOAuthFlowError("authorization_flow_unknown")
        return self._view(flow)

    async def close(self) -> None:
        for flow in self._flows.values():
            if flow.task is not None and not flow.task.done():
                flow.task.cancel()
        await asyncio.gather(
            *(flow.task for flow in self._flows.values() if flow.task is not None),
            return_exceptions=True,
        )
        self._flows.clear()
        self._states.clear()

    async def _drive(self, flow: _PendingFlow, auth: OAuthClientProvider) -> None:
        try:
            async with httpx.AsyncClient(
                auth=auth,
                timeout=self._settings.mcp_oauth_timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                await client.post(
                    flow.server_url,
                    json={
                        "jsonrpc": "2.0",
                        "id": "cognition-oauth",
                        "method": "initialize",
                        "params": {
                            "protocolVersion": "2025-11-25",
                            "capabilities": {},
                            "clientInfo": {"name": "Cognition", "version": "0.14"},
                        },
                    },
                    headers={"Accept": "application/json, text/event-stream"},
                )
            if await flow.storage.get_tokens() is None:
                self._fail(flow, "authorization_token_unavailable")
                return
            flow.status = "authorized"
            if not flow.completion.done():
                flow.completion.set_result(None)
        except asyncio.CancelledError:
            self._fail(flow, "authorization_cancelled")
            raise
        except Exception:
            self._fail(flow, "authorization_failed")
        finally:
            # The authorization code is single-use. Drop the completed Future
            # that held it as soon as the SDK exchange finishes.
            if flow.callback.done():
                loop = asyncio.get_running_loop()
                flow.callback = loop.create_future()
                flow.callback.cancel()

    def _storage(
        self,
        agent_name: str,
        effective_scope: dict[str, str],
        server_url: str,
    ) -> EncryptedMcpOAuthTokenStorage:
        key = self._settings.mcp_oauth_encryption_key
        if key is None or self._settings.mcp_oauth_redirect_uri is None:
            raise McpOAuthFlowError("oauth_configuration_unavailable")
        try:
            return EncryptedMcpOAuthTokenStorage(
                repository=self._repository,
                encryption_key=key,
                agent_name=agent_name,
                effective_scope=effective_scope,
                canonical_server_uri=server_url,
            )
        except McpOAuthStorageError as exc:
            raise McpOAuthFlowError(exc.category) from exc

    def _reject_duplicate(
        self,
        agent_name: str,
        server_alias: str,
        effective_scope: dict[str, str],
        server_url: str,
    ) -> None:
        for flow in self._flows.values():
            if (
                flow.status in {"pending", "authorization_required"}
                and flow.agent_name == agent_name
                and flow.server_alias == server_alias
                and flow.effective_scope == effective_scope
                and flow.server_url == server_url
            ):
                raise McpOAuthFlowError("authorization_flow_in_progress")

    def _cleanup(self) -> None:
        now = time.monotonic()
        for flow_id, flow in list(self._flows.items()):
            if flow.expires_at <= now and flow.status in {"pending", "authorization_required"}:
                self._fail(flow, "authorization_expired")
            elif flow.expires_at <= now and flow.status in {"authorized", "failed"}:
                if flow.state is not None:
                    self._states.pop(flow.state, None)
                self._flows.pop(flow_id, None)

    def _fail(self, flow: _PendingFlow, category: str) -> None:
        flow.status = "failed"
        flow.failure_category = category
        if flow.state is not None:
            self._states.pop(flow.state, None)
        if not flow.completion.done():
            flow.completion.set_result(None)
        if not flow.authorization_url.done():
            flow.authorization_url.set_result("")

    @staticmethod
    def _view(
        flow: _PendingFlow,
        *,
        authorization_url: str | None = None,
    ) -> McpOAuthFlowView:
        remaining = max(0, int(flow.expires_at - time.monotonic()))
        return McpOAuthFlowView(
            flow_id=flow.flow_id,
            status=flow.status,
            authorization_url=authorization_url,
            expires_in_seconds=remaining,
            failure_category=flow.failure_category,
        )


__all__ = [
    "McpOAuthFlowCoordinator",
    "McpOAuthFlowError",
    "McpOAuthFlowStatus",
    "McpOAuthFlowView",
]
