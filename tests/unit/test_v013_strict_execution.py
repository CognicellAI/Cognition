"""Strict sandbox-only execution and callback policy tests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from server.app.agent.cognition_agent import (
    CognitionAgentParams,
    clear_agent_cache,
    create_cognition_agent,
)
from server.app.agent.mcp_client import McpServerConfig
from server.app.agent.resolver import RuntimeResolver
from server.app.agent.sandbox_backend import CognitionDockerSandboxBackend
from server.app.api.routes.messages import _approved_callback_origin
from server.app.settings import Settings
from server.app.storage.config_models import (
    GlobalAgentDefaults,
    ToolRegistration,
)
from server.app.storage.config_registry import MemoryConfigRegistry
from server.app.storage.config_store import DefaultConfigStore


class _DefaultsOnlyStore:
    config_registry = None

    async def get_global_agent_defaults(
        self,
        scope: dict[str, str] | None = None,
    ) -> GlobalAgentDefaults:
        del scope
        return GlobalAgentDefaults()


def _settings(tmp_path: Any | None = None, **overrides: Any) -> Settings:
    settings = Settings()
    if tmp_path is not None:
        settings.workspace_root = tmp_path
    for key, value in overrides.items():
        setattr(settings, key, value)
    return settings


def _strict_agent_params(tmp_path, **overrides: Any) -> CognitionAgentParams:
    values: dict[str, Any] = {
        "project_path": tmp_path,
        "model": "provider:model",
        "settings": _settings(
            tmp_path,
            sandbox_backend="local",
            unsafe_local_execution=True,
            allow_host_tools=False,
            allow_api_python_tools=False,
        ),
        "config_store": _DefaultsOnlyStore(),
        "system_prompt": "Strict runtime.",
        "memory": [],
        "skills": [],
        "subagents": [],
        "async_subagents": [],
        "interrupt_on": {},
        "permissions": [],
    }
    values.update(overrides)
    return CognitionAgentParams(**values)


@pytest.mark.asyncio
async def test_local_execution_fails_closed_by_default(tmp_path) -> None:
    settings = _settings(
        tmp_path,
        sandbox_backend="local",
        unsafe_local_execution=False,
    )
    with pytest.raises(RuntimeError, match="Local execution is disabled"):
        await create_cognition_agent(
            _strict_agent_params(tmp_path, settings=settings)
        )


@pytest.mark.asyncio
async def test_attached_python_tools_are_rejected_in_strict_mode(tmp_path) -> None:
    with pytest.raises(RuntimeError, match="Attached Python tools are disabled"):
        await create_cognition_agent(
            _strict_agent_params(tmp_path, tools=[lambda: None])
        )


@pytest.mark.asyncio
async def test_host_side_mcp_is_rejected_in_strict_mode(tmp_path) -> None:
    config = McpServerConfig(
        name="host-mcp",
        url="https://mcp.example.test/sse",
    )
    with pytest.raises(RuntimeError, match="Host-side MCP tools are disabled"):
        await create_cognition_agent(
            _strict_agent_params(tmp_path, mcp_configs=[config])
        )


@pytest.mark.asyncio
async def test_strict_agent_does_not_inject_host_browser_search_or_inspection(
    tmp_path,
) -> None:
    clear_agent_cache()
    with patch(
        "server.app.agent.cognition_agent.create_deep_agent",
        return_value=AsyncMock(),
    ) as create:
        await create_cognition_agent(_strict_agent_params(tmp_path))
    _, kwargs = create.call_args
    assert kwargs["tools"] == []
    clear_agent_cache()


@pytest.mark.asyncio
async def test_cached_graph_resolves_each_runs_current_sandbox_dynamically(
    tmp_path,
) -> None:
    clear_agent_cache()
    first_sandbox = object()
    second_sandbox = object()
    graph = object()
    settings = _settings(
        tmp_path,
        sandbox_backend="local",
        unsafe_local_execution=True,
        allow_host_tools=False,
        allow_api_python_tools=False,
    )
    with (
        patch(
            "server.app.agent.cognition_agent._create_sandbox",
            side_effect=[first_sandbox, second_sandbox],
        ),
        patch(
            "server.app.agent.cognition_agent.create_deep_agent",
            return_value=graph,
        ) as create,
    ):
        first = await create_cognition_agent(
            _strict_agent_params(
                tmp_path / "session-one",
                settings=settings,
                manifest_digest="a" * 64,
            )
        )
        second = await create_cognition_agent(
            _strict_agent_params(
                tmp_path / "session-two",
                settings=settings,
                manifest_digest="a" * 64,
            )
        )

    assert create.call_count == 1
    assert first.agent is second.agent is graph
    backend_factory = create.call_args.kwargs["backend"]
    first_context = SimpleNamespace(sandbox_backend=first.sandbox_backend)
    second_context = SimpleNamespace(sandbox_backend=second.sandbox_backend)
    assert backend_factory(SimpleNamespace(context=first_context)) is first_sandbox
    assert backend_factory(SimpleNamespace(context=second_context)) is second_sandbox
    with pytest.raises(RuntimeError, match="no assigned sandbox"):
        backend_factory(SimpleNamespace(context=SimpleNamespace()))
    clear_agent_cache()


@pytest.mark.asyncio
async def test_explicit_empty_agent_tool_list_does_not_expand_to_registry_tools(
    tmp_path,
) -> None:
    registry = MemoryConfigRegistry()
    store = DefaultConfigStore(registry, workspace_path=tmp_path)
    await store.upsert_tool(
        ToolRegistration(
            name="host-python",
            code="def host_python(): return 'unsafe'",
            scope={"tenant": "acme"},
            source="api",
        )
    )
    resolver = RuntimeResolver(
        store,
        _settings(
            tmp_path,
            allow_api_python_tools=False,
        ),
    )

    assert (
        await resolver.build_tools(
            {"tenant": "acme"},
            allowed_tool_names=[],
        )
        == []
    )
    with pytest.raises(RuntimeError, match="cannot be loaded in strict mode"):
        await resolver.build_tools(
            {"tenant": "acme"},
            allowed_tool_names=["host-python"],
        )


class _FakeDockerExecution:
    def __init__(self) -> None:
        self.files = {
            "source.txt": b"first\nsecond\n",
            "binary.bin": b"\xff\x00",
        }
        self.terminated = False

    def execute(self, command: str, timeout: int | None = None) -> Any:
        del command, timeout
        return type(
            "Result",
            (),
            {"output": "sandbox", "exit_code": 0, "truncated": False},
        )()

    def read_file_bytes(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def read_file(self, path: str) -> str:
        return self.read_file_bytes(path).decode()

    def path_exists(self, path: str) -> bool:
        return path in self.files

    def write_file(self, path: str, content: str) -> None:
        self.files[path] = content.encode()

    def write_file_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = content

    def list_files(self, path: str) -> list[dict[str, Any]]:
        del path
        return [
            {"path": f"/{name}", "is_dir": False, "size": len(content)}
            for name, content in self.files.items()
        ]

    def grep_files(
        self,
        pattern: str,
        path: str,
        glob: str | None,
    ) -> list[dict[str, Any]]:
        del path, glob
        return [
            {"path": "/source.txt", "line": 1, "text": "first"}
        ] if pattern == "first" else []

    def glob_files(self, pattern: str, path: str) -> list[dict[str, Any]]:
        del path
        return [
            {"path": "/source.txt", "is_dir": False, "size": 13}
        ] if pattern == "*.txt" else []

    def terminate(self) -> None:
        self.terminated = True


def test_docker_file_tools_route_only_through_sandbox_backend(tmp_path) -> None:
    backend = CognitionDockerSandboxBackend(
        root_dir=tmp_path,
        sandbox_id="strict-files",
    )
    fake = _FakeDockerExecution()
    backend._docker_backend = fake

    read = backend.read("/source.txt", offset=1, limit=1)
    assert read.error is None
    assert read.file_data == {"content": "second\n", "encoding": "utf-8"}
    binary = backend.read("/binary.bin")
    assert binary.file_data is not None
    assert binary.file_data["encoding"] == "base64"

    assert backend.write("/new.txt", "new").error is None
    assert fake.files["new.txt"] == b"new"
    assert backend.edit("/new.txt", "new", "updated").error is None
    assert fake.files["new.txt"] == b"updated"
    assert backend.ls("/").error is None
    assert backend.grep("first", "/").matches
    assert backend.glob("*.txt", "/").matches
    assert backend.download_files(["/source.txt"])[0].content == b"first\nsecond\n"
    assert backend.upload_files([("/uploaded.txt", b"uploaded")])[0].error is None
    assert fake.files["uploaded.txt"] == b"uploaded"

    # No model-controlled path may traverse the sandbox root or mutate
    # Cognition's protected configuration tree.
    assert backend.read("../../etc/passwd").error is not None
    assert backend.write("/.cognition/config.yaml", "bad").error is not None
    assert backend.upload_files([("../escape", b"bad")])[0].error == "invalid_path"

    backend.terminate()
    assert fake.terminated
    assert backend._docker_backend is None


@pytest.mark.parametrize(
    "url",
    [
        "https://callbacks.example.test/hook",
        "https://callbacks.example.test:443/hook?event=done",
    ],
)
def test_callback_requires_exact_operator_approved_https_origin(url: str) -> None:
    settings = _settings(
        callback_allowed_origins=["https://callbacks.example.test"],
    )
    assert (
        _approved_callback_origin(url, settings)
        == "https://callbacks.example.test"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://callbacks.example.test/hook",
        "http://callbacks.example.test/hook",
        "https://user:password@callbacks.example.test/hook",
        "https://callbacks.example.test/hook#fragment",
        "https://callbacks.example.test:bad/hook",
        "https://other.example.test/hook",
    ],
)
def test_callback_defaults_to_denied_and_rejects_unsafe_urls(url: str) -> None:
    with pytest.raises(HTTPException) as exc_info:
        _approved_callback_origin(url, _settings(callback_allowed_origins=[]))
    assert exc_info.value.status_code == 403
