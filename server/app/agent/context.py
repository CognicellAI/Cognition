"""Context management for Phase 4.

- Automatic project indexing
- File relevance scoring
- Smart file inclusion in context
- Long-term memory via StoreBackend
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from server.app.execution.sandbox import LocalSandbox


@dataclass
class FileInfo:
    """Information about a file in the project."""

    path: str
    size: int
    language: Optional[str] = None
    last_modified: Optional[float] = None
    importance_score: float = 0.0
    content_preview: Optional[str] = None


@dataclass
class ProjectIndex:
    """Index of all files in a project with metadata."""

    root_path: str
    files: dict[str, FileInfo] = field(default_factory=dict)
    total_size: int = 0
    file_count: int = 0

    def add_file(self, info: FileInfo) -> None:
        """Add a file to the index."""
        self.files[info.path] = info
        self.total_size += info.size
        self.file_count += 1

    def get_file(self, path: str) -> Optional[FileInfo]:
        """Get file info by path."""
        return self.files.get(path)

    def to_dict(self) -> dict:
        """Serialize to dictionary."""
        return {
            "root_path": self.root_path,
            "file_count": self.file_count,
            "total_size": self.total_size,
            "files": {
                path: {
                    "path": info.path,
                    "size": info.size,
                    "language": info.language,
                    "importance_score": info.importance_score,
                }
                for path, info in self.files.items()
            },
        }


class FileRelevanceScorer:
    """Scores file relevance based on various factors."""

    # File patterns that indicate importance
    IMPORTANT_PATTERNS = [
        r"README",
        r"main\.",
        r"app\.",
        r"index\.",
        r"config\.",
        r"settings\.",
        r"pyproject\.toml",
        r"package\.json",
        r"requirements\.txt",
    ]

    # Files to exclude from indexing
    EXCLUDE_PATTERNS = [
        r"\.git/",
        r"__pycache__/",
        r"\.pytest_cache/",
        r"node_modules/",
        r"\.venv/",
        r"\.env",
        r"\.pyc$",
        r"\.log$",
    ]

    def __init__(self):
        self.important_regex = [re.compile(p) for p in self.IMPORTANT_PATTERNS]
        self.exclude_regex = [re.compile(p) for p in self.EXCLUDE_PATTERNS]

    def should_exclude(self, path: str) -> bool:
        """Check if a file should be excluded from indexing."""
        for pattern in self.exclude_regex:
            if pattern.search(path):
                return True
        return False

    def score_importance(self, path: str, content: Optional[str] = None) -> float:
        """Score how important a file is (0.0 to 1.0)."""
        score = 0.0

        # Check against important patterns
        for pattern in self.important_regex:
            if pattern.search(path):
                score += 0.3

        # Boost for certain file types
        if path.endswith((".py", ".js", ".ts", ".go", ".rs")):
            score += 0.1

        # Boost for test files
        if "test" in path.lower() or "spec" in path.lower():
            score += 0.15

        # Cap at 1.0
        return min(score, 1.0)

    def score_relevance_to_query(
        self, query: str, path: str, content: Optional[str] = None
    ) -> float:
        """Score how relevant a file is to a specific query."""
        query_lower = query.lower()
        score = 0.0

        # Filename match
        if query_lower in path.lower():
            score += 0.5

        # Content match
        if content and query_lower in content.lower():
            # Count occurrences
            count = content.lower().count(query_lower)
            score += min(count * 0.1, 0.5)

        return min(score, 1.0)


class ContextManager:
    """Manages project context and file inclusion.

    Provides:
    - Project indexing
    - Relevance-based file selection
    - Context window management
    - Long-term memory storage
    """

    def __init__(self, sandbox: LocalSandbox, max_context_files: int = 20):
        self.sandbox = sandbox
        self.max_context_files = max_context_files
        self.index: Optional[ProjectIndex] = None
        self.scorer = FileRelevanceScorer()

    def build_index(self) -> ProjectIndex:
        """Build or refresh the project index."""
        self.index = ProjectIndex(root_path=str(self.sandbox.root_dir))

        # Find all files
        result = self.sandbox.execute('find . -type f -not -path "./\.*" | head -1000')

        for line in result.output.strip().split("\n"):
            path = line.strip()
            if not path or self.scorer.should_exclude(path):
                continue

            # Get file info
            stat_result = self.sandbox.execute(
                f'stat -f%z "{path}" 2>/dev/null || stat -c%s "{path}" 2>/dev/null || echo "0"'
            )
            try:
                size = int(stat_result.output.strip().split("\n")[0])
            except (ValueError, IndexError):
                size = 0

            # Detect language
            language = self._detect_language(path)

            # Create file info
            info = FileInfo(
                path=path,
                size=size,
                language=language,
                importance_score=self.scorer.score_importance(path),
            )

            self.index.add_file(info)

        return self.index

    def _detect_language(self, path: str) -> Optional[str]:
        """Detect programming language from file extension."""
        ext = Path(path).suffix.lower()
        lang_map = {
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
            ".h": "c",
            ".hpp": "cpp",
            ".rb": "ruby",
            ".php": "php",
            ".swift": "swift",
            ".kt": "kotlin",
        }
        return lang_map.get(ext)

    def get_relevant_files(
        self,
        query: str,
        limit: int = 10,
    ) -> list[FileInfo]:
        """Get files most relevant to a query."""
        if not self.index:
            self.build_index()

        if not self.index:
            return []

        # Score all files
        scored_files = []
        for path, info in self.index.files.items():
            relevance = self.scorer.score_relevance_to_query(query, path)
            # Combine importance and relevance
            final_score = (info.importance_score * 0.3) + (relevance * 0.7)
            scored_files.append((final_score, info))

        # Sort by score and return top files
        scored_files.sort(key=lambda x: x[0], reverse=True)
        return [info for score, info in scored_files[:limit]]

    def format_context_for_llm(
        self,
        query: str,
        include_files: bool = True,
    ) -> str:
        """Format context information for LLM prompt."""
        if not self.index:
            self.build_index()

        context_parts = []

        # Project overview
        if self.index:
            context_parts.append(f"Project has {self.index.file_count} files")

            # Languages used
            languages = set()
            for info in self.index.files.values():
                if info.language:
                    languages.add(info.language)
            if languages:
                context_parts.append(f"Languages: {', '.join(sorted(languages))}")

        # Relevant files for this query
        if include_files:
            relevant = self.get_relevant_files(query, limit=self.max_context_files)
            if relevant:
                context_parts.append("\nMost relevant files:")
                for info in relevant[:5]:
                    context_parts.append(f"  - {info.path} ({info.language or 'unknown'})")

        return "\n".join(context_parts)

    def get_file_content(self, path: str, max_lines: int = 100) -> Optional[str]:
        """Get content of a specific file."""
        result = self.sandbox.execute(f'head -n {max_lines} "{path}"')
        if result.exit_code == 0:
            return result.output
        return None

    def save_memory(self, key: str, content: str) -> None:
        """Save to long-term memory (if store available)."""
        # This would integrate with StoreBackend if available
        # For now, just log it
        import logging

        logging.getLogger(__name__).info(f"Memory: {key} = {content[:50]}...")

    def load_memory(self, key: str) -> Optional[str]:
        """Load from long-term memory."""
        # This would integrate with StoreBackend if available
        return None


import re  # Import at end to avoid circular import issues
