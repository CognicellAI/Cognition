"""Business Scenario: Fast Agent Response Performance.

As a user, I want the AI agent to respond quickly to my messages,
without recompiling the agent for every interaction.

Business Value:
- Improved user experience with faster response times
- Reduced server load through intelligent caching
- Consistent performance across sessions
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
class TestFastAgentPerformance:
    """Test quick response times through agent caching."""

    async def test_multiple_session_creation(self, api_client) -> None:
        """Create multiple test sessions."""
        sessions = []
        for i in range(3):
            sid = await api_client.create_session(f"Performance Test Session {i}")
            sessions.append(sid)

        assert len(sessions) == 3, "Failed to create all sessions"

    async def test_cold_start_response_time(self, api_client, session, timer) -> None:
        """Measure initial response time (cold start)."""
        import time

        start = time.time()
        response = await api_client.send_message(session, "First message - cold start")
        duration_ms = (time.time() - start) * 1000

        assert response.status_code == 200, "First message failed"
        assert duration_ms > 0, "Invalid duration"

        # Log performance (not asserting on specific times)
        print(f"\nCold start: {duration_ms:.0f}ms")

    async def test_cached_response_times(self, api_client, session) -> None:
        """Measure cached response times."""
        import time

        durations = []
        for i in range(5):
            start = time.time()
            response = await api_client.send_message(session, f"Cached message {i}")
            duration_ms = (time.time() - start) * 1000

            assert response.status_code == 200
            durations.append(duration_ms)

        avg_duration = sum(durations) / len(durations)
        print(f"\nCached avg: {avg_duration:.0f}ms")

        # All should complete successfully
        assert len(durations) == 5

    async def test_cross_session_performance(self, api_client) -> None:
        """Test performance across sessions."""
        import time

        sessions = []
        for i in range(3):
            sid = await api_client.create_session(f"Cross-Session Test {i}")
            sessions.append(sid)

        durations = []
        for sid in sessions:
            start = time.time()
            response = await api_client.send_message(sid, "Cross-session message")
            duration_ms = (time.time() - start) * 1000

            assert response.status_code == 200
            durations.append(duration_ms)

        # All sessions should respond
        assert len(durations) == 3

    async def test_performance_consistency(self, api_client, session) -> None:
        """Test that performance is consistent (not degraded)."""
        import time

        # Warm up
        await api_client.send_message(session, "Warmup")

        # Measure several requests
        durations = []
        for i in range(10):
            start = time.time()
            await api_client.send_message(session, f"Consistency test {i}")
            duration_ms = (time.time() - start) * 1000
            durations.append(duration_ms)

        # Check consistency (no extreme outliers)
        avg = sum(durations) / len(durations)
        max_duration = max(durations)

        # Max shouldn't be more than 10x average
        assert max_duration < avg * 10, (
            f"Performance inconsistent: max={max_duration:.0f}ms, avg={avg:.0f}ms"
        )

    async def test_concurrent_session_performance(self, api_client) -> None:
        """Test performance with concurrent sessions."""
        import asyncio

        async def send_to_session(session_id: str, msg: str):
            return await api_client.send_message(session_id, msg)

        # Create multiple sessions
        sessions = []
        for i in range(3):
            sid = await api_client.create_session(f"Concurrent Session {i}")
            sessions.append(sid)

        # Send messages concurrently
        tasks = [send_to_session(sid, f"Concurrent message {i}") for i, sid in enumerate(sessions)]

        responses = await asyncio.gather(*tasks)

        # All should succeed
        for response in responses:
            assert response.status_code == 200
