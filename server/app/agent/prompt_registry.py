"""Prompt Registry for Cognition.

P3-2 Implementation: System prompt management with version tracking,
rollback support, and MLflow Prompt Registry integration.

This module provides:
- Load prompts from MLflow Prompt Registry when configured
- Version tracking and lineage to traces/scores
- Fallback to local prompts when MLflow unavailable
- Configuration-driven prompt source selection
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import structlog

logger = structlog.get_logger(__name__)


# ============================================================================
# Prompt Models
# ============================================================================


class PromptSource(str, Enum):
    """Source types for prompts."""

    LOCAL = "local"
    MLFLOW = "mlflow"


@dataclass
class PromptVersion:
    """A versioned prompt with metadata."""

    name: str
    version: str
    content: str
    source: PromptSource
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    tags: dict[str, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def format(self, **kwargs: Any) -> str:
        """Format the prompt with template variables.

        Args:
            **kwargs: Template variables to substitute

        Returns:
            Formatted prompt string
        """
        try:
            return self.content.format(**kwargs)
        except KeyError as e:
            logger.warning(
                "Prompt formatting failed - missing key",
                prompt_name=self.name,
                missing_key=str(e),
            )
            return self.content
        except Exception as e:
            logger.error(
                "Prompt formatting failed",
                prompt_name=self.name,
                error=str(e),
            )
            return self.content

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "version": self.version,
            "content": self.content,
            "source": self.source.value,
            "created_at": self.created_at.isoformat(),
            "tags": self.tags,
            "metadata": self.metadata,
        }


@dataclass
class PromptLineage:
    """Lineage information linking prompts to traces and scores."""

    prompt_name: str
    prompt_version: str
    session_ids: list[str] = field(default_factory=list)
    trace_ids: list[str] = field(default_factory=list)
    evaluation_scores: dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "session_count": len(self.session_ids),
            "trace_count": len(self.trace_ids),
            "average_score": self.average_score,
            "evaluation_scores": self.evaluation_scores,
        }

    @property
    def average_score(self) -> float:
        """Calculate average evaluation score."""
        if not self.evaluation_scores:
            return 0.0
        return sum(self.evaluation_scores.values()) / len(self.evaluation_scores)


# ============================================================================
# Default System Prompt
# ============================================================================

DEFAULT_SYSTEM_PROMPT = """You are a helpful AI assistant with access to tools.

You can help users by:
- Answering questions about their codebase
- Reading and analyzing files
- Running commands in a sandboxed environment
- Making edits to files

When using tools:
1. Think step by step about what you need to do
2. Use the appropriate tool for each task
3. Verify the results before proceeding
4. Explain your reasoning to the user

Be thorough but efficient. If a task can be completed with fewer tool calls, prefer that approach.
Always prioritize user safety and data integrity.
"""


# ============================================================================
# Prompt Registry Protocol
# ============================================================================


class PromptRegistryBackend(Protocol):
    """Protocol for prompt registry backends."""

    async def get_prompt(
        self,
        name: str,
        version: str | None = None,
        alias: str | None = None,
    ) -> PromptVersion | None:
        """Get a prompt by name.

        Args:
            name: Prompt name
            version: Specific version (optional)
            alias: Alias like "latest" or "production" (optional)

        Returns:
            PromptVersion if found, None otherwise
        """
        ...

    async def list_prompts(self) -> list[dict[str, Any]]:
        """List available prompts.

        Returns:
            List of prompt metadata dictionaries
        """
        ...

    async def get_versions(self, name: str) -> list[str]:
        """Get all versions of a prompt.

        Args:
            name: Prompt name

        Returns:
            List of version strings
        """
        ...


# ============================================================================
# Local Prompt Registry
# ============================================================================


class LocalPromptRegistry:
    """Local file-based prompt registry."""

    def __init__(self, prompts_dir: Path | None = None):
        """Initialize local registry.

        Args:
            prompts_dir: Directory containing prompt files
        """
        if prompts_dir is None:
            prompts_dir = Path(".cognition/prompts")

        self.prompts_dir = prompts_dir
        self._prompts: dict[str, PromptVersion] = {}
        self._load_default_prompt()

    def _load_default_prompt(self) -> None:
        """Load the default system prompt."""
        self._prompts["system"] = PromptVersion(
            name="system",
            version="1.0.0",
            content=DEFAULT_SYSTEM_PROMPT,
            source=PromptSource.LOCAL,
            tags={"default": "true", "type": "system"},
        )

    def _load_from_file(self, name: str) -> PromptVersion | None:
        """Load a prompt from file.

        Args:
            name: Prompt name (filename without extension)

        Returns:
            PromptVersion if file exists, None otherwise
        """
        # Try different extensions
        for ext in [".md", ".txt", ".j2", ".template"]:
            file_path = self.prompts_dir / f"{name}{ext}"
            if file_path.exists():
                try:
                    content = file_path.read_text(encoding="utf-8")
                    stat = file_path.stat()

                    return PromptVersion(
                        name=name,
                        version=f"file-{int(stat.st_mtime)}",
                        content=content,
                        source=PromptSource.LOCAL,
                        created_at=datetime.fromtimestamp(stat.st_mtime),
                        tags={"file": str(file_path), "type": "system"},
                    )
                except Exception as e:
                    logger.error(
                        "Failed to load prompt file",
                        file=str(file_path),
                        error=str(e),
                    )

        return None

    async def get_prompt(
        self,
        name: str,
        version: str | None = None,
        alias: str | None = None,
    ) -> PromptVersion | None:
        """Get a prompt by name."""
        # Return cached prompt if available
        if name in self._prompts:
            return self._prompts[name]

        # Try to load from file
        prompt = self._load_from_file(name)
        if prompt:
            self._prompts[name] = prompt
            return prompt

        return None

    async def list_prompts(self) -> list[dict[str, Any]]:
        """List available prompts."""
        prompts = []

        # Add loaded prompts
        for name, prompt in self._prompts.items():
            prompts.append(
                {
                    "name": name,
                    "version": prompt.version,
                    "source": prompt.source.value,
                }
            )

        # Scan directory for additional prompts
        if self.prompts_dir.exists():
            for file_path in self.prompts_dir.iterdir():
                if file_path.suffix in [".md", ".txt", ".j2", ".template"]:
                    name = file_path.stem
                    if name not in self._prompts:
                        prompts.append(
                            {
                                "name": name,
                                "version": "file",
                                "source": "local",
                            }
                        )

        return prompts

    async def get_versions(self, name: str) -> list[str]:
        """Get all versions of a prompt."""
        if name in self._prompts:
            return [self._prompts[name].version]

        # Check if file exists
        prompt = self._load_from_file(name)
        if prompt:
            return [prompt.version]

        return []


# ============================================================================
# MLflow Prompt Registry
# ============================================================================


class MLflowPromptRegistry:
    """MLflow-based prompt registry."""

    def __init__(self, tracking_uri: str | None = None):
        """Initialize MLflow registry.

        Args:
            tracking_uri: MLflow tracking server URI
        """
        self.tracking_uri = tracking_uri
        self._available = False
        self._mlflow: Any | None = None

        self._init_mlflow()

    def _init_mlflow(self) -> None:
        """Initialize MLflow connection."""
        try:
            import mlflow

            self._mlflow = mlflow

            if self.tracking_uri:
                mlflow.set_tracking_uri(self.tracking_uri)

            # Test connection by listing prompts
            try:
                mlflow.genai.load_prompt("test", allow_multiple=False)
            except Exception:
                # Expected to fail, just testing connection
                pass

            self._available = True
            logger.info(
                "MLflow prompt registry initialized",
                tracking_uri=self.tracking_uri or "default",
            )

        except ImportError:
            logger.warning("MLflow not installed, prompt registry using local mode")
        except Exception as e:
            logger.error("Failed to initialize MLflow prompt registry", error=str(e))

    @property
    def is_available(self) -> bool:
        """Check if MLflow is available."""
        return self._available

    async def get_prompt(
        self,
        name: str,
        version: str | None = None,
        alias: str | None = None,
    ) -> PromptVersion | None:
        """Get a prompt from MLflow."""
        if not self._available or self._mlflow is None:
            return None

        try:
            import mlflow

            # Determine which version to load
            if version:
                prompt_template = mlflow.genai.load_prompt(name, version=version)
            elif alias:
                prompt_template = mlflow.genai.load_prompt(name, alias=alias)
            else:
                prompt_template = mlflow.genai.load_prompt(name)

            # Extract metadata
            prompt_info = mlflow.genai.get_prompt(name)

            return PromptVersion(
                name=name,
                version=version or alias or "latest",
                content=str(prompt_template),
                source=PromptSource.MLFLOW,
                tags={"mlflow_alias": alias} if alias else {},
                metadata={
                    "mlflow_version": prompt_info.version
                    if hasattr(prompt_info, "version")
                    else None,
                    "mlflow_tags": prompt_info.tags if hasattr(prompt_info, "tags") else {},
                },
            )

        except Exception as e:
            logger.error(
                "Failed to load prompt from MLflow",
                name=name,
                version=version,
                error=str(e),
            )
            return None

    async def list_prompts(self) -> list[dict[str, Any]]:
        """List available prompts from MLflow."""
        if not self._available or self._mlflow is None:
            return []

        try:

            # List registered prompts
            prompts = []
            # Note: MLflow's prompt registry API may vary by version
            # This is a simplified implementation

            return prompts

        except Exception as e:
            logger.error("Failed to list prompts from MLflow", error=str(e))
            return []

    async def get_versions(self, name: str) -> list[str]:
        """Get all versions of a prompt from MLflow."""
        if not self._available or self._mlflow is None:
            return []

        try:
            import mlflow

            prompt_info = mlflow.genai.get_prompt(name)
            if hasattr(prompt_info, "versions"):
                return [str(v) for v in prompt_info.versions]

            return []

        except Exception as e:
            logger.error(
                "Failed to get prompt versions from MLflow",
                name=name,
                error=str(e),
            )
            return []


# ============================================================================
# Unified Prompt Registry
# ============================================================================


class PromptRegistry:
    """Unified prompt registry with fallback support."""

    def __init__(
        self,
        prompt_source: PromptSource = PromptSource.LOCAL,
        mlflow_tracking_uri: str | None = None,
        prompts_dir: Path | None = None,
        fallback_to_local: bool = True,
    ):
        """Initialize the prompt registry.

        Args:
            prompt_source: Primary source for prompts
            mlflow_tracking_uri: MLflow tracking URI (for MLflow source)
            prompts_dir: Directory for local prompts
            fallback_to_local: Whether to fall back to local prompts on MLflow failure
        """
        self.prompt_source = prompt_source
        self.fallback_to_local = fallback_to_local

        # Initialize backends
        self._local_registry = LocalPromptRegistry(prompts_dir)
        self._mlflow_registry: MLflowPromptRegistry | None = None

        if prompt_source == PromptSource.MLFLOW:
            self._mlflow_registry = MLflowPromptRegistry(mlflow_tracking_uri)

        # Track prompt usage for lineage
        self._usage_log: list[dict[str, Any]] = []

    async def get_prompt(
        self,
        name: str,
        version: str | None = None,
        alias: str | None = None,
        format_vars: dict[str, Any] | None = None,
    ) -> tuple[str, PromptVersion]:
        """Get a prompt with automatic fallback.

        Args:
            name: Prompt name
            version: Specific version (optional)
            alias: Alias like "production" (optional)
            format_vars: Variables for template formatting

        Returns:
            Tuple of (formatted_content, prompt_version)

        Raises:
            ValueError: If prompt not found and no fallback available
        """
        prompt: PromptVersion | None = None
        used_fallback = False

        # Try primary source first
        if self.prompt_source == PromptSource.MLFLOW and self._mlflow_registry:
            prompt = await self._mlflow_registry.get_prompt(name, version, alias)

        # Fallback to local if needed
        if prompt is None and self.fallback_to_local:
            prompt = await self._local_registry.get_prompt(name, version, alias)
            used_fallback = True

        # If still no prompt, use default
        if prompt is None:
            if name == "system":
                prompt = await self._local_registry.get_prompt("system")
            else:
                raise ValueError(f"Prompt not found: {name}")

        # Format the prompt
        format_vars = format_vars or {}
        formatted_content = prompt.format(**format_vars)

        # Log usage for lineage
        self._usage_log.append(
            {
                "prompt_name": name,
                "version": prompt.version,
                "source": prompt.source.value,
                "used_fallback": used_fallback,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )

        if used_fallback:
            logger.info(
                "Using local fallback prompt",
                prompt_name=name,
                requested_source=self.prompt_source.value,
            )

        return formatted_content, prompt

    async def get_system_prompt(
        self,
        workspace_path: str | None = None,
        custom_vars: dict[str, Any] | None = None,
    ) -> tuple[str, PromptVersion]:
        """Get the system prompt with workspace context.

        Args:
            workspace_path: Path to current workspace
            custom_vars: Additional template variables

        Returns:
            Tuple of (formatted_content, prompt_version)
        """
        format_vars = {"workspace": workspace_path or "."}
        if custom_vars:
            format_vars.update(custom_vars)

        return await self.get_prompt("system", format_vars=format_vars)

    async def list_prompts(self) -> list[dict[str, Any]]:
        """List all available prompts."""
        if self.prompt_source == PromptSource.MLFLOW and self._mlflow_registry:
            mlflow_prompts = await self._mlflow_registry.list_prompts()
            if mlflow_prompts:
                return mlflow_prompts

        return await self._local_registry.list_prompts()

    async def get_versions(self, name: str) -> list[str]:
        """Get all versions of a prompt."""
        if self.prompt_source == PromptSource.MLFLOW and self._mlflow_registry:
            mlflow_versions = await self._mlflow_registry.get_versions(name)
            if mlflow_versions:
                return mlflow_versions

        return await self._local_registry.get_versions(name)

    async def get_lineage(self, prompt_name: str | None = None) -> list[PromptLineage]:
        """Get lineage information for prompts.

        Args:
            prompt_name: Specific prompt to get lineage for (default: all)

        Returns:
            List of PromptLineage objects
        """
        # Group usage by prompt version
        lineage_map: dict[tuple[str, str], PromptLineage] = {}

        for entry in self._usage_log:
            if prompt_name and entry["prompt_name"] != prompt_name:
                continue

            key = (entry["prompt_name"], entry["version"])
            if key not in lineage_map:
                lineage_map[key] = PromptLineage(
                    prompt_name=entry["prompt_name"],
                    prompt_version=entry["version"],
                )

        return list(lineage_map.values())

    def get_usage_stats(self) -> dict[str, Any]:
        """Get usage statistics for the registry."""
        total_uses = len(self._usage_log)
        mlflow_uses = sum(1 for u in self._usage_log if u["source"] == "mlflow")
        local_uses = sum(1 for u in self._usage_log if u["source"] == "local")
        fallback_uses = sum(1 for u in self._usage_log if u["used_fallback"])

        return {
            "total_uses": total_uses,
            "mlflow_uses": mlflow_uses,
            "local_uses": local_uses,
            "fallback_uses": fallback_uses,
            "fallback_rate": fallback_uses / total_uses if total_uses > 0 else 0.0,
        }


# ============================================================================
# Global Registry Instance
# ============================================================================


_registry: PromptRegistry | None = None


def get_prompt_registry(
    prompt_source: PromptSource = PromptSource.LOCAL,
    mlflow_tracking_uri: str | None = None,
    prompts_dir: Path | None = None,
    fallback_to_local: bool = True,
) -> PromptRegistry:
    """Get or create the global prompt registry.

    Args:
        prompt_source: Primary source for prompts
        mlflow_tracking_uri: MLflow tracking URI
        prompts_dir: Directory for local prompts
        fallback_to_local: Whether to fall back to local prompts

    Returns:
        PromptRegistry instance
    """
    global _registry
    if _registry is None:
        _registry = PromptRegistry(
            prompt_source=prompt_source,
            mlflow_tracking_uri=mlflow_tracking_uri,
            prompts_dir=prompts_dir,
            fallback_to_local=fallback_to_local,
        )
    return _registry


def reset_prompt_registry() -> None:
    """Reset the global prompt registry (for testing)."""
    global _registry
    _registry = None
