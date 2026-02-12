"""Context window management for LLM conversations.

Handles token counting, smart message truncation,
and context budget allocation.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional, Sequence

import structlog

logger = structlog.get_logger(__name__)

# Approximate chars-per-token ratio for estimation
# GPT-family: ~4 chars/token, Claude: ~3.5 chars/token
DEFAULT_CHARS_PER_TOKEN = 4


@dataclass
class TokenEstimate:
    """Estimated token count for a piece of content."""

    text_tokens: int = 0
    total_tokens: int = 0
    method: str = "chars"  # "chars" (estimate) or "tiktoken" (exact)


@dataclass
class ContextBudget:
    """Token budget allocation for a conversation turn."""

    max_context: int  # Model's total context window
    max_output: int  # Model's max output tokens
    system_tokens: int = 0  # Tokens used by system prompt
    history_tokens: int = 0  # Tokens used by conversation history
    tool_tokens: int = 0  # Tokens reserved for tool results

    @property
    def available_input(self) -> int:
        """Tokens available for the user's next message."""
        used = self.system_tokens + self.history_tokens + self.tool_tokens
        return max(0, self.max_context - self.max_output - used)

    @property
    def total_used(self) -> int:
        """Total tokens used."""
        return self.system_tokens + self.history_tokens + self.tool_tokens

    @property
    def utilization(self) -> float:
        """Context window utilization as a fraction (0.0 - 1.0)."""
        if self.max_context == 0:
            return 0.0
        return self.total_used / self.max_context


def estimate_tokens(
    text: str,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> TokenEstimate:
    """Estimate token count from text.

    Uses character count heuristic. For exact counting,
    use a tokenizer like tiktoken.

    Args:
        text: Text to estimate tokens for.
        chars_per_token: Characters per token ratio.

    Returns:
        TokenEstimate with the count and method used.
    """
    if not text:
        return TokenEstimate(text_tokens=0, total_tokens=0, method="chars")

    char_count = len(text)
    estimated = max(1, int(char_count / chars_per_token))

    return TokenEstimate(
        text_tokens=estimated,
        total_tokens=estimated,
        method="chars",
    )


def estimate_messages_tokens(
    messages: Sequence[dict[str, Any]],
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> int:
    """Estimate total tokens for a sequence of chat messages.

    Args:
        messages: List of message dicts with 'role' and 'content'.
        chars_per_token: Characters per token ratio.

    Returns:
        Estimated total token count.
    """
    total = 0
    for msg in messages:
        content = msg.get("content", "")
        if isinstance(content, str):
            total += estimate_tokens(content, chars_per_token).total_tokens
        elif isinstance(content, list):
            # Multi-part messages (text + images)
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    total += estimate_tokens(part.get("text", ""), chars_per_token).total_tokens
        # Add overhead per message (role, formatting)
        total += 4  # ~4 tokens per message overhead
    return total


def truncate_messages(
    messages: Sequence[dict[str, Any]],
    max_tokens: int,
    strategy: str = "keep_recent",
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> list[dict[str, Any]]:
    """Truncate messages to fit within a token budget.

    Strategies:
        - 'keep_recent': Keep the most recent messages.
        - 'summarize_old': Keep recent, replace old with summary placeholder.

    Args:
        messages: Conversation messages to truncate.
        max_tokens: Maximum allowed tokens.
        strategy: Truncation strategy to use.
        chars_per_token: Characters per token ratio.

    Returns:
        Truncated list of messages.
    """
    if not messages:
        return []

    current_tokens = estimate_messages_tokens(messages, chars_per_token)

    if current_tokens <= max_tokens:
        return list(messages)

    if strategy == "keep_recent":
        return _truncate_keep_recent(messages, max_tokens, chars_per_token)
    elif strategy == "summarize_old":
        return _truncate_summarize_old(messages, max_tokens, chars_per_token)
    else:
        logger.warning(
            "Unknown truncation strategy, using keep_recent",
            strategy=strategy,
        )
        return _truncate_keep_recent(messages, max_tokens, chars_per_token)


def _truncate_keep_recent(
    messages: Sequence[dict[str, Any]],
    max_tokens: int,
    chars_per_token: float,
) -> list[dict[str, Any]]:
    """Keep the most recent messages that fit within budget.

    Always preserves the system message (first message) if present.
    """
    result: list[dict[str, Any]] = []
    token_count = 0

    # Check if first message is a system message
    system_msg = None
    remaining = list(messages)

    if remaining and remaining[0].get("role") == "system":
        system_msg = remaining[0]
        system_tokens = (
            estimate_tokens(system_msg.get("content", ""), chars_per_token).total_tokens + 4
        )
        token_count += system_tokens
        remaining = remaining[1:]

    # Add messages from most recent, working backwards
    kept: list[dict[str, Any]] = []
    for msg in reversed(remaining):
        msg_tokens = estimate_tokens(msg.get("content", ""), chars_per_token).total_tokens + 4

        if token_count + msg_tokens > max_tokens:
            break

        kept.append(msg)
        token_count += msg_tokens

    # Reassemble in chronological order
    if system_msg:
        result.append(system_msg)

    # Add a truncation notice if we dropped messages
    dropped_count = len(remaining) - len(kept)
    if dropped_count > 0:
        result.append(
            {
                "role": "system",
                "content": (
                    f"[{dropped_count} earlier messages were truncated "
                    f"to fit within the context window]"
                ),
            }
        )

    result.extend(reversed(kept))
    return result


def _truncate_summarize_old(
    messages: Sequence[dict[str, Any]],
    max_tokens: int,
    chars_per_token: float,
) -> list[dict[str, Any]]:
    """Keep recent messages, replace older ones with a summary placeholder.

    This is a simplified version that just replaces old messages
    with a note. A full implementation would use an LLM to summarize.
    """
    result: list[dict[str, Any]] = []
    token_count = 0

    # Check for system message
    system_msg = None
    remaining = list(messages)

    if remaining and remaining[0].get("role") == "system":
        system_msg = remaining[0]
        system_tokens = (
            estimate_tokens(system_msg.get("content", ""), chars_per_token).total_tokens + 4
        )
        token_count += system_tokens
        remaining = remaining[1:]

    # Reserve 20% of budget for the summary placeholder
    summary_budget = int(max_tokens * 0.1)
    message_budget = max_tokens - summary_budget - token_count

    # Keep recent messages within budget
    kept: list[dict[str, Any]] = []
    for msg in reversed(remaining):
        msg_tokens = estimate_tokens(msg.get("content", ""), chars_per_token).total_tokens + 4

        if token_count + msg_tokens > message_budget + token_count:
            break

        kept.append(msg)
        token_count += msg_tokens

    # Build result
    if system_msg:
        result.append(system_msg)

    dropped_count = len(remaining) - len(kept)
    if dropped_count > 0:
        # Collect topics from dropped messages for a rough summary
        topics: list[str] = []
        for msg in remaining[:dropped_count]:
            content = msg.get("content", "")
            if isinstance(content, str) and len(content) > 0:
                # Take first 50 chars as a topic hint
                topics.append(content[:50].strip())

        summary_parts = [
            f"[Summary of {dropped_count} earlier messages: "
            f"The conversation covered: {'; '.join(topics[:5])}"
        ]
        if len(topics) > 5:
            summary_parts.append(f"... and {len(topics) - 5} more topics")
        summary_parts.append("]")

        result.append(
            {
                "role": "system",
                "content": " ".join(summary_parts),
            }
        )

    result.extend(reversed(kept))
    return result


def compute_context_budget(
    system_prompt: str,
    messages: Sequence[dict[str, Any]],
    max_context: int,
    max_output: int,
    tool_reserve: int = 2000,
    chars_per_token: float = DEFAULT_CHARS_PER_TOKEN,
) -> ContextBudget:
    """Compute the context budget for a conversation turn.

    Args:
        system_prompt: System prompt text.
        messages: Conversation history.
        max_context: Model's total context window.
        max_output: Model's max output tokens.
        tool_reserve: Tokens to reserve for tool results.
        chars_per_token: Characters per token ratio.

    Returns:
        ContextBudget with the allocation breakdown.
    """
    system_tokens = estimate_tokens(system_prompt, chars_per_token).total_tokens
    history_tokens = estimate_messages_tokens(messages, chars_per_token)

    return ContextBudget(
        max_context=max_context,
        max_output=max_output,
        system_tokens=system_tokens,
        history_tokens=history_tokens,
        tool_tokens=tool_reserve,
    )
