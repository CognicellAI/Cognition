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
        """Test that MLflow setup is skipped when MLflow is not enabled."""
        from server.app.observability.mlflow_config import setup_mlflow_tracing

        monkeypatch.delenv("COGNITION_MLFLOW_ENABLED", raising=False)
        monkeypatch.delenv("MLFLOW_ENABLED", raising=False)

        # Call setup - should not raise
        result = setup_mlflow_tracing()

        # If we get here without exception, the test passes
        assert result is None

    def test_setup_mlflow_called_when_mlflow_enabled(self, monkeypatch):
        """Test that MLflow setup is called when COGNITION_MLFLOW_ENABLED=true.

        Mocks the MLflow imports so setup completes without network calls.
        Note: Tracing is handled via OpenTelemetry Collector, not direct autolog.
        """

        monkeypatch.setenv("COGNITION_MLFLOW_ENABLED", "true")
        monkeypatch.setenv("COGNITION_MLFLOW_TRACKING_URI", "http://localhost:5000")
        monkeypatch.setenv("COGNITION_MLFLOW_EXPERIMENT_NAME", "cognition-test")

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
                assert mod.is_mlflow_autolog_enabled() is False

    def test_setup_mlflow_accepts_legacy_mlflow_env_aliases(self, monkeypatch):
        """Test that plain MLFLOW_* aliases still work when Cognition names are unset."""

        monkeypatch.delenv("COGNITION_MLFLOW_ENABLED", raising=False)
        monkeypatch.delenv("COGNITION_MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.delenv("COGNITION_MLFLOW_EXPERIMENT_NAME", raising=False)
        monkeypatch.setenv("MLFLOW_ENABLED", "true")
        monkeypatch.setenv("MLFLOW_TRACKING_URI", "http://localhost:5001")
        monkeypatch.setenv("MLFLOW_EXPERIMENT_NAME", "legacy-test")

        with patch("server.app.observability.mlflow_config.mlflow", create=True) as mock_mlflow:
            with patch.dict(
                "sys.modules",
                {"mlflow": mock_mlflow},
            ):
                import importlib

                import server.app.observability.mlflow_config as mod

                importlib.reload(mod)
                result = mod.setup_mlflow_tracing()

                assert result is None
                mock_mlflow.set_tracking_uri.assert_called_once_with("http://localhost:5001")
                mock_mlflow.set_experiment.assert_called_once_with("legacy-test")

    def test_setup_mlflow_otlp_mode_enables_mlflow_config_without_autolog(self, monkeypatch):
        """Test OTLP-to-MLflow mode configures MLflow without native autolog."""

        monkeypatch.delenv("COGNITION_MLFLOW_ENABLED", raising=False)
        monkeypatch.setenv("COGNITION_NATIVE_AGENT_TRACING", "otlp_to_mlflow")
        monkeypatch.setenv("COGNITION_MLFLOW_TRACKING_URI", "http://localhost:5000")

        with patch("server.app.observability.mlflow_config.mlflow", create=True) as mock_mlflow:
            with patch.dict(
                "sys.modules",
                {"mlflow": mock_mlflow},
            ):
                import importlib

                import server.app.observability.mlflow_config as mod

                importlib.reload(mod)
                result = mod.setup_mlflow_tracing()

                assert result is None
                mock_mlflow.set_tracking_uri.assert_called_once_with("http://localhost:5000")
                mock_mlflow.set_experiment.assert_called_once_with("cognition")
                assert mod.is_mlflow_autolog_enabled() is False

    def test_setup_mlflow_autolog_mode_enables_langchain_autolog(self, monkeypatch):
        """Test native MLflow mode enables LangChain autolog explicitly."""

        monkeypatch.setenv("COGNITION_NATIVE_AGENT_TRACING", "mlflow_autolog")
        monkeypatch.setenv("COGNITION_MLFLOW_EXPERIMENT_NAME", "native-test")
        autolog_enabled = False

        with patch("server.app.observability.mlflow_config.mlflow", create=True) as mock_mlflow:
            with patch.dict(
                "sys.modules",
                {"mlflow": mock_mlflow},
            ):
                import importlib

                import server.app.observability.mlflow_config as mod

                importlib.reload(mod)

                def enable_autolog() -> None:
                    nonlocal autolog_enabled
                    autolog_enabled = True

                monkeypatch.setattr(mod, "_enable_mlflow_langchain_autolog", enable_autolog)
                result = mod.setup_mlflow_tracing()

                assert result is None
                mock_mlflow.set_experiment.assert_called_once_with("native-test")
                assert autolog_enabled is True
                assert mod.is_mlflow_autolog_enabled() is True

    def test_setup_mlflow_graceful_degradation_when_package_missing(self, monkeypatch):
        """Test graceful degradation when MLflow package is not installed."""

        monkeypatch.setenv("COGNITION_MLFLOW_ENABLED", "true")
        monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
        monkeypatch.setenv("COGNITION_MLFLOW_TRACKING_URI", "")
        monkeypatch.setenv("COGNITION_MLFLOW_EXPERIMENT_NAME", "test")

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

    def test_tracing_enabled_prefers_new_env_name(self, monkeypatch):
        """Test preferred tracing switch overrides the compatibility alias."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_OTEL_ENABLED", "false")
        monkeypatch.setenv("COGNITION_TRACING_ENABLED", "true")

        settings = Settings()

        assert settings.otel_enabled is True

    def test_metrics_enabled_defaults_to_true(self):
        """Test that Prometheus metrics are enabled independently by default."""
        from server.app.settings import Settings

        settings = Settings()

        assert settings.metrics_enabled is True

    def test_metrics_enabled_from_env_var(self, monkeypatch):
        """Test that metrics can be disabled without changing tracing."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_METRICS_ENABLED", "false")
        monkeypatch.setenv("COGNITION_OTEL_ENABLED", "true")

        settings = Settings()

        assert settings.metrics_enabled is False
        assert settings.otel_enabled is True

    def test_log_format_from_env_var(self, monkeypatch):
        """Test structured log format configuration."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_LOG_FORMAT", "console")

        settings = Settings()

        assert settings.log_format == "console"

    def test_native_agent_tracing_from_env_var(self, monkeypatch):
        """Test native semantic tracing mode configuration."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_NATIVE_AGENT_TRACING", "mlflow_autolog")

        settings = Settings()

        assert settings.native_agent_tracing == "mlflow_autolog"

    def test_otlp_max_export_bytes_prefers_new_env_name(self, monkeypatch):
        """Test the preferred OTLP byte-limit setting."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_OTEL_MAX_EXPORT_BYTES", "100000")
        monkeypatch.setenv("COGNITION_OTLP_MAX_EXPORT_BYTES", "200000")

        settings = Settings()

        assert settings.otel_max_export_bytes == 200000

    def test_otlp_max_export_bytes_accepts_legacy_otel_name(self, monkeypatch):
        """Test backward compatibility for the old OTEL byte-limit setting."""
        from server.app.settings import Settings

        monkeypatch.delenv("COGNITION_OTLP_MAX_EXPORT_BYTES", raising=False)
        monkeypatch.setenv("COGNITION_OTEL_MAX_EXPORT_BYTES", "150000")

        settings = Settings()

        assert settings.otel_max_export_bytes == 150000

    def test_otlp_queue_timeout_and_trace_sampling_from_env(self, monkeypatch):
        """Test bounded OTLP queue, timeout, and root sampling settings."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_OTLP_QUEUE_SIZE", "128")
        monkeypatch.setenv("COGNITION_OTLP_EXPORT_TIMEOUT_MS", "2500")
        monkeypatch.setenv("COGNITION_TRACE_SAMPLE_RATIO", "0.25")

        settings = Settings()

        assert settings.otlp_queue_size == 128
        assert settings.otlp_export_timeout_ms == 2500
        assert settings.trace_sample_ratio == 0.25
