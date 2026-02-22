"""Circuit breaker pattern for LLM provider resilience.

Provides fault tolerance for LLM provider calls through:
- State machine (closed, open, half-open)
- Exponential backoff for retries
- Automatic recovery detection
- Metrics and health reporting

The circuit breaker prevents cascading failures when a provider is
degraded or unavailable by temporarily blocking requests.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Callable, Coroutine, Optional, TypeVar

import structlog

logger = structlog.get_logger(__name__)

T = TypeVar("T")


class CircuitState(Enum):
    """Circuit breaker states."""

    CLOSED = auto()  # Normal operation, requests allowed
    OPEN = auto()  # Failure threshold reached, requests blocked
    HALF_OPEN = auto()  # Testing if service has recovered


@dataclass
class CircuitBreakerConfig:
    """Configuration for circuit breaker."""

    name: str
    failure_threshold: int = 5  # Failures before opening
    success_threshold: int = 3  # Successes in half-open before closing
    timeout_seconds: float = 60.0  # Time to wait before half-open
    half_open_max_calls: int = 3  # Max calls in half-open state


@dataclass
class CircuitBreakerMetrics:
    """Metrics for circuit breaker state."""

    state: str
    total_calls: int = 0
    successful_calls: int = 0
    failed_calls: int = 0
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_failure_time: Optional[float] = None
    last_state_change: Optional[float] = None
    rejection_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "state": self.state,
            "total_calls": self.total_calls,
            "successful_calls": self.successful_calls,
            "failed_calls": self.failed_calls,
            "consecutive_failures": self.consecutive_failures,
            "consecutive_successes": self.consecutive_successes,
            "last_failure_time": self.last_failure_time,
            "last_state_change": self.last_state_change,
            "rejection_count": self.rejection_count,
        }


class CircuitBreakerOpenError(Exception):
    """Exception raised when circuit breaker is open."""

    def __init__(self, breaker_name: str, last_failure: Optional[str] = None):
        self.breaker_name = breaker_name
        self.last_failure = last_failure
        super().__init__(
            f"Circuit breaker '{breaker_name}' is OPEN. Last failure: {last_failure or 'unknown'}"
        )


class CircuitBreaker:
    """Circuit breaker for resilient LLM provider calls.

    The circuit breaker prevents cascading failures by monitoring
    provider health and blocking requests when failure rates are high.

    States:
    - CLOSED: Normal operation, all requests pass
    - OPEN: Failure threshold reached, requests blocked
    - HALF-OPEN: Testing recovery with limited traffic

    Example:
        breaker = CircuitBreaker(
            config=CircuitBreakerConfig(name="openai", failure_threshold=5)
        )

        try:
            result = await breaker.call(llm_client.chat, messages)
        except CircuitBreakerOpenError:
            # Circuit is open, provider unavailable
            result = await fallback_provider.chat(messages)
    """

    def __init__(self, config: CircuitBreakerConfig):
        """Initialize circuit breaker.

        Args:
            config: Circuit breaker configuration
        """
        self.config = config
        self._state = CircuitState.CLOSED
        self._lock = asyncio.Lock()
        self._metrics = CircuitBreakerMetrics(state=CircuitState.CLOSED.name)
        self._half_open_calls = 0

    @property
    def state(self) -> CircuitState:
        """Current circuit state."""
        return self._state

    @property
    def metrics(self) -> CircuitBreakerMetrics:
        """Current circuit metrics (copy)."""
        return CircuitBreakerMetrics(
            state=self._state.name,
            total_calls=self._metrics.total_calls,
            successful_calls=self._metrics.successful_calls,
            failed_calls=self._metrics.failed_calls,
            consecutive_failures=self._metrics.consecutive_failures,
            consecutive_successes=self._metrics.consecutive_successes,
            last_failure_time=self._metrics.last_failure_time,
            last_state_change=self._metrics.last_state_change,
            rejection_count=self._metrics.rejection_count,
        )

    def is_open(self) -> bool:
        """Check if circuit breaker is open.

        Returns:
            True if circuit is open, False otherwise
        """
        return self._state == CircuitState.OPEN

    async def call(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute function with circuit breaker protection.

        Args:
            func: Async function to call
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Result from function call

        Raises:
            CircuitBreakerOpenError: If circuit is open and timeout not elapsed
            Exception: Any exception from the wrapped function
        """
        async with self._lock:
            await self._check_state_transition()

            if self._state == CircuitState.OPEN:
                self._metrics.rejection_count += 1
                raise CircuitBreakerOpenError(
                    self.config.name,
                    f"Failure threshold ({self.config.failure_threshold}) exceeded",
                )

            if self._state == CircuitState.HALF_OPEN:
                if self._half_open_calls >= self.config.half_open_max_calls:
                    self._metrics.rejection_count += 1
                    raise CircuitBreakerOpenError(
                        self.config.name,
                        "Half-open call limit reached",
                    )
                self._half_open_calls += 1

            self._metrics.total_calls += 1

        # Execute outside lock to allow concurrent calls
        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as e:
            await self._on_failure(str(e))
            raise

    async def _check_state_transition(self) -> None:
        """Check and perform state transitions based on time."""
        if self._state == CircuitState.OPEN:
            if self._metrics.last_failure_time is None:
                return

            elapsed = time.time() - self._metrics.last_failure_time
            if elapsed >= self.config.timeout_seconds:
                await self._transition_to(CircuitState.HALF_OPEN)

    async def _on_success(self) -> None:
        """Handle successful call."""
        async with self._lock:
            self._metrics.successful_calls += 1
            self._metrics.consecutive_successes += 1
            self._metrics.consecutive_failures = 0

            if self._state == CircuitState.HALF_OPEN:
                if self._metrics.consecutive_successes >= self.config.success_threshold:
                    await self._transition_to(CircuitState.CLOSED)

    async def _on_failure(self, error: str) -> None:
        """Handle failed call."""
        async with self._lock:
            self._metrics.failed_calls += 1
            self._metrics.consecutive_failures += 1
            self._metrics.consecutive_successes = 0
            self._metrics.last_failure_time = time.time()

            if self._state == CircuitState.CLOSED:
                if self._metrics.consecutive_failures >= self.config.failure_threshold:
                    await self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.HALF_OPEN:
                await self._transition_to(CircuitState.OPEN)

    async def _transition_to(self, new_state: CircuitState) -> None:
        """Transition to new state."""
        old_state = self._state
        self._state = new_state
        self._metrics.state = new_state.name
        self._metrics.last_state_change = time.time()
        self._metrics.consecutive_failures = 0
        self._metrics.consecutive_successes = 0
        self._half_open_calls = 0

        logger.info(
            "Circuit breaker state changed",
            name=self.config.name,
            old_state=old_state.name,
            new_state=new_state.name,
        )

    def reset(self) -> None:
        """Manually reset circuit to CLOSED state."""
        self._state = CircuitState.CLOSED
        self._metrics = CircuitBreakerMetrics(state=CircuitState.CLOSED.name)
        self._half_open_calls = 0
        logger.info("Circuit breaker manually reset", name=self.config.name)

    async def record_failure(self, error: str) -> None:
        """Manually record a failure.

        This is used by the fallback chain to record failures
        that occur outside of the circuit breaker call flow.

        Args:
            error: Description of the error
        """
        async with self._lock:
            self._metrics.failed_calls += 1
            self._metrics.consecutive_failures += 1
            self._metrics.consecutive_successes = 0
            self._metrics.last_failure_time = time.time()

            if self._state == CircuitState.CLOSED:
                if self._metrics.consecutive_failures >= self.config.failure_threshold:
                    await self._transition_to(CircuitState.OPEN)
            elif self._state == CircuitState.HALF_OPEN:
                await self._transition_to(CircuitState.OPEN)

            logger.warning(
                "Failure recorded on circuit breaker",
                name=self.config.name,
                error=error,
                consecutive_failures=self._metrics.consecutive_failures,
                state=self._state.name,
            )


class RetryWithBackoff:
    """Retry logic with exponential backoff.

    Retries failed operations with increasing delays between attempts.
    Respects circuit breaker state to avoid hammering degraded services.

    Example:
        retry = RetryWithBackoff(max_retries=3, base_delay=1.0)

        async def call_llm():
            return await llm.chat(messages)

        result = await retry.execute(call_llm)
    """

    def __init__(
        self,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        exponential_base: float = 2.0,
        jitter: bool = True,
    ):
        """Initialize retry configuration.

        Args:
            max_retries: Maximum number of retry attempts
            base_delay: Initial delay in seconds
            max_delay: Maximum delay cap in seconds
            exponential_base: Exponential growth factor
            jitter: Add random jitter to prevent thundering herd
        """
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.exponential_base = exponential_base
        self.jitter = jitter

    def _calculate_delay(self, attempt: int) -> float:
        """Calculate delay for given attempt with optional jitter."""
        delay = self.base_delay * (self.exponential_base**attempt)
        delay = min(delay, self.max_delay)

        if self.jitter:
            import random

            delay = delay * (0.5 + random.random())

        return delay

    async def execute(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute with retries and exponential backoff.

        Args:
            func: Async function to execute
            *args: Positional arguments
            **kwargs: Keyword arguments

        Returns:
            Result from successful execution

        Raises:
            Exception: The last exception from all retry attempts
        """
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except CircuitBreakerOpenError:
                # Don't retry if circuit breaker is open
                raise
            except Exception as e:
                last_exception = e

                if attempt < self.max_retries:
                    delay = self._calculate_delay(attempt)
                    logger.warning(
                        "Retry attempt failed, backing off",
                        attempt=attempt + 1,
                        max_retries=self.max_retries,
                        delay=delay,
                        error=str(e)[:100],
                    )
                    await asyncio.sleep(delay)

        raise last_exception or Exception("All retry attempts failed")


class ResilientProviderClient:
    """LLM provider client with circuit breaker and retry integration.

    Combines circuit breaker pattern with exponential backoff retries
    for resilient LLM provider calls.

    Example:
        client = ResilientProviderClient(
            provider_name="openai",
            create_model_func=create_openai_model,
            breaker_config=CircuitBreakerConfig(
                name="openai",
                failure_threshold=5,
            ),
            retry_config=RetryWithBackoff(max_retries=3),
        )

        model = await client.get_model()
    """

    def __init__(
        self,
        provider_name: str,
        create_model_func: Callable[..., Coroutine[Any, Any, T]],
        breaker_config: Optional[CircuitBreakerConfig] = None,
        retry_config: Optional[RetryWithBackoff] = None,
    ):
        """Initialize resilient client.

        Args:
            provider_name: Name of the LLM provider
            create_model_func: Factory function to create model
            breaker_config: Circuit breaker configuration
            retry_config: Retry/backoff configuration
        """
        self.provider_name = provider_name
        self.create_model_func = create_model_func
        self.breaker = CircuitBreaker(breaker_config or CircuitBreakerConfig(name=provider_name))
        self.retry = retry_config or RetryWithBackoff()

    async def call(self, *args: Any, **kwargs: Any) -> Any:
        """Execute call with circuit breaker and retry.

        Args:
            *args: Arguments for create_model_func
            **kwargs: Keyword arguments for create_model_func

        Returns:
            Result from the model factory function
        """
        return await self.breaker.call(
            self.retry.execute,
            self.create_model_func,
            *args,
            **kwargs,
        )

    @property
    def circuit_state(self) -> CircuitState:
        """Current circuit breaker state."""
        return self.breaker.state

    @property
    def circuit_metrics(self) -> CircuitBreakerMetrics:
        """Current circuit breaker metrics."""
        return self.breaker.metrics


# Global circuit breaker registry
_breakers: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(name: str) -> Optional[CircuitBreaker]:
    """Get circuit breaker by name from registry.

    Args:
        name: Circuit breaker name

    Returns:
        Circuit breaker if found, None otherwise
    """
    return _breakers.get(name)


def register_circuit_breaker(breaker: CircuitBreaker) -> None:
    """Register circuit breaker in global registry.

    Args:
        breaker: Circuit breaker to register
    """
    _breakers[breaker.config.name] = breaker
    logger.info("Circuit breaker registered", name=breaker.config.name)


def get_all_circuit_breaker_metrics() -> dict[str, CircuitBreakerMetrics]:
    """Get metrics for all registered circuit breakers.

    Returns:
        Dictionary mapping breaker names to metrics
    """
    return {name: breaker.metrics for name, breaker in _breakers.items()}


def get_circuit_breaker_registry() -> dict[str, CircuitBreaker]:
    """Get the global circuit breaker registry.

    Returns:
        Dictionary mapping breaker names to CircuitBreaker instances.
    """
    return _breakers
