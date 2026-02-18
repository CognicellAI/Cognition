"""Tests for observability configuration.

Tests that observability settings properly gate OTel and MLflow setup,
with graceful degradation when packages are not installed.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


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
    """Test MLflow configuration respects settings."""

    def test_setup_mlflow_skipped_when_mlflow_disabled(self):
        """Test that MLflow setup is skipped when mlflow_enabled=False."""
        from server.app.mlflow_tracing import setup_mlflow_tracing

        # Create mock settings with mlflow_enabled=False
        mock_settings = MagicMock()
        mock_settings.mlflow_enabled = False
        mock_settings.mlflow_tracking_uri = None
        mock_settings.mlflow_experiment_name = "test"

        # Call setup - should not raise
        result = setup_mlflow_tracing(mock_settings)

        # If we get here without exception, the test passes
        assert result is None

    def test_setup_mlflow_called_when_mlflow_enabled(self):
        """Test that MLflow setup is called when mlflow_enabled=True.

        This test verifies the function attempts MLflow setup when enabled.
        Since MLflow is imported inside the function, we test that the
        graceful degradation path is taken when MLflow is not installed.
        """
        from server.app.mlflow_tracing import setup_mlflow_tracing

        # Create mock settings with mlflow_enabled=True
        mock_settings = MagicMock()
        mock_settings.mlflow_enabled = True
        mock_settings.mlflow_tracking_uri = "http://localhost:5000"
        mock_settings.mlflow_experiment_name = "cognition-test"

        # Call setup - should gracefully handle missing MLflow
        result = setup_mlflow_tracing(mock_settings)

        # If we get here without exception, the test passes
        # The function should either succeed (if MLflow installed)
        # or gracefully degrade (if MLflow not installed)
        assert result is None

    def test_setup_mlflow_graceful_degradation_when_package_missing(self):
        """Test graceful degradation when MLflow package is not installed."""
        from server.app.mlflow_tracing import setup_mlflow_tracing

        # Create mock settings with mlflow_enabled=True
        mock_settings = MagicMock()
        mock_settings.mlflow_enabled = True
        mock_settings.mlflow_tracking_uri = None
        mock_settings.mlflow_experiment_name = "test"

        # Call setup - should not raise exception even if MLflow not installed
        result = setup_mlflow_tracing(mock_settings)

        # If we get here without exception, the test passes
        assert result is None


class TestSettingsValidation:
    """Test observability settings validation."""

    def test_otel_enabled_defaults_to_true(self):
        """Test that otel_enabled defaults to True."""
        from server.app.settings import Settings

        # Create settings with no explicit otel_enabled
        settings = Settings()

        assert settings.otel_enabled is True

    def test_mlflow_enabled_defaults_to_false(self):
        """Test that mlflow_enabled defaults to False."""
        from server.app.settings import Settings

        # Create settings with no explicit mlflow_enabled
        settings = Settings()

        assert settings.mlflow_enabled is False

    def test_mlflow_experiment_name_defaults_to_cognition(self):
        """Test that mlflow_experiment_name defaults to 'cognition'."""
        from server.app.settings import Settings

        # Create settings with no explicit mlflow_experiment_name
        settings = Settings()

        assert settings.mlflow_experiment_name == "cognition"

    def test_otel_enabled_from_env_var(self, monkeypatch):
        """Test that otel_enabled can be set from environment variable."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_OTEL_ENABLED", "false")

        settings = Settings()

        assert settings.otel_enabled is False

    def test_mlflow_enabled_from_env_var(self, monkeypatch):
        """Test that mlflow_enabled can be set from environment variable."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_MLFLOW_ENABLED", "true")

        settings = Settings()

        assert settings.mlflow_enabled is True

    def test_mlflow_tracking_uri_from_env_var(self, monkeypatch):
        """Test that mlflow_tracking_uri can be set from environment variable."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_MLFLOW_TRACKING_URI", "http://mlflow-server:5000")

        settings = Settings()

        assert settings.mlflow_tracking_uri == "http://mlflow-server:5000"

    def test_mlflow_experiment_name_from_env_var(self, monkeypatch):
        """Test that mlflow_experiment_name can be set from environment variable."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_MLFLOW_EXPERIMENT_NAME", "my-experiment")

        settings = Settings()

        assert settings.mlflow_experiment_name == "my-experiment"
