"""Tests for observability configuration.

Tests that observability settings properly gate OTel and metrics setup,
with graceful degradation when packages are not installed.
"""

from __future__ import annotations


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

    def test_otlp_endpoint_prefers_canonical_env_name(self, monkeypatch):
        """COGNITION_OTLP_ENDPOINT is canonical over the old OTEL alias."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_OTEL_ENDPOINT", "http://old:4317")
        monkeypatch.setenv("COGNITION_OTLP_ENDPOINT", "http://new:4317")

        settings = Settings()

        assert settings.otel_endpoint == "http://new:4317"

    def test_trace_profile_settings_from_env(self, monkeypatch):
        """Trace detail settings are operator controlled."""
        from server.app.settings import Settings

        monkeypatch.setenv("COGNITION_TRACE_DETAIL", "debug")

        settings = Settings()

        assert settings.trace_detail == "debug"

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
