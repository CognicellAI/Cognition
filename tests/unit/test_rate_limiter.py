"""Unit tests for rate_limiter module."""

from __future__ import annotations

import asyncio

import pytest

from server.app.exceptions import RateLimitError
from server.app.rate_limiter import RateLimitConfig, RateLimiter, TokenBucket


class TestTokenBucket:
    """Test TokenBucket class."""

    @pytest.fixture
    def bucket(self):
        """Create a token bucket for testing."""
        return TokenBucket(rate=10, capacity=5)

    async def test_acquire_with_available_tokens(self, bucket):
        """Test acquiring token when tokens are available."""
        result = await bucket.acquire()
        assert result is True
        assert bucket.tokens == 4

    async def test_acquire_multiple_tokens(self, bucket):
        """Test acquiring multiple tokens."""
        for _ in range(5):
            result = await bucket.acquire()
            assert result is True
        # Bucket should be empty or near-empty (floating point precision)
        assert bucket.tokens < 0.001

    async def test_acquire_when_empty(self, bucket):
        """Test acquiring token when bucket is empty."""
        # Empty the bucket
        for _ in range(5):
            await bucket.acquire()

        result = await bucket.acquire()
        assert result is False

    async def test_tokens_refill_over_time(self, bucket):
        """Test that tokens refill over time."""
        # Empty the bucket
        for _ in range(5):
            await bucket.acquire()

        # Bucket should be near empty
        assert bucket.tokens < 1

        # Wait for refill
        await asyncio.sleep(0.2)  # Should add about 2 tokens at rate 10/s

        result = await bucket.acquire()
        assert result is True

    async def test_wait_time_when_tokens_available(self, bucket):
        """Test wait time when tokens are available."""
        wait_time = await bucket.wait_time()
        assert wait_time == 0.0

    async def test_wait_time_when_empty(self, bucket):
        """Test wait time when bucket is empty."""
        # Empty the bucket
        for _ in range(5):
            await bucket.acquire()

        wait_time = await bucket.wait_time()
        assert wait_time > 0.0

    async def test_capacity_not_exceeded(self, bucket):
        """Test that capacity is not exceeded."""
        # Wait a long time
        await asyncio.sleep(1.0)

        # Should still be at capacity
        assert bucket.tokens == 5


class TestRateLimiter:
    """Test RateLimiter class."""

    @pytest.fixture
    def limiter(self):
        """Create a rate limiter for testing."""
        config = RateLimitConfig(
            requests_per_minute=60,  # 1 per second
            burst_size=5,
            window_seconds=60,
        )
        return RateLimiter(config)

    @pytest.fixture
    async def started_limiter(self, limiter):
        """Create and start a rate limiter."""
        await limiter.start()
        yield limiter
        await limiter.stop()

    async def test_check_rate_limit_within_limit(self, started_limiter):
        """Test that requests within limit succeed."""
        # Should be able to make burst_size requests immediately
        for _ in range(5):
            await started_limiter.check_rate_limit("client1")

    async def test_check_rate_limit_exceeds_burst(self, started_limiter):
        """Test that exceeding burst raises RateLimitError."""
        # Exhaust the burst
        for _ in range(5):
            await started_limiter.check_rate_limit("client1")

        # Next request should be rate limited
        with pytest.raises(RateLimitError) as exc_info:
            await started_limiter.check_rate_limit("client1")

        assert exc_info.value.code.value == "rate_limited"
        assert exc_info.value.details["resource"] == "client1"

    async def test_different_keys_independent(self, started_limiter):
        """Test that different rate limit keys are independent."""
        # Exhaust burst for client1
        for _ in range(5):
            await started_limiter.check_rate_limit("client1")

        # client2 should still have their own burst
        for _ in range(5):
            await started_limiter.check_rate_limit("client2")

    async def test_rate_limit_error_details(self, started_limiter):
        """Test that RateLimitError has correct details."""
        # Exhaust the burst
        for _ in range(5):
            await started_limiter.check_rate_limit("client1")

        with pytest.raises(RateLimitError) as exc_info:
            await started_limiter.check_rate_limit("client1")

        error = exc_info.value
        assert error.details["resource"] == "client1"
        assert error.details["limit"] == 60
        assert error.details["window_seconds"] == 60


class TestRateLimitConfig:
    """Test RateLimitConfig dataclass."""

    def test_default_config(self):
        """Test default configuration values."""
        config = RateLimitConfig()
        assert config.requests_per_minute == 60
        assert config.burst_size == 10
        assert config.window_seconds == 60

    def test_custom_config(self):
        """Test custom configuration values."""
        config = RateLimitConfig(
            requests_per_minute=120,
            burst_size=20,
            window_seconds=30,
        )
        assert config.requests_per_minute == 120
        assert config.burst_size == 20
        assert config.window_seconds == 30


class TestRateLimiterLifecycle:
    """Test RateLimiter lifecycle management."""

    async def test_start_stop(self):
        """Test that start and stop work correctly."""
        limiter = RateLimiter()
        await limiter.start()
        assert limiter._cleanup_task is not None
        await limiter.stop()
        # After stop, task might be cancelled but not yet None
        assert limiter._cleanup_task is None or limiter._cleanup_task.done()

    async def test_multiple_stops(self):
        """Test that multiple stops are safe."""
        limiter = RateLimiter()
        await limiter.start()
        await limiter.stop()
        await limiter.stop()  # Should not raise

    async def test_operations_without_start(self):
        """Test that operations work without explicit start."""
        limiter = RateLimiter(RateLimitConfig(requests_per_minute=60, burst_size=5))

        # Should work without start()
        for _ in range(5):
            await limiter.check_rate_limit("client1")


class TestRateLimiterEdgeCases:
    """Test edge cases for RateLimiter."""

    @pytest.fixture
    def limiter(self):
        """Create a rate limiter for testing."""
        config = RateLimitConfig(
            requests_per_minute=60,
            burst_size=1,  # Minimal burst
            window_seconds=60,
        )
        return RateLimiter(config)

    @pytest.fixture
    async def started_limiter(self, limiter):
        """Create and start a rate limiter."""
        await limiter.start()
        yield limiter
        await limiter.stop()

    async def test_single_burst_capacity(self, started_limiter):
        """Test rate limiter with minimal burst capacity."""
        # Should be able to make exactly 1 request
        await started_limiter.check_rate_limit("client1")

        # Second request should be rate limited
        with pytest.raises(RateLimitError):
            await started_limiter.check_rate_limit("client1")

    async def test_empty_key(self, started_limiter):
        """Test rate limiting with empty key."""
        await started_limiter.check_rate_limit("")

    async def test_long_key(self, started_limiter):
        """Test rate limiting with very long key."""
        long_key = "x" * 1000
        await started_limiter.check_rate_limit(long_key)
