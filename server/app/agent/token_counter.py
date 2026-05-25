"""Token counting helpers aligned with Deep Agents.

This module keeps Cognition's context/debug accounting on the same approximate
counter used by Deep Agents summarization middleware.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from deepagents.middleware.summarization import count_tokens_approximately


def count_text_tokens(text: str | None) -> int:
    """Estimate token count for a single text payload using Deep Agents' counter."""
    if not text:
        return 0
    return int(count_tokens_approximately([text]))


def count_message_tokens(messages: Iterable[Any]) -> int:
    """Estimate token count for message-like objects using Deep Agents' counter."""
    return int(count_tokens_approximately(messages))
