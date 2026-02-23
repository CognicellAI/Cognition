"""MLflow configuration for Cognition.

Provides MLflow experiment setup and configuration.

NOTE: Actual tracing is handled via OpenTelemetry Collector → MLflow.
This module only handles experiment creation, tracking URI setup,
and MLflow availability checks.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import structlog

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = structlog.get_logger(__name__)

# Track whether MLflow setup was attempted and successful
_mlflow_setup_attempted = False
_mlflow_available = False


def setup_mlflow_tracing(settings: Settings) -> None:
    """Initialize MLflow tracing for Cognition.

    Configures MLflow tracking with experiment setup.
    Traces are ingested via OpenTelemetry Collector.

    Args:
        settings: Application settings containing MLflow configuration

    Example:
        >>> from server.app.settings import get_settings
        >>> settings = get_settings()
        >>> setup_mlflow_tracing(settings)
    """
    global _mlflow_setup_attempted, _mlflow_available

    _mlflow_setup_attempted = True

    if not settings.mlflow_enabled:
        logger.debug("MLflow tracing disabled by settings")
        return

    try:
        import mlflow

        # Configure tracking URI if provided
        if settings.mlflow_tracking_uri:
            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            logger.info(
                "MLflow tracking URI configured",
                uri=settings.mlflow_tracking_uri,
            )

        # Set experiment name
        experiment_name = settings.mlflow_experiment_name or "cognition"
        experiment = mlflow.set_experiment(experiment_name)
        logger.info(
            "MLflow experiment configured",
            experiment=experiment_name,
            experiment_id=experiment.experiment_id,
        )

        logger.info("MLflow experiment configured for evaluation")

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
