"""Tool block widget for collapsible tool execution display."""

from textual.containers import Container
from textual.reactive import reactive
from textual.widgets import Collapsible, RichLog, Static


class ToolBlock(Container):
    """Collapsible block displaying tool execution.

    Shows tool name, status, and expandable output.
    Streams tool_output events in real-time.
    """

    tool_name = reactive("")
    status = reactive("running")  # running, success, error
    exit_code = reactive(0)
    expanded = reactive(False)

    DEFAULT_CSS = """
    ToolBlock {
        width: 100%;
        height: auto;
        margin: 1 0;
    }
    
    .tool-block-collapsed {
        background: $surface;
        border: solid $primary-darken-2;
        padding: 0 1;
    }
    
    .tool-block-expanded {
        background: $surface-darken-1;
        border: solid $primary;
        padding: 0;
    }
    
    .tool-block-header {
        height: 1;
        color: $text;
        text-style: bold;
    }
    
    .tool-block-header.running {
        color: $warning;
    }
    
    .tool-block-header.success {
        color: $success;
    }
    
    .tool-block-header.error {
        color: $error;
    }
    
    .tool-block-content {
        height: auto;
        max-height: 20;
        padding: 1;
    }
    
    RichLog {
        background: $surface-darken-2;
        border: none;
        height: auto;
        max-height: 15;
    }
    """

    def __init__(
        self,
        tool_name: str,
        tool_input: dict | None = None,
        *args,
        **kwargs,
    ) -> None:
        """Initialize tool block.

        Args:
            tool_name: Name of the tool being executed.
            tool_input: Input arguments for the tool.
        """
        super().__init__(*args, **kwargs)
        self.tool_name = tool_name
        self.tool_input = tool_input or {}
        self._stdout_lines: list[str] = []
        self._stderr_lines: list[str] = []

    def compose(self) -> "ComposeResult":
        """Compose the tool block layout."""
        from textual.app import ComposeResult

        # Build header text
        header_text = self._build_header()

        # Create collapsible container
        with Collapsible(title=header_text, collapsed=True):
            with Container(classes="tool-block-content"):
                # Tool input (if any)
                if self.tool_input:
                    input_str = self._format_input(self.tool_input)
                    yield Static(f"[dim]Input:[/dim]\n{input_str}")

                # Output log
                yield RichLog(id="tool-output", wrap=True)

    def _build_header(self) -> str:
        """Build the header text with status indicator."""
        indicators = {
            "running": "⟳",
            "success": "✓",
            "error": "✗",
        }
        indicator = indicators.get(self.status, "?")

        if self.status == "error" and self.exit_code != 0:
            return f"{indicator} {self.tool_name} (exit: {self.exit_code})"
        return f"{indicator} {self.tool_name}"

    def _format_input(self, tool_input: dict) -> str:
        """Format tool input for display."""
        lines = []
        for key, value in tool_input.items():
            if isinstance(value, str) and len(value) > 100:
                # Truncate long strings
                value = value[:100] + "..."
            lines.append(f"  {key}: {value}")
        return "\n".join(lines) if lines else "  (none)"

    def on_mount(self) -> None:
        """Initialize styling."""
        self._update_style()

    def watch_status(self) -> None:
        """Update when status changes."""
        self._update_style()
        self._update_header()

    def watch_exit_code(self) -> None:
        """Update when exit code changes."""
        if self.status != "running":
            self._update_header()

    def _update_style(self) -> None:
        """Apply status-based styling."""
        self.remove_class("running", "success", "error")
        self.add_class(self.status)

    def _update_header(self) -> None:
        """Update the collapsible header."""
        collapsible = self.query_one(Collapsible)
        collapsible.title = self._build_header()

    def append_output(self, chunk: str, stream: str = "stdout") -> None:
        """Append output chunk to the log.

        Args:
            chunk: Text chunk to append.
            stream: "stdout" or "stderr".
        """
        try:
            log = self.query_one("#tool-output", RichLog)
            if stream == "stderr":
                log.write(f"[error]{chunk}[/error]")
            else:
                log.write(chunk)
        except Exception:
            # Log widget might not be ready yet
            if stream == "stderr":
                self._stderr_lines.append(chunk)
            else:
                self._stdout_lines.append(chunk)

    def set_completed(self, exit_code: int = 0) -> None:
        """Mark tool execution as completed.

        Args:
            exit_code: The exit code from tool execution.
        """
        self.exit_code = exit_code
        self.status = "error" if exit_code != 0 else "success"

    def set_diff(self, files_changed: list[str], diff_preview: str) -> None:
        """Display diff output for apply_patch tool.

        Args:
            files_changed: List of files that were changed.
            diff_preview: The unified diff output.
        """
        log = self.query_one("#tool-output", RichLog)

        log.write(f"[dim]Files changed: {', '.join(files_changed)}[/dim]")
        log.write("")

        # Colorize diff
        for line in diff_preview.split("\n"):
            if line.startswith("+"):
                log.write(f"[green]{line}[/green]")
            elif line.startswith("-"):
                log.write(f"[red]{line}[/red]")
            elif line.startswith("@@"):
                log.write(f"[cyan]{line}[/cyan]")
            else:
                log.write(line)

    def set_test_results(self, summary: str) -> None:
        """Display test results.

        Args:
            summary: Test summary string (e.g., "5 passed, 0 failed").
        """
        log = self.query_one("#tool-output", RichLog)
        log.write(f"[dim]Results: {summary}[/dim]")
