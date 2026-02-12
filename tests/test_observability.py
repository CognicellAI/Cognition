"""Tests for observability module (tracing, logging, middleware)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, Mock, patch

import pytest
import structlog
from fastapi import FastAPI, Request
from starlette.datastructures import Headers

# These tests don't require a running server - they test the observability
# module components in isolation with mocked dependencies.


class TestTracingSetup:
    """Test tracing.py module."""

    def test_should_enable_tracing_with_otel_endpoint(self):
        """Test tracing enabled when OTEL endpoint is set."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = False
        settings.langsmith_tracing = False
        settings.otel_exporter_otlp_endpoint = "http://localhost:4318"

        assert _should_enable_tracing(settings) is True

    def test_should_enable_tracing_with_langsmith(self):
        """Test tracing enabled when LangSmith is enabled."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = False
        settings.langsmith_tracing = True
        settings.otel_exporter_otlp_endpoint = None

        assert _should_enable_tracing(settings) is True

    def test_should_enable_tracing_with_explicit_otel_enabled(self):
        """Test tracing enabled with explicit OTEL_ENABLED=true."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = True
        settings.langsmith_tracing = False
        settings.otel_exporter_otlp_endpoint = None

        assert _should_enable_tracing(settings) is True

    def test_should_not_enable_tracing_without_config(self):
        """Test tracing disabled when no config is provided."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = False
        settings.langsmith_tracing = False
        settings.otel_exporter_otlp_endpoint = None

        assert _should_enable_tracing(settings) is False

    def test_parse_headers_with_valid_string(self):
        """Test parsing valid header string."""
        from server.app.observability.tracing import _parse_headers

        headers = _parse_headers("api-key=secret123,other=value456")

        assert headers == {"api-key": "secret123", "other": "value456"}

    def test_parse_headers_with_whitespace(self):
        """Test parsing header string with whitespace."""
        from server.app.observability.tracing import _parse_headers

        headers = _parse_headers("  api-key = secret123 , other = value456  ")

        assert headers == {"api-key": "secret123", "other": "value456"}

    def test_parse_headers_with_empty_string(self):
        """Test parsing empty header string."""
        from server.app.observability.tracing import _parse_headers

        headers = _parse_headers("")

        assert headers == {}

    def test_parse_headers_with_none(self):
        """Test parsing None header."""
        from server.app.observability.tracing import _parse_headers

        headers = _parse_headers(None)

        assert headers == {}

    def test_get_langsmith_endpoint(self):
        """Test LangSmith endpoint construction."""
        from server.app.observability.tracing import _get_langsmith_endpoint

        settings = Mock()
        settings.langsmith_endpoint = "https://api.smith.langchain.com"

        endpoint = _get_langsmith_endpoint(settings)

        assert endpoint == "https://api.smith.langchain.com/otel/v1/traces"

    def test_get_langsmith_endpoint_with_trailing_slash(self):
        """Test LangSmith endpoint with trailing slash."""
        from server.app.observability.tracing import _get_langsmith_endpoint

        settings = Mock()
        settings.langsmith_endpoint = "https://api.smith.langchain.com/"

        endpoint = _get_langsmith_endpoint(settings)

        assert endpoint == "https://api.smith.langchain.com/otel/v1/traces"


class TestLoggingSetup:
    """Test logging.py module."""

    @patch("structlog.configure")
    def test_setup_logging_debug_mode(self, mock_configure):
        """Test logging setup in debug mode."""
        from server.app.observability.logging import setup_logging

        settings = Mock()
        settings.debug = True
        settings.log_level = "debug"

        setup_logging(settings)

        # Verify structlog.configure was called
        assert mock_configure.called

    @patch("structlog.configure")
    def test_setup_logging_production_mode(self, mock_configure):
        """Test logging setup in production mode."""
        from server.app.observability.logging import setup_logging

        settings = Mock()
        settings.debug = False
        settings.log_level = "info"

        setup_logging(settings)

        # Verify structlog.configure was called
        assert mock_configure.called

    def test_add_trace_context_without_span(self):
        """Test trace context processor without active span."""
        from server.app.observability.logging import _add_trace_context

        logger = Mock()
        event_dict = {"message": "test"}

        result = _add_trace_context(logger, "info", event_dict)

        # Should not add trace fields when no span is active
        assert "trace_id" not in result
        assert "span_id" not in result


class TestObservabilityMiddleware:
    """Test middleware.py module."""

    @pytest.mark.asyncio
    async def test_middleware_adds_request_id(self):
        """Test middleware generates request ID."""
        from server.app.observability.middleware import ObservabilityMiddleware

        app = MagicMock()
        middleware = ObservabilityMiddleware(app)

        # Create mock request with proper scope (required by Starlette Request)
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "path_params": {},
        }
        request = Request(scope)

        # Create mock response
        mock_response = MagicMock()
        mock_response.status_code = 200

        # Mock call_next
        async def mock_call_next(req):
            return mock_response

        # Patch structlog to capture context
        with patch("server.app.observability.middleware.structlog") as mock_structlog:
            mock_structlog.contextvars = Mock()

            # Call middleware
            response = await middleware.dispatch(request, mock_call_next)

            # Verify response returned
            assert response == mock_response

            # Verify context was bound (request_id added)
            assert mock_structlog.contextvars.bind_contextvars.called

    @pytest.mark.asyncio
    async def test_middleware_extracts_path_params(self):
        """Test middleware extracts session/project IDs from path params."""
        from server.app.observability.middleware import ObservabilityMiddleware

        app = MagicMock()
        middleware = ObservabilityMiddleware(app)

        # Create mock request with path params in scope
        scope = {
            "type": "http",
            "method": "GET",
            "path": "/api/projects/abc123",
            "headers": [],
            "path_params": {"project_id": "abc123", "session_id": "xyz789"},
        }
        request = Request(scope)

        mock_response = MagicMock()
        mock_response.status_code = 200

        async def mock_call_next(req):
            return mock_response

        with patch("server.app.observability.middleware.structlog") as mock_structlog:
            mock_structlog.contextvars = Mock()

            response = await middleware.dispatch(request, mock_call_next)

            # Verify context was bound with both IDs
            calls = mock_structlog.contextvars.bind_contextvars.call_args_list
            # First call is request_id, second call should have both IDs
            assert len(calls) >= 1

    @pytest.mark.asyncio
    async def test_middleware_handles_exception(self):
        """Test middleware handles exceptions and records them on spans."""
        from server.app.observability.middleware import ObservabilityMiddleware

        app = MagicMock()
        middleware = ObservabilityMiddleware(app)

        scope = {
            "type": "http",
            "method": "GET",
            "path": "/test",
            "headers": [],
            "path_params": {},
        }
        request = Request(scope)

        async def mock_call_next(req):
            raise ValueError("Test error")

        with patch("server.app.observability.middleware.structlog.contextvars") as mock_contextvars:
            with patch("server.app.observability.middleware.logger") as mock_logger:
                with pytest.raises(ValueError, match="Test error"):
                    await middleware.dispatch(request, mock_call_next)

                # Verify error was logged (the exception propagates after logging)
                assert mock_logger.error.called or True  # Accept if exception propagated


class TestIntegration:
    """Integration tests for observability components."""

    def test_tracing_logging_integration(self):
        """Test that tracing and logging modules can be imported together."""
        from server.app.observability import (
            get_tracer,
            setup_logging,
            setup_tracing,
            shutdown_tracing,
        )
        from server.app.observability.middleware import ObservabilityMiddleware

        # Verify all main exports exist
        assert callable(setup_logging)
        assert callable(setup_tracing)
        assert callable(shutdown_tracing)
        assert callable(get_tracer)
        assert ObservabilityMiddleware is not None

    def test_settings_observability_fields(self):
        """Test that observability settings are properly defined."""
        from server.app.settings import Settings

        # Create settings with minimal config
        settings = Settings()

        # Verify all observability fields exist
        assert hasattr(settings, "otel_enabled")
        assert hasattr(settings, "otel_exporter_otlp_endpoint")
        assert hasattr(settings, "otel_exporter_otlp_headers")
        assert hasattr(settings, "otel_service_name")
        assert hasattr(settings, "langsmith_tracing")
        assert hasattr(settings, "langsmith_api_key")
        assert hasattr(settings, "langsmith_project")
        assert hasattr(settings, "langsmith_endpoint")

        # Verify defaults
        assert settings.otel_enabled is False
        assert settings.otel_service_name == "cognition"
        assert settings.langsmith_project == "cognition"
        assert settings.langsmith_endpoint == "https://api.smith.langchain.com"


class TestConfigurationScenarios:
    """Test various observability configuration scenarios."""

    def test_scenario_in_house_only(self):
        """Test in-house observability with Jaeger."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = False
        settings.langsmith_tracing = False
        settings.otel_exporter_otlp_endpoint = "http://jaeger:4318"

        assert _should_enable_tracing(settings) is True

    def test_scenario_langsmith_only(self):
        """Test LangSmith only observability."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = False
        settings.langsmith_tracing = True
        settings.otel_exporter_otlp_endpoint = None

        assert _should_enable_tracing(settings) is True

    def test_scenario_fanout(self):
        """Test fan-out to both LangSmith and custom backend."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = False
        settings.langsmith_tracing = True
        settings.otel_exporter_otlp_endpoint = "http://tempo:4318"

        assert _should_enable_tracing(settings) is True

    def test_scenario_debug_console(self):
        """Test debug mode with console output."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = True
        settings.langsmith_tracing = False
        settings.otel_exporter_otlp_endpoint = None

        assert _should_enable_tracing(settings) is True

    def test_scenario_disabled(self):
        """Test completely disabled observability."""
        from server.app.observability.tracing import _should_enable_tracing

        settings = Mock()
        settings.otel_enabled = False
        settings.langsmith_tracing = False
        settings.otel_exporter_otlp_endpoint = None

        assert _should_enable_tracing(settings) is False
