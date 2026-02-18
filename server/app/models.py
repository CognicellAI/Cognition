"""Shared models for Cognition.

Core domain models used across the application.
These are separate from API models to avoid circular imports.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal, Optional
from enum import Enum


from pydantic import BaseModel


class SessionStatus(str, Enum):
    """Session status enumeration."""

    ACTIVE = "active"
    INACTIVE = "inactive"
    ERROR = "error"


class SessionConfig(BaseModel):
    """Session configuration options."""

    provider: Optional[Literal["openai", "bedrock", "mock", "openai_compatible"]] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None
    system_prompt: Optional[str] = None


@dataclass
class Session:
    """Core session domain model."""

    id: str
    workspace_path: str
    title: Optional[str]
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
    def from_dict(cls, data: dict) -> "Session":
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
class Message:
    """Core message domain model."""

    id: str
    session_id: str
    role: Literal["user", "assistant", "system"]
    content: Optional[str]
    parent_id: Optional[str]
    created_at: datetime


@dataclass
class ExecutionResult:
    """Result of sandbox command execution."""

    output: str
    exit_code: int
    duration_ms: Optional[int] = None
