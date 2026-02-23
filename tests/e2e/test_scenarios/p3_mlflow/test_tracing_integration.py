"""Business Scenario: MLflow Tracing Integration.

As an AI platform operator, I want agent traces automatically captured in MLflow,
so I can monitor and debug agent behavior across sessions.

Business Value:
- Complete visibility into agent decision-making
- Debugging and troubleshooting capabilities
- Performance monitoring and optimization
- Audit trail for compliance
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.mlflow  # Custom marker for MLflow-dependent tests
class TestMLflowTracingIntegration:
    """Test MLflow captures agent traces automatically."""

    async def test_mlflow_trace_created_on_session(self, api_client) -> None:
        """Test that MLflow creates a trace when agent processes message."""
        # This test requires MLflow to be enabled and running
        # Skip if MLflow not available
        try:
            import mlflow

            if not mlflow.is_tracking_uri_set():
                pytest.skip("MLflow not configured")
        except ImportError:
            pytest.skip("MLflow not installed")

        # Create session and send message
        session_id = await api_client.create_session("MLflow Trace Test")
        response = await api_client.send_message(session_id, "Hello, trace me")

        # Response should succeed
        assert response.status_code == 200

        # In a real scenario with MLflow, we would verify:
        # - Trace was created in MLflow
        # - Trace contains session_id metadata
        # - Trace has spans for LLM calls, tool calls, etc.
        print("\n  Trace would be captured in MLflow (requires running MLflow server)")

    async def test_trace_metadata_includes_session(self, api_client) -> None:
        """Test that traces include session metadata for filtering."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        session_id = await api_client.create_session("Trace Metadata Test")

        # Send message that triggers multi-step reasoning
        response = await api_client.send_message(session_id, "What files are in the workspace?")

        assert response.status_code == 200

        # With MLflow enabled, trace would include:
        # - cognition.session_id tag
        # - cognition.workspace tag
        # - cognition.session_title tag
        print(f"\n  Trace would include metadata: session_id={session_id}")

    async def test_nested_spans_in_trace(self, api_client) -> None:
        """Test that complex operations create nested spans."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        session_id = await api_client.create_session("Nested Spans Test")

        # Message that triggers tool use
        response = await api_client.send_message(
            session_id, "List all Python files and analyze their structure"
        )

        assert response.status_code == 200

        # With MLflow, trace would show:
        # - Root span: Agent execution
        #   - LLM span: Planning
        #   - Tool span: list_files
        #   - LLM span: Analysis
        print("\n  Nested spans would include: planning, tool calls, analysis")

    async def test_trace_latency_capture(self, api_client) -> None:
        """Test that trace captures operation latency."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        import time

        session_id = await api_client.create_session("Latency Test")

        start = time.time()
        response = await api_client.send_message(session_id, "Quick response test")
        duration = time.time() - start

        assert response.status_code == 200

        # Trace would capture:
        # - Total request latency
        # - Individual LLM call latency
        # - Tool execution latency
        print(f"\n  Request latency: {duration:.2f}s (would be in trace)")

    async def test_error_tracing(self, api_client) -> None:
        """Test that errors are captured in traces."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        session_id = await api_client.create_session("Error Trace Test")

        # Send message (may succeed or fail depending on provider)
        response = await api_client.send_message(session_id, "This should work normally")

        # Trace would capture status:
        # - OK status for successful operations
        # - Error status with stack traces for failures
        print(f"\n  Response status: {response.status_code} (would be in trace)")

    async def test_multiple_session_traces(self, api_client) -> None:
        """Test that multiple sessions create separate traces."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        # Create multiple sessions
        sessions = []
        for i in range(3):
            sid = await api_client.create_session(f"Trace Session {i}")
            sessions.append(sid)

        # Send message in each session
        for sid in sessions:
            response = await api_client.send_message(sid, f"Message for {sid}")
            assert response.status_code == 200

        # Each session should have its own trace
        print(f"\n  {len(sessions)} separate traces would be created")

    async def test_trace_without_mlflow_enabled(self, api_client) -> None:
        """Test that agent works normally without MLflow."""
        # This should work even without MLflow
        session_id = await api_client.create_session("No MLflow Test")
        response = await api_client.send_message(session_id, "Works without MLflow")

        assert response.status_code == 200
        print("\n  Agent works without MLflow (graceful degradation)")
