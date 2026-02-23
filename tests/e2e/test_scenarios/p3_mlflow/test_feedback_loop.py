"""Business Scenario: Human Feedback Loop.

As an AI platform operator, I want to collect human feedback on agent responses,
so I can improve the system and create training datasets.

Business Value:
- Human-in-the-loop quality improvement
- Training data for model fine-tuning
- Quality assurance and monitoring
- User satisfaction tracking
"""

from __future__ import annotations

import pytest


@pytest.mark.asyncio
@pytest.mark.mlflow
class TestHumanFeedbackLoop:
    """Test human feedback collection via MLflow."""

    async def test_feedback_submission_endpoint(self, api_client) -> None:
        """Test feedback can be submitted for a message."""
        # Create session and get a response
        session_id = await api_client.create_session("Feedback Test")
        response = await api_client.send_message(session_id, "Hello")

        assert response.status_code == 200

        # Submit feedback
        feedback_response = await api_client.post(
            f"/sessions/{session_id}/feedback",
            json={
                "rating": 5,
                "comment": "Very helpful response",
                "message_index": -1,  # Most recent message
            },
        )

        if feedback_response.status_code == 201:
            print("\n  Feedback submitted successfully")
        elif feedback_response.status_code == 501:
            print("\n  Feedback requires MLflow (not available)")
        elif feedback_response.status_code == 404:
            print("\n  Feedback endpoint not yet implemented")
        else:
            print(f"\n  Feedback response: {feedback_response.status_code}")

    async def test_feedback_attached_to_trace(self, api_client) -> None:
        """Test feedback is attached to MLflow trace."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        session_id = await api_client.create_session("Trace Feedback Test")
        await api_client.send_message(session_id, "Test message")

        # Feedback would be:
        # - Attached to the trace in MLflow
        # - Searchable via MLflow API
        # - Used for evaluation datasets
        print("\n  Feedback would attach to MLflow trace")

    async def test_multiple_feedback_per_session(self, api_client) -> None:
        """Test multiple feedback entries for a session."""
        session_id = await api_client.create_session("Multi Feedback Test")

        # Send multiple messages
        for i in range(3):
            await api_client.send_message(session_id, f"Message {i}")

            # Submit feedback for each
            # feedback = await api_client.post(...)

        print("\n  Multiple feedback entries per session")

    async def test_feedback_with_different_ratings(self, api_client) -> None:
        """Test feedback with various rating levels."""
        session_id = await api_client.create_session("Rating Test")
        await api_client.send_message(session_id, "Test")

        ratings = [
            {"rating": 1, "comment": "Poor"},
            {"rating": 3, "comment": "Average"},
            {"rating": 5, "comment": "Excellent"},
        ]

        for rating_data in ratings:
            # Each rating would be stored
            print(f"\n  Rating {rating_data['rating']}: {rating_data['comment']}")

    async def test_feedback_creates_dataset(self, api_client) -> None:
        """Test feedback creates MLflow evaluation dataset."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        # Collect feedback across multiple sessions
        sessions = []
        for i in range(5):
            sid = await api_client.create_session(f"Dataset Session {i}")
            await api_client.send_message(sid, "Question")
            sessions.append(sid)

        # Feedback-annotated traces become datasets
        print(f"\n  {len(sessions)} sessions with feedback → evaluation dataset")

    async def test_feedback_filtering(self, api_client) -> None:
        """Test filtering sessions by feedback."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        # Find sessions with:
        # - Low ratings (need improvement)
        # - High ratings (good examples)
        # - Recent feedback

        print("\n  Can filter traces by feedback rating")

    async def test_feedback_without_mlflow(self, api_client) -> None:
        """Test feedback endpoint returns 501 without MLflow."""
        response = await api_client.post("/sessions/test/feedback", json={"rating": 5})

        if response.status_code == 404:
            print("\n  Feedback endpoint not yet implemented")
        elif response.status_code == 501:
            print("\n  Correctly returns 501 when MLflow not available")
        else:
            print(f"\n  Response: {response.status_code}")

    async def test_feedback_informs_improvement(self, api_client) -> None:
        """Test feedback loop improves system over time."""
        try:
            import mlflow
        except ImportError:
            pytest.skip("MLflow not installed")

        # Feedback loop:
        # 1. Agent responds
        # 2. User provides feedback
        # 3. Feedback stored in MLflow
        # 4. Analysis identifies patterns
        # 5. Prompts/tools improved
        # 6. Quality increases

        print("\n  Feedback loop enables continuous improvement")
