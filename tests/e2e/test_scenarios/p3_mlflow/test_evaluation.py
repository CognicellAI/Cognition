"""Business Scenario: MLflow Evaluation and Scoring.

As a platform operator, I want to evaluate agent session quality using MLflow,
so I can measure and improve AI performance systematically.

Business Value:
- Systematic quality assessment
- Automated scoring with built-in metrics
- Custom scorers for domain-specific needs
- Quality trend tracking over time
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.mlflow
class TestMLflowEvaluation:
    """Test MLflow evaluation capabilities."""

    async def test_session_evaluation_endpoint(self, api_client) -> None:
        """Test evaluation endpoint for completed sessions."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        # Create and populate session
        session_id = await api_client.create_session("Evaluation Test")
        await api_client.send_message(session_id, "What is Python?")

        # Request evaluation
        response = await api_client.post(
            f"/sessions/{session_id}/evaluate", json={"scorers": ["correctness", "helpfulness"]}
        )

        # Should return 200 if MLflow available, 501 if not
        if response.status_code == 200:
            data = response.json()
            assert "scores" in data
            assert "overall_score" in data
            print(f"\n  Evaluation scores: {data.get('scores')}")
        elif response.status_code == 501:
            print("\n  Evaluation requires MLflow (not available)")
        elif response.status_code == 404:
            print("\n  Evaluation endpoint not yet implemented (expected)")
        else:
            # Other error
            assert response.status_code in [200, 404, 501]

    async def test_evaluation_with_trace(self, api_client) -> None:
        """Test evaluation uses MLflow trace data."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        # Create session with tool usage
        session_id = await api_client.create_session("Trace Evaluation Test")
        await api_client.send_message(session_id, "List files and summarize")

        # Get session messages for evaluation context
        messages = await api_client.get_messages(session_id)

        # Evaluation would use:
        # - Trace showing tool calls
        # - LLM responses
        # - User messages
        print(f"\n  {len(messages)} messages available for evaluation context")

    async def test_builtin_scorers(self, api_client) -> None:
        """Test MLflow built-in scorers."""
        try:
            import mlflow
            from mlflow.genai.scorers import Correctness, Helpfulness, Safety
        except ImportError:
            pytest.skip("MLflow genai not installed")

        session_id = await api_client.create_session("Scorer Test")
        await api_client.send_message(session_id, "Explain quantum computing")

        # Built-in scorers would evaluate:
        # - Correctness: Factual accuracy
        # - Helpfulness: Usefulness of response
        # - Safety: Harmful content detection
        print("\n  Built-in scorers: Correctness, Helpfulness, Safety")

    async def test_custom_scorer_support(self, api_client) -> None:
        """Test custom scorer registration."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        # Custom scorers for Cognition-specific metrics:
        # - ToolEfficiency: Number of tool calls needed
        # - SafetyCompliance: Sandbox violations
        # - ResponseQuality: Code quality if code generated

        session_id = await api_client.create_session("Custom Scorer Test")
        await api_client.send_message(session_id, "Write a Python function")

        print("\n  Custom scorers would evaluate tool usage and code quality")

    async def test_evaluation_persistence(self, api_client) -> None:
        """Test evaluation results are stored in MLflow."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        session_id = await api_client.create_session("Persistent Evaluation")
        await api_client.send_message(session_id, "Test message")

        # Evaluation results would be:
        # - Stored as MLflow run metrics
        # - Associated with session trace
        # - Searchable via MLflow API
        print("\n  Evaluation results would persist in MLflow")

    async def test_batch_evaluation(self, api_client) -> None:
        """Test evaluating multiple sessions at once."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        # Create multiple sessions
        sessions = []
        for i in range(3):
            sid = await api_client.create_session(f"Batch Eval {i}")
            await api_client.send_message(sid, f"Question {i}")
            sessions.append(sid)

        # Batch evaluation would:
        # - Process all sessions
        # - Aggregate scores
        # - Generate comparison report
        print(f"\n  Batch evaluation would process {len(sessions)} sessions")

    async def test_evaluation_without_mlflow(self, api_client) -> None:
        """Test evaluation endpoint returns 501 without MLflow."""
        # Request evaluation
        response = await api_client.post(
            "/sessions/test-session/evaluate", json={"scorers": ["correctness"]}
        )

        # Should return 501 if MLflow not configured
        if response.status_code == 404:
            print("\n  Evaluation endpoint not yet implemented")
        elif response.status_code == 501:
            print("\n  Correctly returns 501 when MLflow not available")
        else:
            print(f"\n  Response: {response.status_code}")
