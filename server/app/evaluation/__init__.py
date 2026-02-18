"""Evaluation module for Cognition.

Provides MLflow evaluation workflows, custom scorers, and human feedback loop.
"""

from server.app.evaluation.workflows import (
    EvaluationService,
    FeedbackEntry,
    FeedbackType,
    ScoreCategory,
    EvaluationScore,
    SessionEvaluation,
    ToolEfficiencyScorer,
    SafetyComplianceScorer,
    ResponseQualityScorer,
    get_evaluation_service,
    reset_evaluation_service,
)

__all__ = [
    "EvaluationService",
    "FeedbackEntry",
    "FeedbackType",
    "ScoreCategory",
    "EvaluationScore",
    "SessionEvaluation",
    "ToolEfficiencyScorer",
    "SafetyComplianceScorer",
    "ResponseQualityScorer",
    "get_evaluation_service",
    "reset_evaluation_service",
]
