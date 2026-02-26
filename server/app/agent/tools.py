"""Built-in tools for Cognition agents.

This module contains tools that are built into the Cognition agent runtime.
These tools cover capabilities missing from the core `deepagents` library,
specifically web browsing and search.
"""

from __future__ import annotations

import asyncio
import html.parser
import inspect
import re
import urllib.parse
from typing import Any, ClassVar

import httpx
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field


class BrowserToolInput(BaseModel):
    """Input for BrowserTool."""

    url: str = Field(..., description="The URL to fetch content from")
    format: str = Field(
        default="markdown",
        description="The format to return the content in (text, markdown, or html). Defaults to markdown.",
    )
    timeout: float = Field(default=30.0, description="Optional timeout in seconds (max 120)")


class BrowserTool(BaseTool):
    """Tool for fetching and reading web pages."""

    name: str = "webfetch"
    description: str = """- Fetches content from a specified URL
- Takes a URL and optional format as input
- Fetches the URL content, converts to requested format (markdown by default)
- Returns the content in the specified format
- Use this tool when you need to retrieve and analyze web content

Usage notes:
  - The URL must be a fully-formed valid URL
  - HTTP URLs will be automatically upgraded to HTTPS
  - Format options: "markdown" (default), "text", or "html"
  - This tool is read-only and does not modify any files
  - Results may be summarized if the content is very large"""
    args_schema: ClassVar[type[BaseModel]] = BrowserToolInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        """Run the tool synchronously."""
        return asyncio.run(self._arun(*args, **kwargs))

    async def _arun(self, url: str, format: str = "markdown", timeout: float = 30.0) -> str:
        """Run the tool asynchronously.

        Args:
            url: URL to fetch.
            format: Output format (text, markdown, html).
            timeout: Request timeout in seconds.

        Returns:
            Fetched content string.
        """
        # Validate URL
        if not url.startswith(("http://", "https://")):
            raise ValueError("URL must start with http:// or https://")

        # Cap timeout
        timeout = min(timeout, 120.0)

        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }

        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            try:
                response = await client.get(url, headers=headers)
                response.raise_for_status()

                # Check content type
                content_type = response.headers.get("content-type", "").lower()
                if "text/html" not in content_type and "text/plain" not in content_type:
                    return f"Error: Unsupported content type: {content_type}. Only text/html and text/plain are supported."

                html_content = response.text

                if format == "html":
                    return html_content
                elif format == "text":
                    return self._extract_text(html_content)
                elif format == "markdown":
                    return self._convert_to_markdown(html_content)
                else:
                    return f"Error: Unknown format '{format}'"

            except httpx.RequestError as e:
                return f"Error fetching URL: {str(e)}"
            except httpx.HTTPStatusError as e:
                return f"HTTP Error {e.response.status_code}: {e.response.reason_phrase}"
            except Exception as e:
                return f"Error: {str(e)}"

    def _extract_text(self, html_content: str) -> str:
        """Extract text from HTML using simple parsing."""

        class TextExtractor(html.parser.HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.text: list[str] = []
                self.skip = False
                self.skip_tags = {
                    "script",
                    "style",
                    "noscript",
                    "iframe",
                    "object",
                    "embed",
                    "head",
                }

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag in self.skip_tags:
                    self.skip = True

            def handle_endtag(self, tag: str) -> None:
                if tag in self.skip_tags:
                    self.skip = False
                if tag in {"p", "div", "br", "li", "h1", "h2", "h3", "h4", "h5", "h6"}:
                    self.text.append("\n")

            def handle_data(self, data: str) -> None:
                if not self.skip:
                    clean = data.strip()
                    if clean:
                        self.text.append(clean + " ")

        parser = TextExtractor()
        parser.feed(html_content)
        return "".join(parser.text).strip()

    def _convert_to_markdown(self, html_content: str) -> str:
        """Convert HTML to simple Markdown-like text."""

        class MarkdownExtractor(html.parser.HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.output: list[str] = []
                self.skip = False
                self.skip_tags = {
                    "script",
                    "style",
                    "noscript",
                    "iframe",
                    "object",
                    "embed",
                    "head",
                }
                self.in_link = False
                self.link_url = ""

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if tag in self.skip_tags:
                    self.skip = True
                    return

                if tag in {"h1", "h2", "h3"}:
                    self.output.append(f"\n\n# ")
                elif tag == "p":
                    self.output.append("\n\n")
                elif tag == "li":
                    self.output.append("\n- ")
                elif tag == "a":
                    self.in_link = True
                    for k, v in attrs:
                        if k == "href":
                            self.link_url = v or ""
                            break
                    self.output.append("[")
                elif tag in {"b", "strong"}:
                    self.output.append("**")
                elif tag in {"i", "em"}:
                    self.output.append("*")
                elif tag == "code":
                    self.output.append("`")
                elif tag == "pre":
                    self.output.append("\n```\n")

            def handle_endtag(self, tag: str) -> None:
                if tag in self.skip_tags:
                    self.skip = False
                    return

                if tag in {"h1", "h2", "h3", "p"}:
                    self.output.append("\n")
                elif tag == "a":
                    self.in_link = False
                    self.output.append(f"]({self.link_url})")
                elif tag in {"b", "strong"}:
                    self.output.append("**")
                elif tag in {"i", "em"}:
                    self.output.append("*")
                elif tag == "code":
                    self.output.append("`")
                elif tag == "pre":
                    self.output.append("\n```\n")

            def handle_data(self, data: str) -> None:
                if not self.skip:
                    text = data.replace("\n", " ")
                    if not self.in_link and not text.strip():
                        return
                    self.output.append(text)

        parser = MarkdownExtractor()
        parser.feed(html_content)
        text = "".join(parser.output)
        return re.sub(r"\n{3,}", "\n\n", text).strip()


class SearchToolInput(BaseModel):
    """Input for SearchTool."""

    query: str = Field(..., description="The search query")
    limit: int = Field(default=10, description="Maximum number of results to return")


class SearchTool(BaseTool):
    """Tool for searching the web."""

    name: str = "websearch"
    description: str = """- Search the web for information using a privacy-friendly search engine
- Use this tool for accessing information beyond knowledge cutoff, current events, or documentation
- Returns a list of search results with titles, links, and snippets

Usage notes:
  - Searches are performed automatically
  - Returns up to 'limit' results (default 10)
"""
    args_schema: ClassVar[type[BaseModel]] = SearchToolInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        return asyncio.run(self._arun(*args, **kwargs))

    async def _arun(self, query: str, limit: int = 10) -> str:
        """Search using DuckDuckGo HTML interface."""
        url = "https://html.duckduckgo.com/html/"
        data = {"q": query}
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
            "Content-Type": "application/x-www-form-urlencoded",
        }

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.post(url, data=data, headers=headers)
                response.raise_for_status()
                return self._parse_ddg_results(response.text, limit)
            except Exception as e:
                return f"Error performing search: {str(e)}"

    def _parse_ddg_results(self, html_content: str, limit: int) -> str:
        """Parse DuckDuckGo HTML results."""
        results_text = []

        # Regex to find result blocks (robust against simple HTML structure)
        link_pattern = re.compile(
            r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.DOTALL
        )
        snippet_pattern = re.compile(
            r'<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>', re.DOTALL
        )

        # Find all matches
        links = link_pattern.findall(html_content)
        snippets = snippet_pattern.findall(html_content)

        count = min(len(links), limit)
        for i in range(count):
            link_url, title_html = links[i]
            snippet_html = snippets[i] if i < len(snippets) else ""

            title = re.sub(r"<[^>]+>", "", title_html).strip()
            snippet = re.sub(r"<[^>]+>", "", snippet_html).strip()

            link_url = urllib.parse.unquote(link_url)
            if "uddg=" in link_url:
                try:
                    parsed = urllib.parse.urlparse(link_url)
                    qs = urllib.parse.parse_qs(parsed.query)
                    if "uddg" in qs:
                        link_url = qs["uddg"][0]
                except Exception:
                    pass

            results_text.append(f"## {title}\nURL: {link_url}\n{snippet}\n")

        if not results_text:
            return "No results found."

        return "\n".join(results_text)


class InspectPackageToolInput(BaseModel):
    """Input for InspectPackageTool."""

    package_name: str = Field(..., description="The name of the package or module to inspect")


class InspectPackageTool(BaseTool):
    """Tool for inspecting Python packages."""

    name: str = "inspect_package"
    description: str = "Inspects a Python package or module to list its classes and functions. Useful for exploring available functionality."
    args_schema: ClassVar[type[BaseModel]] = InspectPackageToolInput

    def _run(self, *args: Any, **kwargs: Any) -> Any:
        return self._run_sync(*args, **kwargs)

    def _run_sync(self, package_name: str) -> str:
        try:
            import importlib
            import pkgutil

            # Import the module
            module = importlib.import_module(package_name)

            output = [f"# Inspection of `{package_name}`", ""]

            # List submodules if it's a package
            if hasattr(module, "__path__"):
                output.append("## Submodules:")
                for _, name, _ in pkgutil.iter_modules(module.__path__):
                    output.append(f"- {name}")
                output.append("")

            # List classes and functions
            classes = []
            functions = []

            for name, obj in inspect.getmembers(module):
                if name.startswith("_"):
                    continue
                if inspect.isclass(obj):
                    if obj.__module__ == module.__name__:
                        classes.append(name)
                elif inspect.isfunction(obj):
                    if obj.__module__ == module.__name__:
                        functions.append(name)

            if classes:
                output.append("## Classes:")
                for c in classes:
                    output.append(f"- {c}")
                output.append("")

            if functions:
                output.append("## Functions:")
                for f in functions:
                    output.append(f"- {f}")

            return "\n".join(output)

        except ImportError:
            return f"Error: Package '{package_name}' not found."
        except Exception as e:
            return f"Error inspecting package: {str(e)}"

    async def _arun(self, package_name: str) -> str:
        return self._run_sync(package_name)
