"""Output formatting for Phase 4.

- Syntax highlighting
- Diff visualization
- Collapsible sections
- Rich markdown rendering
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class DiffHunk:
    """A single hunk in a diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    lines: list[tuple[str, str]]  # (marker, content)


class DiffFormatter:
    """Format diffs for display."""

    def format_unified_diff(self, diff_text: str) -> str:
        """Format a unified diff for display."""
        lines = diff_text.split("\n")
        formatted = []

        for line in lines:
            if line.startswith("+++") or line.startswith("---"):
                formatted.append(f"📄 {line}")
            elif line.startswith("@@"):
                formatted.append(f"\n{line}")
            elif line.startswith("+"):
                formatted.append(f"✅ {line}")
            elif line.startswith("-"):
                formatted.append(f"❌ {line}")
            else:
                formatted.append(f"   {line}")

        return "\n".join(formatted)

    def format_compact_diff(self, old: str, new: str, context_lines: int = 3) -> str:
        """Create a compact diff between two strings."""
        old_lines = old.split("\n")
        new_lines = new.split("\n")

        # Simple LCS-based diff (can be enhanced)
        diff_lines = []

        i = j = 0
        while i < len(old_lines) or j < len(new_lines):
            if i < len(old_lines) and j < len(new_lines) and old_lines[i] == new_lines[j]:
                diff_lines.append((" ", old_lines[i]))
                i += 1
                j += 1
            elif j < len(new_lines):
                diff_lines.append(("+", new_lines[j]))
                j += 1
            elif i < len(old_lines):
                diff_lines.append(("-", old_lines[i]))
                i += 1

        # Format with context
        result = []
        last_was_change = False

        for i, (marker, content) in enumerate(diff_lines):
            if marker != " ":
                # Add context before change
                if not last_was_change:
                    context_start = max(0, i - context_lines)
                    for k in range(context_start, i):
                        result.append(f" {diff_lines[k][1]}")
                    if context_start > 0:
                        result.append("...")

                result.append(f"{marker}{content}")
                last_was_change = True
            else:
                if last_was_change:
                    # Add context after change
                    context_end = min(len(diff_lines), i + context_lines)
                    for k in range(i, context_end):
                        result.append(f" {diff_lines[k][1]}")
                    if context_end < len(diff_lines):
                        result.append("...")
                    result.append("")
                last_was_change = False

        return "\n".join(result)

    def create_file_diff_summary(self, file_path: str, diff_text: str) -> str:
        """Create a summary of a file diff."""
        # Count lines starting with + or -
        additions = sum(1 for line in diff_text.split("\n") if line.startswith("+"))
        deletions = sum(1 for line in diff_text.split("\n") if line.startswith("-"))

        return f"📄 {file_path}: +{additions} -{deletions}"


class SyntaxHighlighter:
    """Basic syntax highlighting for code."""

    # Simple regex-based highlighting
    KEYWORDS = [
        "def",
        "class",
        "if",
        "else",
        "elif",
        "for",
        "while",
        "return",
        "import",
        "from",
        "try",
        "except",
        "finally",
        "with",
        "as",
        "async",
        "await",
        "lambda",
        "yield",
        "raise",
        "assert",
    ]

    def highlight_python(self, code: str) -> str:
        """Apply basic Python syntax highlighting."""
        result = code

        # Keywords
        for kw in self.KEYWORDS:
            result = re.sub(rf"\b{kw}\b", f"**{kw}**", result)

        # Strings
        result = re.sub(r'(".*?"|\'.*?\')', r"`\1`", result)

        # Comments
        result = re.sub(r"(#.*)$", r"//\1", result, flags=re.MULTILINE)

        return result

    def get_language_from_extension(self, file_path: str) -> Optional[str]:
        """Detect language from file extension."""
        ext_map = {
            ".py": "python",
            ".js": "javascript",
            ".ts": "typescript",
            ".jsx": "javascript",
            ".tsx": "typescript",
            ".go": "go",
            ".rs": "rust",
            ".java": "java",
            ".cpp": "cpp",
            ".c": "c",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
            ".kt": "kotlin",
        }

        for ext, lang in ext_map.items():
            if file_path.endswith(ext):
                return lang

        return None

    def highlight(self, code: str, language: Optional[str] = None) -> str:
        """Highlight code in specified language."""
        if language == "python":
            return self.highlight_python(code)
        # Add more languages as needed
        return code


class OutputFormatter:
    """Format agent output for display."""

    def __init__(self):
        self.diff_formatter = DiffFormatter()
        self.syntax_highlighter = SyntaxHighlighter()

    def format_tool_call(
        self,
        tool_name: str,
        args: dict,
        result: any,
        duration_ms: Optional[int] = None,
    ) -> str:
        """Format a tool call and its result."""
        lines = []

        # Header
        duration_str = f" ({duration_ms}ms)" if duration_ms else ""
        lines.append(f"🔧 {tool_name}{duration_str}")

        # Arguments
        if args:
            lines.append("Arguments:")
            for key, value in args.items():
                lines.append(f"  {key}: {value}")

        # Result
        if result is not None:
            lines.append("Result:")
            result_str = str(result)
            if len(result_str) > 500:
                result_str = result_str[:500] + "..."
            lines.append(f"  {result_str}")

        return "\n".join(lines)

    def format_code_block(
        self,
        code: str,
        language: Optional[str] = None,
        file_path: Optional[str] = None,
    ) -> str:
        """Format a code block with syntax highlighting."""
        if not language and file_path:
            language = self.syntax_highlighter.get_language_from_extension(file_path)

        highlighted = self.syntax_highlighter.highlight(code, language)

        header = f"```{language or ''}"
        if file_path:
            header += f" {file_path}"

        return f"{header}\n{highlighted}\n```"

    def format_collapsible(
        self,
        title: str,
        content: str,
        collapsed: bool = False,
    ) -> str:
        """Format content in a collapsible section."""
        # Using HTML details/summary for collapsible sections
        # In a real TUI, this would use proper widget controls
        status = "collapsed" if collapsed else "expanded"
        return f"""
<details {"open" if not collapsed else ""}>
<summary>{title} ({status})</summary>

{content}
</details>
"""

    def format_error(self, error: str, context: Optional[str] = None) -> str:
        """Format an error message."""
        lines = [f"❌ Error: {error}"]

        if context:
            lines.append(f"Context: {context}")

        return "\n".join(lines)

    def format_success(self, message: str) -> str:
        """Format a success message."""
        return f"✅ {message}"

    def format_warning(self, message: str) -> str:
        """Format a warning message."""
        return f"⚠️  {message}"

    def format_info(self, message: str) -> str:
        """Format an info message."""
        return f"ℹ️  {message}"

    def truncate_output(self, output: str, max_lines: int = 50, max_chars: int = 2000) -> str:
        """Truncate long output with indication."""
        lines = output.split("\n")

        if len(lines) > max_lines:
            lines = lines[:max_lines]
            lines.append(f"... ({len(lines) - max_lines} more lines)")

        result = "\n".join(lines)

        if len(result) > max_chars:
            result = result[:max_chars] + f"... ({len(result) - max_chars} more chars)"

        return result

    def format_plan_progress(self, plan: "Plan") -> str:
        """Format the progress of a plan."""
        from server.app.agent.workflows import TaskStatus

        total = len(plan.tasks)
        completed = sum(1 for t in plan.tasks if t.status == TaskStatus.COMPLETED)
        failed = sum(1 for t in plan.tasks if t.status == TaskStatus.FAILED)
        pending = sum(1 for t in plan.tasks if t.status == TaskStatus.PENDING)
        in_progress = sum(1 for t in plan.tasks if t.status == TaskStatus.IN_PROGRESS)

        progress = (completed / total * 100) if total > 0 else 0

        lines = [
            f"📋 Plan: {plan.description}",
            f"Progress: {completed}/{total} ({progress:.0f}%)",
            f"  ✅ Completed: {completed}",
            f"  ❌ Failed: {failed}",
            f"  ⏳ Pending: {pending}",
            f"  🔄 In Progress: {in_progress}",
        ]

        return "\n".join(lines)
