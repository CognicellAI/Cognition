"""MLflow configuration for Cognition.

Provides MLflow experiment setup and configuration.

NOTE: Actual tracing is handled via OpenTelemetry Collector -> MLflow.
This module only handles experiment creation, tracking URI setup,
and MLflow availability checks.

MLflow settings are read from environment variables:
- MLFLOW_ENABLED: "true" to enable (default: disabled)
- MLFLOW_TRACKING_URI: MLflow server URI
- MLFLOW_EXPERIMENT_NAME: Experiment name (default: "cognition")
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger(__name__)

# Track whether MLflow setup was attempted and successful
_mlflow_setup_attempted = False
_mlflow_available = False


def setup_mlflow_tracing() -> None:
    """Initialize MLflow tracing for Cognition.

    Configures MLflow tracking with experiment setup.
    Traces are ingested via OpenTelemetry Collector.

    Configuration is read from environment variables:
    - MLFLOW_ENABLED: "true" to enable
    - MLFLOW_TRACKING_URI: MLflow server URI
    - MLFLOW_EXPERIMENT_NAME: Experiment name (default: "cognition")
    """
    global _mlflow_setup_attempted, _mlflow_available

    _mlflow_setup_attempted = True

    mlflow_enabled = os.getenv("MLFLOW_ENABLED", "false").lower() == "true"
    if not mlflow_enabled:
        logger.debug("MLflow tracing disabled (set MLFLOW_ENABLED=true to enable)")
        return

    try:
        import mlflow

        # Configure tracking URI if provided
        tracking_uri = os.getenv("MLFLOW_TRACKING_URI")
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)
            logger.info(
                "MLflow tracking URI configured",
                uri=tracking_uri,
            )

        # Set experiment name
        experiment_name = os.getenv("MLFLOW_EXPERIMENT_NAME", "cognition")
        experiment = mlflow.set_experiment(experiment_name)
        logger.info(
            "MLflow experiment configured",
            experiment=experiment_name,
            experiment_id=experiment.experiment_id,
        )

        _mlflow_available = True

    except ImportError:
        logger.warning(
            "MLflow not installed, skipping MLflow tracing setup. Install with: pip install mlflow"
        )
        _mlflow_available = False
    except Exception as e:
        logger.error(
            "Failed to initialize MLflow tracing",
            error=str(e),
            error_type=type(e).__name__,
        )
        _mlflow_available = False


def is_mlflow_available() -> bool:
    """Check if MLflow tracing was successfully initialized.

    Returns:
        True if MLflow is available and initialized, False otherwise
    """
    return _mlflow_available


def is_mlflow_setup_attempted() -> bool:
    """Check if MLflow setup was attempted.

    Returns:
        True if setup_mlflow_tracing was called, False otherwise
    """
    return _mlflow_setup_attempted


def get_current_experiment_id() -> str | None:
    """Get the current MLflow experiment ID.

    Returns:
        Experiment ID string or None if MLflow not available
    """
    if not _mlflow_available:
        return None

    try:
        import mlflow

        experiment = mlflow.get_experiment_by_name("cognition")
        return experiment.experiment_id if experiment else None
    except Exception as e:
        logger.debug("Failed to get experiment ID", error=str(e))
        return None
