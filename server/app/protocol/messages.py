"""Client to server message models."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class CreateSessionRequest(BaseModel):
    """Request to create a new session or resume existing project."""

    type: Literal["create_session"] = "create_session"
    # Project support
    project_id: str | None = Field(default=None, description="Resume existing project by ID")
    user_prefix: str | None = Field(default=None, description="Create new project with this prefix")
    # Session configuration
    network_mode: Literal["OFF", "ON"] = Field(default="OFF")
    repo_url: str | None = Field(default=None, description="Optional git repo URL to clone")


class UserMessage(BaseModel):
    """User message sent to the server."""

    type: Literal["user_msg"] = "user_msg"
    session_id: str
    content: str


ClientMessage = CreateSessionRequest | UserMessage
