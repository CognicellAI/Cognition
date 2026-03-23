from __future__ import annotations

from pydantic import BaseModel


class CodeReviewResult(BaseModel):
    issues: list[str]


class SessionResult(BaseModel):
    status: str


class AgentResult(BaseModel):
    summary: str
