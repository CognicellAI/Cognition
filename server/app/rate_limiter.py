"""Rate limiting implementation for Cognition.

Supports token bucket and sliding window algorithms.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Callable

from server.app.exceptions import RateLimitError


@dataclass
class RateLimitConfig:
    """Configuration for rate limiting."""

    requests_per_minute: int = 60
    burst_size: int = 10  # Allow short bursts
    window_seconds: int = 60


class TokenBucket:
    """Token bucket rate limiter.

    Allows bursts up to burst_size, then enforces steady rate.
    """

    def __init__(self, rate: float, capacity: int):
        """Initialize token bucket.

        Args:
            rate: Tokens added per second
            capacity: Maximum tokens (burst size)
        """
        self.rate = rate
        self.capacity = capacity
        self.tokens = capacity
        self.last_update = time.time()
        self._lock = asyncio.Lock()

    async def acquire(self) -> bool:
        """Try to acquire a token.

        Returns:
            True if token acquired, False otherwise
        """
        async with self._lock:
            now = time.time()
            elapsed = now - self.last_update

            # Add tokens based on elapsed time
            self.tokens = min(self.capacity, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens >= 1:
                self.tokens -= 1
                return True
            return False

    async def wait_time(self) -> float:
        """Calculate time until next token available."""
        async with self._lock:
            if self.tokens >= 1:
                return 0.0
            return (1 - self.tokens) / self.rate


class RateLimiter:
    """Rate limiter for WebSocket connections and API endpoints."""

    def __init__(self, config: RateLimitConfig | None = None):
        """Initialize rate limiter.

        Args:
            config: Rate limiting configuration
        """
        self.config = config or RateLimitConfig()
        self.buckets: dict[str, TokenBucket] = defaultdict(
            lambda: TokenBucket(
                rate=self.config.requests_per_minute / 60,
                capacity=self.config.burst_size,
            )
        )
        self._cleanup_task: asyncio.Task | None = None

    async def start(self) -> None:
        """Start background cleanup task."""
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop(self) -> None:
        """Stop background cleanup task."""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

    async def check_rate_limit(self, key: str) -> None:
        """Check if request is within rate limit.

        Args:
            key: Rate limit key (e.g., client_id, session_id, IP)

        Raises:
            RateLimitError: If rate limit exceeded
        """
        bucket = self.buckets[key]

        if not await bucket.acquire():
            wait_time = await bucket.wait_time()
            raise RateLimitError(
                resource=key,
                limit=self.config.requests_per_minute,
                window=self.config.window_seconds,
            )

    async def _cleanup_loop(self) -> None:
        """Periodically clean up unused buckets."""
        while True:
            try:
                await asyncio.sleep(300)  # Clean up every 5 minutes

                # Remove buckets that haven't been used in 10 minutes
                cutoff = time.time() - 600
                keys_to_remove = [
                    key for key, bucket in self.buckets.items() if bucket.last_update < cutoff
                ]
                for key in keys_to_remove:
                    del self.buckets[key]

            except asyncio.CancelledError:
                break
            except Exception:
                # Log but don't crash
                pass


# Global rate limiter instance
_rate_limiter: RateLimiter | None = None


def get_rate_limiter(config: RateLimitConfig | None = None) -> RateLimiter:
    """Get or create global rate limiter instance."""
    global _rate_limiter
    if _rate_limiter is None:
        _rate_limiter = RateLimiter(config)
    return _rate_limiter
