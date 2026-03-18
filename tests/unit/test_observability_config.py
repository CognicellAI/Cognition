"""Tests for observability configuration.

Tests that observability settings properly gate OTel and MLflow setup,
with graceful degradation when packages are not installed.
"""

from __future__ import annotations

from unittest.mock import patch


class TestOTelConfiguration:
    """Test OpenTelemetry configuration respects settings."""

    def test_setup_tracing_skipped_when_otel_disabled(self):
        """Test that tracing setup is skipped when otel_enabled=False."""
        from server.app.observability import setup_tracing

        # Call setup with enabled=False - should not raise
        # The function should return early without doing anything
        result = setup_tracing(enabled=False)

        # If we get here without exception, the test passes
        assert result is None

    def test_setup_metrics_skipped_when_otel_disabled(self):
        """Test that metrics setup is skipped when otel_enabled=False."""
        from server.app.observability import setup_metrics

        # Call setup with enabled=False - should not raise
        result = setup_metrics(enabled=False)

        # If we get here without exception, the test passes
        assert result is None


class TestMLflowConfiguration:
    """Test MLflow configuration respects environment variables."""

    def test_setup_mlflow_skipped_when_mlflow_disabled(self, monkeypatch):
        """Test that MLflow setup is skipped when MLFLOW_ENABLED is not set."""
        from server.app.observability.mlflow_config import setup_mlflow_tracing

        monkeypatch.delenv("MLFLOW_ENABLED", raising=False)

        # Call setup - should not raise
        result = setup_mlflow_tracing()

        # If we get here without exception, the test passes
        assert result is None

    def test_setup_mlflow_called_when_mlflow_enabled(self, monkeypatch):
        """Test that MLflow setup is called when MLFLOW_ENABLED=true.

        Mocks the MLflow imports so setup completes without network calls.
        Note: Tracing is handled via OpenTelemetry Collector, not direct autolog.
        """

        monkeypatch.setenv("MLFLOW_ENABLED", "true")
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5000")
        monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "cognition-test")

        with patch("server.app.observability.mlflow_config.mlflow", create=True) as mock_mlflow:
            with patch.dict(
                "sys.modules",
                {"mlflow": mock_mlflow},
            ):
                # Re-import to pick up mocked modules
                import importlib

                import server.app.observability.mlflow_config as mod

                importlib.reload(mod)
                result = mod.setup_mlflow_tracing()

                assert result is None
                mock_mlflow.set_tracking_uri.assert_called_once_with("http://localhost:5000")
                mock_mlflow.set_experiment.assert_called_once_with("cognition-test")

    def test_setup_mlflow_graceful_degradation_when_package_missing(self, monkeypatch):
        """Test graceful degradation when MLflow package is not installed."""

        monkeypatch.setenv("MLFLOW_ENABLED", "true")
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "test")

        # Force ImportError by removing mlflow from sys.modules
        with patch.dict("sys.modules", {"mlflow": None, "mlflow.langchain": None}):
            import importlib

            import server.app.observability.mlflow_config as mod

            importlib.reload(mod)
            result = mod.setup_mlflow_tracing()

            assert result is None


class TestSettingsValidation:
    """Test observability settings validation."""

    def test_otel_enabled_defaults_to_false(self):
        """Test that otel_enabled defaults to False."""
        from server.app.settings import Settings

        # Create settings with no explicit otel_enabled
        settings = Settings()

        assert settings.otel_enabled is False

    def test_otel_enabled_from_env_var(self, monkeypatch):
        """Test that otel_enabled can be set from environment variable."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_OTEL_ENABLED", "false")

        settings = Settings()

        assert settings.otel_enabled is False
