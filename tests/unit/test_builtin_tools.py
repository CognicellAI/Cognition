"""Unit tests for built-in tools logic."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from server.app.agent.tools import BrowserTool, InspectPackageTool, SearchTool


@pytest.mark.asyncio
class TestBrowserTool:
    """Test BrowserTool logic."""

    async def test_fetch_html_text_conversion(self):
        """Test fetching HTML and converting to text."""
        tool = BrowserTool()

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><h1>Hello</h1><p>World</p></body></html>"
        mock_response.headers = {"content-type": "text/html"}

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock) as mock_get:
            mock_get.return_value = mock_response

            # Test text format
            result_text = await tool._arun("http://example.com", format="text")
            assert "Hello" in result_text
            assert "World" in result_text

            # Test markdown format
            result_md = await tool._arun("http://example.com", format="markdown")
            assert "# Hello" in result_md
            assert "World" in result_md

    async def test_invalid_url(self):
        """Test invalid URL handling."""
        tool = BrowserTool()
        with pytest.raises(ValueError):
            await tool._arun("ftp://example.com")


@pytest.mark.asyncio
class TestSearchTool:
    """Test SearchTool logic."""

    async def test_search_parsing(self):
        """Test parsing of DuckDuckGo results."""
        tool = SearchTool()

        # Mock simple DDG HTML structure
        html_content = """
        <div class="result">
            <h2 class="result__title">
                <a class="result__a" href="http://example.com">Example Title</a>
            </h2>
            <div class="result__snippet">
                <a class="result__snippet" href="http://example.com">This is a snippet.</a>
            </div>
        </div>
        """

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = html_content

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock) as mock_post:
            mock_post.return_value = mock_response

            result = await tool._arun("query")
            assert "Example Title" in result
            assert "http://example.com" in result
            assert "This is a snippet" in result


class TestInspectPackageTool:
    """Test InspectPackageTool logic."""

    def test_inspect_json_module(self):
        """Test inspecting the standard json module."""
        tool = InspectPackageTool()
        result = tool._run("json")

        assert "# Inspection of `json`" in result
        assert "## Functions:" in result
        assert "- dumps" in result
        assert "- loads" in result
        # json module imports classes from submodules, so they might not pass the __module__ check
        # depending on implementation. We check for submodules instead.
        assert "## Submodules:" in result

    def test_inspect_nonexistent_package(self):
        """Test inspecting a missing package."""
        tool = InspectPackageTool()
        result = tool._run("nonexistent_package_12345")
        assert "Error: Package 'nonexistent_package_12345' not found" in result
