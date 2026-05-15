"""Unit tests for #23: ConfigStore tool bridge.

Covers:
- ToolRegistration model: path XOR code validation
- RuntimeResolver.build_tools(): code-based and path-based loading
- GET /tools: returns tools from ConfigStore
- POST /tools: accepts code and path, validates XOR
- Disabled tools are skipped
- Scoped tools loaded for matching scope
- Load errors logged and skipped
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.tools import BaseTool, tool

# ---------------------------------------------------------------------------
# ToolRegistration model validation
# ---------------------------------------------------------------------------


class TestToolRegistrationModel:
    def test_path_only_is_valid(self):
        from server.app.storage.config_models import ToolRegistration

        t = ToolRegistration(name="my-tool", path="mypackage.tools.foo")
        assert t.path == "mypackage.tools.foo"
        assert t.code is None

    def test_code_only_is_valid(self):
        from server.app.storage.config_models import ToolRegistration

        t = ToolRegistration(name="my-tool", code="def my_tool(): pass")
        assert t.code == "def my_tool(): pass"
        assert t.path is None

    def test_neither_raises(self):
        from pydantic import ValidationError

        from server.app.storage.config_models import ToolRegistration

        with pytest.raises(ValidationError, match="Either 'path' or 'code'"):
            ToolRegistration(name="my-tool")

    def test_both_raises(self):
        from pydantic import ValidationError

        from server.app.storage.config_models import ToolRegistration

        with pytest.raises(ValidationError, match="either 'path' or 'code'"):
            ToolRegistration(name="my-tool", path="a.b", code="pass")

    def test_name_validation(self):
        from pydantic import ValidationError

        from server.app.storage.config_models import ToolRegistration

        with pytest.raises(ValidationError):
            ToolRegistration(name="invalid name!", path="a.b")


# ---------------------------------------------------------------------------
# RuntimeResolver.build_tools(): code-based tools
# ---------------------------------------------------------------------------


class TestLoadCodeTools:
    @pytest.mark.asyncio
    async def test_code_tool_loaded_as_base_tool(self):
        """A @tool-decorated function in code is loaded as a BaseTool."""
        from server.app.agent.resolver import RuntimeResolver
        from server.app.storage.config_models import ToolRegistration

        code = textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def say_hello(name: str) -> str:
                \"\"\"Say hello to someone.\"\"\"
                return f"Hello, {name}!"
        """)

        mock_store = MagicMock()
        mock_store.list_tools = AsyncMock(
            return_value=[ToolRegistration(name="say-hello", code=code, enabled=True)]
        )

        resolver = RuntimeResolver(config_store=mock_store, settings=MagicMock())
        tools = await resolver.build_tools(scope=None)

        assert len(tools) == 1
        assert isinstance(tools[0], BaseTool)
        assert tools[0].name == "say_hello"

    @pytest.mark.asyncio
    async def test_multiple_tools_in_one_code_block(self):
        """Multiple @tool functions in one code block all get loaded."""
        from server.app.agent.resolver import RuntimeResolver
        from server.app.storage.config_models import ToolRegistration

        code = textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def tool_a(x: str) -> str:
                \"\"\"Tool A.\"\"\"
                return x

            @tool
            def tool_b(x: str) -> str:
                \"\"\"Tool B.\"\"\"
                return x
        """)

        mock_store = MagicMock()
        mock_store.list_tools = AsyncMock(
            return_value=[ToolRegistration(name="multi-tools", code=code, enabled=True)]
        )

        resolver = RuntimeResolver(config_store=mock_store, settings=MagicMock())
        tools = await resolver.build_tools(scope=None)

        assert len(tools) == 2
        names = {t.name for t in tools}
        assert names == {"tool_a", "tool_b"}

    @pytest.mark.asyncio
    async def test_disabled_tool_is_skipped(self):
        """Disabled ToolRegistration entries are not loaded."""
        from server.app.agent.resolver import RuntimeResolver
        from server.app.storage.config_models import ToolRegistration

        code = textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def disabled_tool(x: str) -> str:
                \"\"\"Disabled.\"\"\"
                return x
        """)

        mock_store = MagicMock()
        mock_store.list_tools = AsyncMock(
            return_value=[ToolRegistration(name="disabled", code=code, enabled=False)]
        )

        resolver = RuntimeResolver(config_store=mock_store, settings=MagicMock())
        tools = await resolver.build_tools(scope=None)

        assert len(tools) == 0

    @pytest.mark.asyncio
    async def test_allowed_tool_names_filters_registry_tools(self):
        from server.app.agent.resolver import RuntimeResolver
        from server.app.storage.config_models import ToolRegistration

        code = textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def allowed_tool(x: str) -> str:
                \"\"\"Allowed tool.\"\"\"
                return x
        """)
        other_code = textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def other_tool(x: str) -> str:
                \"\"\"Other tool.\"\"\"
                return x
        """)

        mock_store = MagicMock()
        mock_store.list_tools = AsyncMock(
            return_value=[
                ToolRegistration(name="allowed-tool", code=code, enabled=True),
                ToolRegistration(name="other-tool", code=other_code, enabled=True),
            ]
        )

        resolver = RuntimeResolver(config_store=mock_store, settings=MagicMock())
        tools = await resolver.build_tools(scope=None, allowed_tool_names=["allowed-tool"])

        assert len(tools) == 1
        assert tools[0].name == "allowed_tool"

    @pytest.mark.asyncio
    async def test_code_with_syntax_error_is_skipped(self):
        """A tool with invalid Python source is skipped without crashing."""
        from server.app.agent.resolver import RuntimeResolver
        from server.app.storage.config_models import ToolRegistration

        mock_store = MagicMock()
        mock_store.list_tools = AsyncMock(
            return_value=[
                ToolRegistration(
                    name="bad-tool",
                    code="def broken(: this is not valid python!!!",
                    enabled=True,
                )
            ]
        )

        resolver = RuntimeResolver(config_store=mock_store, settings=MagicMock())
        tools = await resolver.build_tools(scope=None)

        # Should not raise — just returns empty
        assert len(tools) == 0

    @pytest.mark.asyncio
    async def test_one_bad_tool_does_not_block_others(self):
        """An error loading one tool doesn't prevent other tools from loading."""
        from server.app.agent.resolver import RuntimeResolver
        from server.app.storage.config_models import ToolRegistration

        good_code = textwrap.dedent("""\
            from langchain_core.tools import tool

            @tool
            def good_tool(x: str) -> str:
                \"\"\"Good tool.\"\"\"
                return x
        """)

        mock_store = MagicMock()
        mock_store.list_tools = AsyncMock(
            return_value=[
                ToolRegistration(name="bad-tool", code="def broken(:", enabled=True),
                ToolRegistration(name="good-tool", code=good_code, enabled=True),
            ]
        )

        resolver = RuntimeResolver(config_store=mock_store, settings=MagicMock())
        tools = await resolver.build_tools(scope=None)

        assert len(tools) == 1
        assert tools[0].name == "good_tool"

    @pytest.mark.asyncio
    async def test_missing_config_store_returns_empty(self):
        """If ConfigStore is not initialized, returns empty list gracefully."""
        from server.app.agent.resolver import RuntimeResolver

        resolver = RuntimeResolver(config_store=None, settings=MagicMock())
        tools = await resolver.build_tools(scope=None)

        assert tools == []


# ---------------------------------------------------------------------------
# RuntimeResolver.build_tools(): path-based tools
# ---------------------------------------------------------------------------


class TestLoadPathTools:
    @pytest.mark.asyncio
    async def test_path_tool_loaded_via_importlib(self):
        """A path-based tool is loaded via importlib and BaseTool instances collected."""
        from server.app.agent.resolver import RuntimeResolver
        from server.app.storage.config_models import ToolRegistration

        # Create a real minimal BaseTool subclass so isinstance() works
        @tool
        def path_loaded_tool(x: str) -> str:
            """A tool loaded from a module path."""
            return x

        # Create a fake module whose namespace contains our tool
        import types

        fake_module = types.ModuleType("mypackage.tools")
        fake_module.path_loaded_tool = path_loaded_tool  # type: ignore[attr-defined]

        mock_store = MagicMock()
        mock_store.list_tools = AsyncMock(
            return_value=[ToolRegistration(name="path-tool", path="mypackage.tools", enabled=True)]
        )

        resolver = RuntimeResolver(config_store=mock_store, settings=MagicMock())
        with patch("importlib.import_module", return_value=fake_module):
            tools = await resolver.build_tools(scope=None)

        assert len(tools) == 1
        assert isinstance(tools[0], BaseTool)

    @pytest.mark.asyncio
    async def test_path_import_error_is_skipped(self):
        """An ImportError on a path-based tool is skipped without crashing."""
        from server.app.agent.resolver import RuntimeResolver
        from server.app.storage.config_models import ToolRegistration

        mock_store = MagicMock()
        mock_store.list_tools = AsyncMock(
            return_value=[
                ToolRegistration(name="missing-module", path="nonexistent.module", enabled=True)
            ]
        )

        resolver = RuntimeResolver(config_store=mock_store, settings=MagicMock())
        tools = await resolver.build_tools(scope=None)

        assert tools == []


# ---------------------------------------------------------------------------
# POST /tools: API validation
# ---------------------------------------------------------------------------


class TestPostToolsAPI:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        from server.app.api.dependencies import set_config_store
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        set_config_store(DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path))

    def _client(self) -> Any:
        from fastapi.testclient import TestClient

        from server.app.main import app

        return TestClient(app)

    def test_post_with_path_returns_201(self):
        client = self._client()
        response = client.post(
            "/tools",
            json={"name": "path-tool", "path": "mypackage.tools.foo"},
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "path-tool"
        assert data["source_type"] == "api_path"

    def test_post_with_code_returns_201(self):
        client = self._client()
        response = client.post(
            "/tools",
            json={
                "name": "code-tool",
                "code": "from langchain_core.tools import tool\n@tool\ndef code_tool(x: str) -> str:\n    '''A tool.'''\n    return x",
            },
        )
        assert response.status_code == 201
        data = response.json()
        assert data["name"] == "code-tool"
        assert data["source_type"] == "api_code"

    def test_post_with_neither_returns_422(self):
        client = self._client()
        response = client.post("/tools", json={"name": "no-source"})
        assert response.status_code == 422

    def test_post_with_both_returns_422(self):
        client = self._client()
        response = client.post(
            "/tools",
            json={"name": "both", "path": "a.b", "code": "pass"},
        )
        assert response.status_code == 422

    def test_post_without_name_returns_422(self):
        client = self._client()
        response = client.post("/tools", json={"path": "a.b.c"})
        assert response.status_code == 422


# ---------------------------------------------------------------------------
# GET /tools: unified listing
# ---------------------------------------------------------------------------


class TestGetToolsAPI:
    @pytest.fixture(autouse=True)
    def setup(self, tmp_path: Path) -> None:
        from server.app.api.dependencies import set_config_store
        from server.app.storage.config_registry import MemoryConfigRegistry
        from server.app.storage.config_store import DefaultConfigStore

        set_config_store(DefaultConfigStore(MemoryConfigRegistry(), workspace_path=tmp_path))

    def _client(self) -> Any:
        from fastapi.testclient import TestClient

        from server.app.main import app

        return TestClient(app)

    def test_api_registered_tools_appear_in_list(self):
        """Tools registered via POST /tools appear in GET /tools."""
        client = self._client()
        client.post("/tools", json={"name": "listed-tool", "path": "a.b.c"})
        response = client.get("/tools")
        assert response.status_code == 200
        names = [t["name"] for t in response.json()["tools"]]
        assert "listed-tool" in names

    def test_api_registered_tool_has_correct_source_type(self):
        """A path-based API tool has source_type='api_path'."""
        client = self._client()
        client.post("/tools", json={"name": "path-listed", "path": "x.y.z"})
        response = client.get("/tools")
        tool = next(t for t in response.json()["tools"] if t["name"] == "path-listed")
        assert tool["source_type"] == "api_path"

    def test_code_tool_has_api_code_source_type(self):
        """A code-based API tool has source_type='api_code'."""
        client = self._client()
        client.post(
            "/tools",
            json={"name": "code-listed", "code": "pass"},
        )
        response = client.get("/tools")
        tool = next(
            (t for t in response.json()["tools"] if t["name"] == "code-listed"),
            None,
        )
        # code-listed was registered with code="pass" which is valid (just no BaseTool)
        assert tool is not None
        assert tool["source_type"] == "api_code"
