"""MLflow integration for Cognition observability.

Provides MLflow tracing integration with graceful degradation
when MLflow is not installed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from server.app.settings import Settings

logger = structlog.get_logger(__name__)

# Track whether MLflow setup was attempted and successful
_mlflow_setup_attempted = False
_mlflow_available = False


def setup_mlflow_tracing(settings: Settings) -> None:
    """Initialize MLflow tracing for LangChain.

    Configures MLflow to automatically log LangChain traces when enabled.
    Gracefully handles the case where MLflow is not installed.

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
        from mlflow.langchain import autolog

        # Configure tracking URI if provided
        if settings.mlflow_tracking_uri:
            mlflow.set_tracking_uri(settings.mlflow_tracking_uri)
            logger.info(
                "MLflow tracking URI configured",
                uri=settings.mlflow_tracking_uri,
            )

        # Set experiment name
        experiment_name = settings.mlflow_experiment_name or "cognition"
        mlflow.set_experiment(experiment_name)
        logger.info(
            "MLflow experiment configured",
            experiment=experiment_name,
        )

        # Enable LangChain autologging with inline tracing.
        # run_tracer_inline=True runs the tracer callback in the main async
        # task instead of a background thread, which avoids ContextVar
        # propagation failures under uvicorn / asyncio.
        autolog(log_traces=True, run_tracer_inline=True)
        logger.info("MLflow LangChain autologging enabled (inline tracing)")

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
