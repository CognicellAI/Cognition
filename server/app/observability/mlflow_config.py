"""MLflow configuration for Cognition.

Provides MLflow experiment setup and configuration.

NOTE: Actual tracing is handled via OpenTelemetry Collector -> MLflow.
By default this module handles experiment creation, tracking URI setup, and
MLflow availability checks. Native LangChain/Deep Agents autolog tracing is
enabled only when the operator selects COGNITION_NATIVE_AGENT_TRACING=mlflow_autolog.

MLflow settings are read from Cognition operator environment variables:
- COGNITION_MLFLOW_ENABLED: "true" to enable (default: disabled)
- COGNITION_MLFLOW_TRACKING_URI: MLflow server URI
- COGNITION_MLFLOW_EXPERIMENT_NAME: Experiment name (default: "cognition")
- COGNITION_NATIVE_AGENT_TRACING: disabled | langsmith_otel | mlflow_autolog | otlp_to_mlflow

The plain upstream MLFLOW_* names remain supported as compatibility aliases
when the Cognition-prefixed setting is unset.
"""

from __future__ import annotations

import os

import structlog

logger = structlog.get_logger(__name__)

# Track whether MLflow setup was attempted and successful
_mlflow_setup_attempted = False
_mlflow_available = False
_mlflow_autolog_enabled = False
_mlflow_experiment_name = "cognition"
_NATIVE_TRACING_MODES = {"disabled", "langsmith_otel", "mlflow_autolog", "otlp_to_mlflow"}


def _env_value(cognition_name: str, upstream_name: str, default: str | None = None) -> str | None:
    """Return a Cognition-prefixed setting with upstream MLflow fallback."""
    value = os.getenv(cognition_name)
    if value is not None:
        return value
    return os.getenv(upstream_name, default)


def _env_bool(cognition_name: str, upstream_name: str, default: bool = False) -> bool:
    """Return a boolean Cognition-prefixed setting with upstream MLflow fallback."""
    value = _env_value(cognition_name, upstream_name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _native_agent_tracing_mode() -> str:
    mode = os.getenv("COGNITION_NATIVE_AGENT_TRACING", "disabled").strip().lower()
    if mode not in _NATIVE_TRACING_MODES:
        logger.warning(
            "Unsupported native agent tracing mode; disabling native tracing",
            mode=mode,
        )
        return "disabled"
    return mode


def _enable_mlflow_langchain_autolog() -> None:
    """Enable MLflow's native LangChain/Deep Agents trace autologging."""
    import mlflow.langchain as mlflow_langchain

    mlflow_langchain.autolog(
        disable=False,
        exclusive=False,
        disable_for_unsupported_versions=True,
        silent=True,
        log_traces=True,
        run_tracer_inline=False,
    )


def setup_mlflow_tracing() -> None:
    """Initialize MLflow tracing for Cognition.

    Configures MLflow tracking with experiment setup.
    Traces are ingested via OpenTelemetry Collector.

    Configuration is read from environment variables:
    - COGNITION_MLFLOW_ENABLED: "true" to enable
    - COGNITION_MLFLOW_TRACKING_URI: MLflow server URI
    - COGNITION_MLFLOW_EXPERIMENT_NAME: Experiment name (default: "cognition")
    - COGNITION_NATIVE_AGENT_TRACING=mlflow_autolog: enable native MLflow LangChain tracing

    MLFLOW_ENABLED, MLFLOW_TRACKING_URI, and MLFLOW_EXPERIMENT_NAME are accepted
    as compatibility aliases when the Cognition-prefixed setting is unset.
    """
    global _mlflow_setup_attempted, _mlflow_available, _mlflow_autolog_enabled
    global _mlflow_experiment_name

    _mlflow_setup_attempted = True

    native_mode = _native_agent_tracing_mode()
    mlflow_enabled = _env_bool("COGNITION_MLFLOW_ENABLED", "MLFLOW_ENABLED")
    if native_mode in {"mlflow_autolog", "otlp_to_mlflow"}:
        mlflow_enabled = True
    if not mlflow_enabled:
        logger.debug("MLflow tracing disabled (set COGNITION_MLFLOW_ENABLED=true to enable)")
        return

    try:
        import mlflow

        # Configure tracking URI if provided
        tracking_uri = _env_value("COGNITION_MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_URI")
        if tracking_uri:
            mlflow.set_tracking_uri(tracking_uri)
            logger.info(
                "MLflow tracking URI configured",
                uri=tracking_uri,
            )

        # Set experiment name
        experiment_name = (
            _env_value(
                "COGNITION_MLFLOW_EXPERIMENT_NAME",
                "MLFLOW_EXPERIMENT_NAME",
                "cognition",
            )
            or "cognition"
        )
        _mlflow_experiment_name = experiment_name
        experiment = mlflow.set_experiment(experiment_name)
        logger.info(
            "MLflow experiment configured",
            experiment=experiment_name,
            experiment_id=experiment.experiment_id,
            native_agent_tracing=native_mode,
        )

        if native_mode == "mlflow_autolog":
            _enable_mlflow_langchain_autolog()
            _mlflow_autolog_enabled = True
            logger.info(
                "MLflow LangChain autolog tracing enabled",
                native_agent_tracing=native_mode,
            )
        else:
            _mlflow_autolog_enabled = False

        _mlflow_available = True

    except ImportError:
        logger.warning(
            "MLflow not installed, skipping MLflow tracing setup. Install with: pip install mlflow"
        )
        _mlflow_available = False
        _mlflow_autolog_enabled = False
    except Exception as e:
        logger.error(
            "Failed to initialize MLflow tracing",
            error_type=type(e).__name__,
        )
        _mlflow_available = False
        _mlflow_autolog_enabled = False


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


def is_mlflow_autolog_enabled() -> bool:
    """Check if native MLflow LangChain autolog tracing was enabled."""
    return _mlflow_autolog_enabled


def get_current_experiment_id() -> str | None:
    """Get the current MLflow experiment ID.

    Returns:
        Experiment ID string or None if MLflow not available
    """
    if not _mlflow_available:
        return None

    try:
        import mlflow

        experiment = mlflow.get_experiment_by_name(_mlflow_experiment_name)
        return experiment.experiment_id if experiment else None
    except Exception as e:
        logger.debug("Failed to get experiment ID", error=str(e))
        return None
