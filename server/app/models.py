"""Shared models for Cognition.

Core domain models used across the application.
These are separate from API models to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel


class SessionStatus(str, Enum):
    """Session status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class PromptConfig(BaseModel):
    """Prompt configuration with explicit type and value.

    Supports three prompt sources:
    - file: Load from .cognition/prompts/{value}.md
    - inline: Use value directly as prompt text
    - mlflow: Load from MLflow registry (requires mlflow_enabled)

    Examples:
        file: "security-expert" -> loads .cognition/prompts/security-expert.md
        inline: "You are a helpful assistant" -> uses text directly
        mlflow: "my-prompt:v1" -> loads from MLflow registry
    """

    type: Literal["file", "inline", "mlflow"] = "file"
    value: str = "system"

    def get_prompt_text(self, prompts_dir: str = ".cognition/prompts") -> str:
        """Resolve prompt configuration to actual text.

        Args:
            prompts_dir: Directory for file-type prompts

        Returns:
            Resolved prompt text

        Raises:
            FileNotFoundError: If file-type prompt not found
            RuntimeError: If mlflow-type but MLflow not available
        """
        from pathlib import Path

        if self.type == "inline":
            return self.value

        elif self.type == "file":
            path = Path(prompts_dir) / f"{self.value}.md"
            if not path.exists():
                # Try without .md extension
                path = Path(prompts_dir) / self.value
            if not path.exists():
                raise FileNotFoundError(f"Prompt file not found: {path}")
            return path.read_text()

        elif self.type == "mlflow":
            # Lazy import to avoid dependency when MLflow not used
            try:
                import mlflow
                from mlflow.genai import load_prompt

                return load_prompt(self.value)
            except ImportError:
                raise RuntimeError("MLflow not installed. Install with: pip install mlflow[genai]")
            except Exception as e:
                raise RuntimeError(f"Failed to load MLflow prompt '{self.value}': {e}")

        else:
            raise ValueError(f"Unknown prompt type: {self.type}")


class SessionConfig(BaseModel):
    """Session configuration options."""

    provider: Literal["openai", "bedrock", "mock", "openai_compatible"] | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    system_prompt: str | None = None  # Kept for backward compatibility


@dataclass
class Session:
    """Core session domain model."""

    id: str
    workspace_path: str
    title: str | None
    thread_id: str
    status: SessionStatus
    config: SessionConfig
    created_at: str
    updated_at: str
    message_count: int = 0
    scopes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Convert to dictionary for storage."""
        return {
            "id": self.id,
            "workspace_path": self.workspace_path,
            "title": self.title,
            "thread_id": self.thread_id,
            "status": self.status.value,
            "config": {
                "provider": self.config.provider,
                "model": self.config.model,
                "temperature": self.config.temperature,
                "max_tokens": self.config.max_tokens,
                "system_prompt": self.config.system_prompt,
            },
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "message_count": self.message_count,
            "scopes": self.scopes,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Session:
        """Create from dictionary."""
        config_data = data.get("config", {})
        return cls(
            id=data["id"],
            workspace_path=data["workspace_path"],
            title=data.get("title"),
            thread_id=data["thread_id"],
            status=SessionStatus(data.get("status", "active")),
            config=SessionConfig(
                provider=config_data.get("provider"),
                model=config_data.get("model"),
                temperature=config_data.get("temperature"),
                max_tokens=config_data.get("max_tokens"),
                system_prompt=config_data.get("system_prompt"),
            ),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            message_count=data.get("message_count", 0),
            scopes=data.get("scopes", {}),
        )


@dataclass
class ToolCall:
    """Tool call invocation details."""

    name: str
    args: dict[str, Any]
    id: str


@dataclass
class Message:
    """Core message domain model."""

    id: str
    session_id: str
    role: Literal["user", "assistant", "system", "tool"]
    content: str | None
    parent_id: str | None
    created_at: datetime
    tool_calls: list[ToolCall] | None = None
    tool_call_id: str | None = None
    token_count: int | None = None
    model_used: str | None = None
    metadata: dict[str, Any] | None = None


@dataclass
class ExecutionResult:
    """Result of sandbox command execution."""

    output: str
    exit_code: int
    duration_ms: int | None = None
